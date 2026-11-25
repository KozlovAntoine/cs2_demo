"""History of the generated compilations: list, thumbnails, post text, platform links and deletion."""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from compilation_video import COMPILATION_FOLDER, DESCRIPTION_MAX, META_VERSION, ffmpeg_tools, probe, write_meta
from utility_video import run


THUMBNAIL_FOLDER = ".thumbs"
PLATFORMS = {"youtube": ("youtube.com", "youtu.be"), "tiktok": ("tiktok.com",), "instagram": ("instagram.com",)}
PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
CLIP_COUNT = re.compile(r" (\d+) clips? ")


def compilation_folder(output):
    return Path(output) / COMPILATION_FOLDER


def compilation_video(output, name):
    """The video of a compilation by its bare name: in its own folder, or loose in the compilations folder."""
    if not isinstance(name, str) or not name.endswith(".mp4") or Path(name).name != name or name.startswith("."):
        raise ValueError("Compilation introuvable.")
    folder = compilation_folder(output).resolve()
    for video in (folder / Path(name).stem / name, folder / name):
        video = video.resolve()
        if video.parent in (folder, folder / Path(name).stem) and video.is_file():
            return video
    raise ValueError("Compilation introuvable.")


def sidecars(video):
    return video.with_suffix(".json"), video.with_suffix(".txt")


def move_into_folders(folder):
    """Compilations made before the folders existed: each video and its files move into a folder of its name."""
    for video in folder.glob("*.mp4"):
        target = folder / video.stem
        if target.exists():
            continue
        try:
            target.mkdir()
            video.replace(target / video.name)
            for file in sidecars(video):
                if file.is_file():
                    file.replace(target / file.name)
        except OSError:
            # The video may be open in a player: it stays where it is and is moved on a later visit.
            if not (target / video.name).exists():
                try:
                    target.rmdir()
                except OSError:
                    pass


def compilation_videos(folder):
    for directory in folder.iterdir():
        if directory.is_dir() and not directory.name.startswith("."):
            video = directory / f"{directory.name}.mp4"
            if video.is_file():
                yield video
    yield from folder.glob("*.mp4")


def read_meta(video):
    try:
        meta = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def legacy_description(video):
    try:
        return video.with_suffix(".txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def entry(video, meta):
    stat = video.stat()
    clips = meta.get("clips") if isinstance(meta.get("clips"), list) else []
    count = meta.get("clipCount")
    if not isinstance(count, int):
        found = CLIP_COUNT.search(video.stem)
        count = int(found[1]) if found else len(clips) or None
    links = meta.get("links") if isinstance(meta.get("links"), dict) else {}
    created = meta.get("createdAt") or datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
    description = meta.get("description")
    return {
        "file": video.name, "size": stat.st_size, "createdAt": created, "duration": meta.get("duration"),
        "clipCount": count, "clips": clips,
        "description": description if isinstance(description, str) else legacy_description(video),
        "links": {platform: links[platform] for platform in PLATFORMS if isinstance(links.get(platform), str)},
    }


def list_compilations(output):
    folder = compilation_folder(output)
    if not folder.is_dir():
        return []
    move_into_folders(folder)
    ffprobe = None
    items = []
    for video in compilation_videos(folder):
        meta = read_meta(video)
        if not isinstance(meta.get("duration"), (int, float)):
            # Compilations made before the history existed: measure them once and keep the result.
            try:
                ffprobe = ffprobe or ffmpeg_tools()[1]
                meta = {"version": META_VERSION, **meta, "duration": round(probe(ffprobe, video)[0], 2)}
                meta.setdefault("description", legacy_description(video))
                write_meta(video, meta)
            except (ValueError, OSError, subprocess.SubprocessError):
                pass
        items.append(entry(video, meta))
    return sorted(items, key=lambda item: item["createdAt"], reverse=True)


def compilation_thumbnail(output, stem):
    """A 9:16 poster taken 2.5 s into the video, once the titles are written, made the first time it is asked for."""
    video = compilation_video(output, f"{stem}.mp4")
    # Kept apart so that each compilation folder only holds what is worth opening.
    folder = compilation_folder(output) / THUMBNAIL_FOLDER
    thumbnail = folder / f"{video.stem}.jpg"
    if thumbnail.is_file() and thumbnail.stat().st_mtime >= video.stat().st_mtime:
        return thumbnail
    folder.mkdir(exist_ok=True)
    ffmpeg = ffmpeg_tools()[0]
    run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", "2.5", "-i", video,
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", thumbnail], capture_output=True, encoding="utf-8")
    if not thumbnail.is_file():
        raise ValueError("Miniature indisponible.")
    return thumbnail


def clean_link(platform, value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if len(value) > 300:
        raise ValueError("Lien trop long.")
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == domain or host.endswith(f".{domain}") for domain in PLATFORMS[platform]):
        raise ValueError(f"Le lien {PLATFORM_NAMES[platform]} doit commencer par https:// et pointer vers {PLATFORMS[platform][0]}.")
    return value


def update_compilation(output, data):
    video = compilation_video(output, data.get("file"))
    meta = read_meta(video) or {"version": META_VERSION}
    if isinstance(data.get("links"), dict):
        links = dict(meta.get("links") or {})
        for platform in PLATFORMS:
            if platform in data["links"]:
                link = clean_link(platform, data["links"][platform])
                if link:
                    links[platform] = link
                else:
                    links.pop(platform, None)
        meta["links"] = links
    if isinstance(data.get("description"), str):
        description = data["description"].strip()[:DESCRIPTION_MAX]
        meta["description"] = description
        text = video.with_suffix(".txt")
        if description:
            text.write_text(description + "\n", encoding="utf-8")
        else:
            text.unlink(missing_ok=True)
    write_meta(video, meta)
    return entry(video, meta)


def record_upload(output, name, platform, result):
    """Keeps what a platform returned after an upload, and its link for the history."""
    video = compilation_video(output, name)
    meta = read_meta(video) or {"version": META_VERSION}
    meta["links"] = {**(meta.get("links") or {}), platform: result["url"]}
    meta["uploads"] = {**(meta.get("uploads") or {}), platform: result}
    write_meta(video, meta)
    return entry(video, meta)


def post_text(output, name):
    """Title and description for a platform: the first line of the post text, then the rest."""
    video = compilation_video(output, name)
    meta = read_meta(video)
    text = meta.get("description") if isinstance(meta.get("description"), str) else legacy_description(video)
    lines = text.strip().splitlines()
    title = lines[0].strip() if lines else ""
    return video, title, "\n".join(lines[1:]).strip()


def delete_compilation(output, name):
    video = compilation_video(output, name)
    try:
        video.unlink()
    except PermissionError as error:
        raise ValueError("La vidéo est ouverte dans un autre programme. Ferme-la puis réessaie.") from error
    for file in (*sidecars(video), compilation_folder(output) / THUMBNAIL_FOLDER / f"{video.stem}.jpg"):
        file.unlink(missing_ok=True)
    if video.parent.name == video.stem:
        # Files added by hand stay, with their folder.
        try:
            video.parent.rmdir()
        except OSError:
            pass
    return {"deleted": name}
