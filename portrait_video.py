import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from utility_video import run


GRENADE_NAMES = {"smoke": "Smoke", "flash": "Flash", "he": "HE", "molo": "Molo", "decoy": "Leurre"}
# Crop around the center using the largest even dimensions with an exact 9:16 ratio.
PORTRAIT_FILTER = r"crop=18*trunc(min(iw/18\,ih/32)):32*trunc(min(iw/18\,ih/32)),scale=720:1280:flags=lanczos,setsar=1"


def portrait_folder(clip_file):
    folder = clip_file.parent.parent / "recadrages" / clip_file.stem
    if folder.resolve() != folder:
        raise ValueError("Dossier de rendu vertical invalide.")
    return folder


def existing_portrait(folder):
    return next((file for file in folder.glob("*.mp4")
                 if file.resolve() == file and file.is_file() and file.stat().st_size), None)


def portrait_name(clip, map_name):
    player = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clip["player"]).strip(" .")[:50] or "Joueur"
    name = re.sub(r"^(de|cs|ar)_", "", map_name).replace("_", " ").capitalize()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:30] or "Carte"
    stamp = datetime.now().strftime("%Y-%m-%d %H-%M")
    return f"{player} {name} {GRENADE_NAMES[clip['kind']]} {stamp}.mp4"


def generate_portrait(source, map_name, clip, folder, report):
    bundled = Path.home() / ".csdm" / "ffmpeg" / "bin" / "ffmpeg.exe"
    ffmpeg = bundled if bundled.is_file() else shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("FFmpeg est nécessaire pour recadrer ce clip.")

    report("Recadrage 9:16", f"Recadrage du clip de {clip['player']}…")
    folder.mkdir(parents=True, exist_ok=True)
    filename = portrait_name(clip, map_name)
    with tempfile.TemporaryDirectory(dir=folder) as directory:
        video = Path(directory) / "portrait.mp4"
        run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", source,
             "-map", "0:v:0", "-map", "0:a:0?", "-vf", PORTRAIT_FILTER,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "copy", "-movflags", "+faststart", video], capture_output=True, encoding="utf-8")
        if not video.is_file() or not video.stat().st_size:
            raise ValueError("FFmpeg n’a pas livré le fichier 9:16.")
        video.rename(folder / filename)
    report("Rendu 9:16 terminé", filename)
