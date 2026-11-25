import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from enemy_pov import clip_povs
from compilation_titles import FRAME_HEIGHT, FRAME_TOP, HEIGHT, WIDTH, titles
from job_control import job_checkpoint
from utility_video import run


FPS = 60
# CS2 demos record 64 ticks a second; clip videos start on the clip's startTick.
TICK_RATE = 64
FADE_SECONDS = 0.25
MAX_CLIPS = 30
# Instagram's caption limit, the shortest of the three platforms.
DESCRIPTION_MAX = 2200
META_VERSION = 1
COMPILATION_FOLDER = "compilations"
KIND_LABELS = {"smoke": "SMOKE", "flash": "FLASH", "he": "HE", "molo": "MOLOTOV"}
KIND_COLORS = {"smoke": "#8dd3f3", "flash": "#f7df88", "molo": "#fa9c77", "he": "#ed91af"}
SIDE_COLORS = {"t": "#f29423", "ct": "#378ef0"}
NEUTRAL_COLOR = "#9aa3ae"
# Each key with the buttons of throw_inputs.HELD_BUTTONS that must all be held for it to light up.
MOUSE_KEYS = {"left": ("LEFT CLICK", ("attack",)), "right": ("RIGHT CLICK", ("attack2",)),
              "middle": ("LEFT + RIGHT CLICK", ("attack", "attack2"))}


def ffmpeg_tools():
    folder = Path.home() / ".csdm" / "ffmpeg" / "bin"
    ffmpeg = folder / "ffmpeg.exe" if (folder / "ffmpeg.exe").is_file() else shutil.which("ffmpeg")
    ffprobe = folder / "ffprobe.exe" if (folder / "ffprobe.exe").is_file() else shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("FFmpeg est nécessaire pour la compilation. Installe-le depuis CS Demo Manager.")
    return Path(ffmpeg), Path(ffprobe)


def probe(ffprobe, video):
    result = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                             "-of", "json", video], capture_output=True, encoding="utf-8", errors="replace",
                            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = json.loads(result.stdout or "{}")
    duration = float(data.get("format", {}).get("duration") or 0)
    audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams", ()))
    if duration <= 0:
        raise ValueError(f"Vidéo illisible · {Path(video).name}")
    return duration, audio


def map_label(name):
    return re.sub(r"^(de|cs|ar)_", "", name).replace("_", " ").upper()


def overlap(first, second):
    """The moments covered by both lists of [from, to] spans."""
    spans, i, j = [], 0, 0
    while i < len(first) and j < len(second):
        start, end = max(first[i][0], second[j][0]), min(first[i][1], second[j][1])
        if start < end:
            spans.append([start, end])
        if first[i][1] < second[j][1]:
            i += 1
        else:
            j += 1
    return spans


def controls_keys(clip):
    """The throw's keys, each with the spans in seconds where the thrower holds it, or None for older clips."""
    lineup = clip.get("lineup") or {}
    controls = lineup.get("controls") or {}
    keys = [(key, (key,)) for key in controls.get("directions", ()) if key in ("W", "Q", "S", "D")]
    for flag, label in (("walk", "WALK"), ("crouch", "CROUCH"), ("jump", "JUMP")):
        if controls.get(flag):
            keys.append((label, (flag,)))
    if mouse := MOUSE_KEYS.get(controls.get("mouse")):
        keys.append(mouse)
    held = lineup.get("held")
    result = []
    for label, buttons in keys:
        spans = None
        if isinstance(held, dict):
            spans = held.get(buttons[0]) or []
            for button in buttons[1:]:
                spans = overlap(spans, held.get(button) or [])
        result.append({"label": label, "held": spans})
    return result


def title_card(item, index, total, duration):
    """What one clip's titles say, and when the throw, the detonation and the camera switch happen in its video."""
    clip = item["clip"]
    kind, side = clip["kind"], clip.get("side", "")

    def moment(key):
        tick, start = clip.get(key), clip.get("startTick")
        if not all(isinstance(value, (int, float)) for value in (tick, start)):
            return None
        seconds = (tick - start) / TICK_RATE
        return seconds if 0 <= seconds < duration else None

    card = {
        "duration": duration, "color": KIND_COLORS[kind], "band": f"{map_label(item['map'])} · {KIND_LABELS[kind]}",
        "title": item["title"].upper(), "counter": f"{index:02d}/{total:02d}" if total > 1 else None,
        "player": clip.get("player", ""),
        "team": " · ".join(part for part in ((clip.get("team") or "").upper(), side.upper()) if part),
        "team_color": SIDE_COLORS.get(side, NEUTRAL_COLOR), "keys": controls_keys(clip),
        "launch": moment("launchTick"), "pop": moment("activationTick"), "povs": [],
    }
    for pov in clip_povs(clip):
        if not isinstance(pov.get("offset"), (int, float)) or not 0 < pov["offset"] < duration:
            continue
        value = pov.get("value") or 0
        effect = {"blind": f"BLINDED {value:.1f}S", "damage": f"-{value} HP", "inside": "IN THE SMOKE"}.get(
            pov.get("effect"))
        if pov.get("killed"):
            effect = f"{effect} · KILLED" if effect else "KILLED"
        card["povs"].append({"at": pov["offset"], "label": f"ENEMY POV · {pov.get('player', '')}".upper(),
                             "effect": effect})
    card["povs"].sort(key=lambda pov: pov["at"])
    # Each badge stays until the next camera switch.
    for current, following in zip(card["povs"], card["povs"][1:] + [None]):
        current["until"] = following["at"] if following else None
    card["pov"] = card["povs"][0] if card["povs"] else None
    return card


