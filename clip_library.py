import json
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from grenade_data import UTILITY_KINDS
from throw_groups import RADAR_GROUP_RADIUS, THROW_GROUP_SIZE_UNITS


CATALOGUE_PREFIX = "window.utilityCatalogue = "
EXPORT_NAME = re.compile(r"[\w-]+")
CLIP_NAME = re.compile(r"[\w-]+\.mp4")
CLIP_VIDEO = re.compile(r"clips/[\w-]+\.mp4")
CLIP_FLAGS = ("favorite", "watched")


def export_url(folder):
    return f"/exports/{quote(folder.name)}"


def read_catalogue(manifest):
    source = manifest.read_text(encoding="utf-8").strip()
    if not source.startswith(CATALOGUE_PREFIX):
        raise ValueError("Catalogue illisible.")
    return json.loads(source[len(CATALOGUE_PREFIX):].removesuffix(";"))


def completed_catalogues(output):
    for manifest in output.glob("*/catalogue.js"):
        folder = manifest.parent
        try:
            if (folder.resolve() != folder or (folder / "work").exists()
                    or not (folder / "index.html").is_file() or manifest.resolve() != manifest):
                continue
            catalogue = read_catalogue(manifest)
            if isinstance(catalogue.get("matches"), list):
                yield folder, manifest.stat().st_mtime, catalogue
        except (OSError, AttributeError, TypeError, ValueError):
            continue


def published_clips(match):
    return (clip for clip in match["clips"] if clip.get("kind") in UTILITY_KINDS
            and CLIP_VIDEO.fullmatch(clip.get("video", "")))


def playable_matches(folder, catalogue):
    for match in catalogue["matches"]:
        clips = [clip for clip in published_clips(match) if (folder / clip["video"]).is_file()]
        if clips:
            yield {**match, "clips": clips}


def existing_galleries(output):
    galleries = []
    for folder, date, data in completed_catalogues(output):
        matches = list(playable_matches(folder, data))
        if matches:
            count = sum(len(match["clips"]) for match in matches)
            galleries.append({"title": data["title"], "maps": [match["map"] for match in matches],
                              "count": count, "url": export_url(folder) + "/index.html", "date": date})
    return sorted(galleries, key=lambda item: item["date"], reverse=True)


def read_clip_states(folder):
    path = folder / ".clip-state.json"
    try:
        if path.resolve() == path:
            states = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(states, dict):
                return {file: state for file, state in states.items() if isinstance(state, dict)}
    except (OSError, ValueError):
        pass
    return {}


def throw_usage_path(folder, filename):
    return folder / f".throw-usage-{filename}.json"


def read_throw_usage(path):
    try:
        if path.resolve() == path:
            counts = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(counts, dict):
                return {demo: count for demo, count in counts.items() if type(count) is int and count > 0}
    except (OSError, ValueError):
        pass
    return {}


def record_throw_usage(folder, filename, demo, count):
    clip_file = folder / "clips" / filename
    if not clip_file.is_file():
        return
    path = throw_usage_path(folder, filename)
    counts = read_throw_usage(path)
    if counts.get(demo) == count:
        return
    counts[demo] = count
    temporary = folder / f".throw-usage-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(counts, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(path)
        if not clip_file.is_file():
            path.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)


def throw_references(output, map_name):
    radar = None
    clips = []
    removed = []
    for folder, _, data in sorted(completed_catalogues(output), key=lambda item: item[1], reverse=True):
        for match in data["matches"]:
            if match["map"] != map_name:
                continue
            radar = radar or match.get("radar")
            # Use one catalogue snapshot so a concurrent deletion cannot hide a throw.
            clips.extend({**clip, "exportId": folder.name} for clip in published_clips(match))
            removed.extend(clip for clip in match.get("removedThrows", ()) if clip.get("kind") in UTILITY_KINDS)
    return radar, clips, removed


