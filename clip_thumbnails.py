"""Miniatures JPEG des clips, rangées dans <export>/thumbs/ à côté de clips/."""

import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

THUMBNAIL_FOLDER = "thumbs"
THUMBNAIL_NAME = re.compile(r"[\w-]+\.jpg")
# Le clip commence trois secondes avant le lancer : cette image montre le joueur en train de viser.
THUMBNAIL_TIME = 2.5
THUMBNAIL_WIDTH = 192
# Évite de lancer des dizaines de FFmpeg quand une page de la liste demande ses miniatures d'un coup.
_running = threading.BoundedSemaphore(4)


def ffmpeg_path():
    bundled = Path.home() / ".csdm" / "ffmpeg" / "bin" / "ffmpeg.exe"
    found = bundled if bundled.is_file() else shutil.which("ffmpeg")
    return Path(found) if found else None


def thumbnail_path(clip_file):
    return clip_file.parent.parent / THUMBNAIL_FOLDER / f"{clip_file.stem}.jpg"


def _extract(ffmpeg, clip_file, target, seconds):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(seconds),
                    "-i", clip_file, "-frames:v", "1", "-vf", f"scale={THUMBNAIL_WIDTH}:-2", "-q:v", "5", target],
                   capture_output=True, check=False, creationflags=flags, timeout=30)
    return target.is_file() and target.stat().st_size > 0


def create_thumbnail(clip_file, ffmpeg=None):
    """Crée la miniature si elle manque ou date d'avant le clip. Renvoie son chemin, ou None."""
    target = thumbnail_path(clip_file)
    try:
        if target.is_file() and target.stat().st_mtime >= clip_file.stat().st_mtime:
            return target
    except OSError:
        return None
    ffmpeg = ffmpeg or ffmpeg_path()
    if not ffmpeg or not clip_file.is_file():
        return None
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_name(f".{target.stem}-{uuid4().hex}.jpg")
    try:
        with _running:
            # Un clip plus court que le décalage ne donne aucune image : on prend alors la première.
            created = _extract(ffmpeg, clip_file, temporary, THUMBNAIL_TIME) or _extract(ffmpeg, clip_file, temporary, 0)
        if not created:
            return None
        temporary.replace(target)
        return target
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def create_thumbnails(clip_files, workers=4):
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sum(1 for result in pool.map(lambda clip: create_thumbnail(clip, ffmpeg), clip_files) if result)


def remove_thumbnail(clip_file):
    with suppress(OSError):
        thumbnail_path(clip_file).unlink(missing_ok=True)


def backfill(output):
    """Crée les miniatures manquantes de tous les exports d'un dossier."""
    clips = sorted(output.glob(f"*/clips/*.mp4"))
    missing = [clip for clip in clips if not thumbnail_path(clip).is_file()]
    print(f"{len(clips)} clips · {len(missing)} miniatures à créer", flush=True)
    created = create_thumbnails(missing)
    print(f"{created} miniatures créées", flush=True)
    return created


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "exports"
    if not ffmpeg_path():
        sys.exit("FFmpeg est introuvable.")
    backfill(folder)