def render_segment(ffmpeg, ffprobe, item, index, total, work):
    clip_file = Path(item["file"]).resolve()
    duration, audio = probe(ffprobe, clip_file)
    # Named relative to the work folder: a drive colon would need escaping inside the filter graph.
    script = f"titles-{index:02d}.ass"
    (work / script).write_text(titles(title_card(item, index, total, duration), FPS), encoding="utf-8")
    inputs = ["-i", clip_file]
    if not audio:
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    fade_out = max(0, duration - FADE_SECONDS)
    graph = [
        f"[0:v]fps={FPS},split=2[bg][fg]",
        # Blur a small copy for speed, then scale it to fill the vertical frame.
        f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=12,"
        f"eq=brightness=-0.22:saturation=0.75,scale={WIDTH}:{HEIGHT}[back]",
        f"[fg]scale={WIDTH}:{FRAME_HEIGHT}:flags=lanczos,setsar=1[front]",
        f"[back][front]overlay=0:{FRAME_TOP},ass={script},fade=t=in:st=0:d={FADE_SECONDS},"
        f"fade=t=out:st={fade_out:.3f}:d={FADE_SECONDS},format=yuv420p[v]",
        f"[{'0' if audio else '1'}:a]aresample=48000,aformat=channel_layouts=stereo,"
        f"afade=t=in:st=0:d={FADE_SECONDS},afade=t=out:st={fade_out:.3f}:d={FADE_SECONDS}[a]",
    ]
    segment = work / f"segment-{index:02d}.mp4"
    run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", segment.name], capture_output=True, encoding="utf-8", cwd=work)
    if not segment.is_file() or not segment.stat().st_size:
        raise ValueError(f"FFmpeg n’a pas livré le segment {index}.")
    return segment


def write_meta(video, meta):
    """The compilation's history entry, next to the video: clips, post text, platform links."""
    target = video.with_suffix(".json")
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def compilation_name(items):
    maps = sorted({map_label(item["map"]).title() for item in items})
    kinds = sorted({KIND_LABELS[item["clip"]["kind"]].title() for item in items})
    kinds_label = " ".join(kinds) if len(kinds) <= 2 else "Mix"
    label = f"{' '.join(maps)} {kinds_label}" if len(maps) <= 2 else kinds_label
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:60] or "Compilation"
    return f"Compilation {label} {len(items)} clips {datetime.now().strftime('%Y-%m-%d %H-%M')}.mp4"


def compilation_target(folder, name):
    """Each compilation gets its own folder, named like its video: video, post text and history entry."""
    stem = Path(name).stem
    candidate, number = stem, 2
    while (folder / candidate).exists():
        candidate, number = f"{stem} ({number})", number + 1
    (folder / candidate).mkdir()
    return folder / candidate / f"{candidate}.mp4"


def generate_compilation(output, items, report, description=""):
    if not 1 <= len(items) <= MAX_CLIPS:
        raise ValueError(f"Choisis entre 1 et {MAX_CLIPS} clips.")
    ffmpeg, ffprobe = ffmpeg_tools()
    folder = output / COMPILATION_FOLDER
    folder.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".compilation-", dir=output) as directory:
        work = Path(directory)
        segments = []
        for index, item in enumerate(items, 1):
            job_checkpoint()
            report("Compilation", f"Clip {index}/{len(items)} · {item['clip'].get('player', '')} · {item['title']}")
            segments.append(render_segment(ffmpeg, ffprobe, item, index, len(items), work))
        report("Compilation", "Assemblage des clips…")
        playlist = work / "segments.txt"
        playlist.write_text("".join(f"file '{segment.name}'\n" for segment in segments), encoding="utf-8")
        result = work / "compilation.mp4"
        run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", playlist.name, "-c", "copy", "-movflags", "+faststart", result.name],
            capture_output=True, encoding="utf-8", cwd=work)
        if not result.is_file() or not result.stat().st_size:
            raise ValueError("FFmpeg n’a pas assemblé la compilation.")
        target = compilation_target(folder, compilation_name(items))
        shutil.move(result, target)
    if description:
        # Same name as the video, ready to paste on TikTok, Instagram or YouTube.
        target.with_suffix(".txt").write_text(description.strip() + "\n", encoding="utf-8")
    try:
        duration = round(probe(ffprobe, target)[0], 2)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        duration = None
    write_meta(target, {
        "version": META_VERSION, "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration": duration, "clipCount": len(items), "description": description.strip(), "links": {},
        "clips": [{**item.get("reference", {}), "title": item["title"], "map": item["map"],
                   "kind": item["clip"].get("kind"), "player": item["clip"].get("player"),
                   "team": item["clip"].get("team"), "povs": len(clip_povs(item["clip"]))} for item in items],
    })
    report("Compilation terminée", target.name)
    return target