def library_catalogue(output):
    maps = {}
    for folder, _, data in sorted(completed_catalogues(output), key=lambda item: item[1], reverse=True):
        base = export_url(folder)
        states = read_clip_states(folder)
        for match in playable_matches(folder, data):
            clips = []
            for clip in match["clips"]:
                video = clip["video"]
                filename = Path(video).name
                state = states.get(filename, {})
                counts = read_throw_usage(throw_usage_path(folder, filename))
                usage = {"usage": {"total": sum(counts.values()), "demos": len(counts)}} if counts else {}
                clips.append({**clip, **usage, "video": f"{base}/{video}", "file": filename,
                              "exportId": folder.name, "source": match["demo"],
                              "calloutOverride": state.get("calloutOverride"),
                              **{flag: state.get(flag) is True for flag in CLIP_FLAGS}})
            entry = maps.setdefault(match["map"], {"map": match["map"], "radar": None, "clips": []})
            radar = match.get("radar")
            if radar and not entry["radar"]:
                entry["radar"] = {**radar, "upper": f"{base}/{radar['upper']}",
                                  "lower": f"{base}/{radar['lower']}" if radar.get("lower") else None}
            entry["clips"].extend(clips)
    return {"title": "Vidéothèque", "groupRadius": RADAR_GROUP_RADIUS, "groupBoxUnits": THROW_GROUP_SIZE_UNITS,
            "matches": [maps[map_name] for map_name in sorted(maps)]}


def clip_paths(output, export_id, filename):
    if (not isinstance(export_id, str) or not EXPORT_NAME.fullmatch(export_id)
            or not isinstance(filename, str) or not CLIP_NAME.fullmatch(filename)):
        raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")
    folder = output / export_id
    manifest = folder / "catalogue.js"
    clip_file = folder / "clips" / filename
    if (folder.resolve() != folder or manifest.resolve() != manifest or clip_file.resolve() != clip_file
            or not manifest.is_file() or (folder / "work").exists()):
        raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")
    return manifest, clip_file


def find_library_clip(output, export_id, filename):
    manifest, clip_file = clip_paths(output, export_id, filename)
    catalogue = read_catalogue(manifest)
    for match in catalogue["matches"]:
        for clip in published_clips(match):
            if clip["video"] == f"clips/{filename}" and clip_file.is_file():
                return clip_file, match["map"], clip
    raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")


def set_clip_state(output, export_id, filename, data):
    updates = {flag: data[flag] for flag in CLIP_FLAGS if flag in data}
    if any(type(value) is not bool for value in updates.values()):
        raise ValueError("État de clip invalide.")
    if "calloutOverride" in data:
        name = data["calloutOverride"]
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 64):
            raise ValueError("Saisis un nom de zone de 1 à 64 caractères.")
        updates["calloutOverride"] = name.strip() if name is not None else None
    if not updates:
        raise ValueError("État de clip invalide.")
    clip_file, _, _ = find_library_clip(output, export_id, filename)
    folder = clip_file.parent.parent
    states = read_clip_states(folder)
    state = states.setdefault(filename, {})
    state.update(updates)

    temporary = folder / f".clip-state-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(states, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(folder / ".clip-state.json")
    finally:
        temporary.unlink(missing_ok=True)
    return state


def delete_clip(output, export_id, filename):
    manifest, clip_file = clip_paths(output, export_id, filename)
    folder = manifest.parent

    catalogue = read_catalogue(manifest)
    original = manifest.read_bytes()
    removed = False
    for match in catalogue["matches"]:
        remaining = []
        for clip in match["clips"]:
            if clip.get("video") == f"clips/{filename}":
                match.setdefault("removedThrows", []).append({key: clip[key] for key in ("kind", "launch", "impact")})
                removed = True
            else:
                remaining.append(clip)
        match["clips"] = remaining
    if not removed:
        return

    # Publish the index first. If Windows still holds the video open, restore the previous index.
    temporary = folder / f".catalogue-{uuid4().hex}.tmp"
    try:
        temporary.write_text(CATALOGUE_PREFIX + json.dumps(catalogue, ensure_ascii=True) + ";\n", encoding="utf-8")
        temporary.replace(manifest)
        try:
            clip_file.unlink(missing_ok=True)
        except OSError:
            temporary.write_bytes(original)
            temporary.replace(manifest)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    with suppress(OSError):
        throw_usage_path(folder, filename).unlink(missing_ok=True)
