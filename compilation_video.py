import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from job_control import job_checkpoint
from utility_video import run


WIDTH, HEIGHT = 1080, 1920
FRAME_HEIGHT = WIDTH * 9 // 16
FRAME_TOP = (HEIGHT - FRAME_HEIGHT) // 2
FRAME_BOTTOM = FRAME_TOP + FRAME_HEIGHT
FPS = 60
FADE_SECONDS = 0.25
MAX_CLIPS = 30
# Instagram's caption limit, the shortest of the three platforms.
DESCRIPTION_MAX = 2200
META_VERSION = 1
COMPILATION_FOLDER = "compilations"
ACCENT = "#ffad66"
KIND_LABELS = {"smoke": "SMOKE", "flash": "FLASH", "he": "HE", "molo": "MOLOTOV"}
KIND_COLORS = {"smoke": "#8dd3f3", "flash": "#f7df88", "molo": "#fa9c77", "he": "#ed91af"}
SIDE_COLORS = {"t": "#f29423", "ct": "#378ef0"}
MOUSE_LABELS = {"left": "LEFT CLICK", "right": "RIGHT CLICK", "middle": "LEFT + RIGHT CLICK"}
FONT = "Segoe UI"
# Average advance of Segoe UI Bold capitals, in em. It only sizes the key boxes around centred text.
CAPITAL_ADVANCE = 0.64


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


def controls_keys(clip):
    controls = (clip.get("lineup") or {}).get("controls") or {}
    keys = [key for key in controls.get("directions", ()) if key in ("W", "Q", "S", "D")]
    if controls.get("walk"):
        keys.append("WALK")
    if controls.get("crouch"):
        keys.append("CROUCH")
    if controls.get("jump"):
        keys.append("JUMP")
    if mouse := MOUSE_LABELS.get(controls.get("mouse")):
        keys.append(mouse)
    return keys


def fitted_size(text, size, width):
    return max(28, min(size, int(width / max(1, len(text) * CAPITAL_ADVANCE))))


def text(x, y, value, size, color="#ffffff", weight=700, anchor="middle", spacing=0, opacity=1):
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-weight="{weight}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" letter-spacing="{spacing}" opacity="{opacity}">{escape(value)}</text>')


def keycaps(keys, y, size=40):
    """A centred row of key boxes, wrapped on two lines when it is too wide."""
    padding, gap, height = 30, 18, size + 40
    widths = [round(len(key) * size * CAPITAL_ADVANCE) + 2 * padding for key in keys]
    rows, row, row_width = [], [], 0
    for key, width in zip(keys, widths):
        if row and row_width + gap + width > WIDTH - 120:
            rows.append(row)
            row, row_width = [], 0
        row.append((key, width))
        row_width += width + (gap if len(row) > 1 else 0)
    if row:
        rows.append(row)
    parts = []
    for index, row in enumerate(rows):
        total = sum(width for _, width in row) + gap * (len(row) - 1)
        x = (WIDTH - total) / 2
        top = y + index * (height + 16)
        for key, width in row:
            parts.append(f'<rect x="{x:.1f}" y="{top}" width="{width}" height="{height}" rx="18" fill="#10161c" '
                         f'fill-opacity="0.92" stroke="{ACCENT}" stroke-width="4"/>')
            parts.append(text(round(x + width / 2, 1), top + height / 2 + size * 0.36, key, size))
            x += width + gap
    return "".join(parts)


def overlay_svg(item, index, total):
    clip, title = item["clip"], item["title"]
    kind = clip["kind"]
    side = clip.get("side", "")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}">',
             # A dark veil keeps the text readable when a flash whitens the blurred background.
             f'<rect width="{WIDTH}" height="{FRAME_TOP}" fill="#0c1013" fill-opacity="0.55"/>',
             f'<rect y="{FRAME_BOTTOM}" width="{WIDTH}" height="{HEIGHT - FRAME_BOTTOM}" fill="#0c1013" '
             f'fill-opacity="0.55"/>']
    # Top band: map and utility, then the callout.
    parts.append(f'<rect x="440" y="300" width="200" height="6" rx="3" fill="{KIND_COLORS[kind]}"/>')
    parts.append(text(WIDTH / 2, 380, f"{map_label(item['map'])} · {KIND_LABELS[kind]}", 40, KIND_COLORS[kind],
                      spacing=6))
    headline = title.upper()
    parts.append(text(WIDTH / 2, 520, headline, fitted_size(headline, 104, WIDTH - 120)))
    if total > 1:
        parts.append(text(WIDTH - 60, 120, f"{index}/{total}", 40, "#ffffff", 600, "end", opacity=0.75))
    # Bottom band: the throw inputs. The thrower and the enemy POV take turns above them.
    keys = controls_keys(clip)
    if keys:
        parts.append(text(WIDTH / 2, FRAME_BOTTOM + 268, "HOW TO THROW", 28, "#9aa3ae", 600, spacing=8))
        parts.append(keycaps(keys, FRAME_BOTTOM + 296))
    parts.append("</svg>")
    return "".join(parts)


def layer(content):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}">{content}</svg>'


def thrower_svg(clip):
    side = clip.get("side", "")
    player = clip.get("player", "")
    parts = [text(WIDTH / 2, FRAME_BOTTOM + 120, player, fitted_size(player, 72, WIDTH - 160))]
    team = " · ".join(part for part in (clip.get("team", "").upper(), side.upper()) if part)
    if team:
        parts.append(text(WIDTH / 2, FRAME_BOTTOM + 180, team, 34, SIDE_COLORS.get(side, "#9aa3ae"), 600, spacing=4))
    return layer("".join(parts))


def pov_svg(clip):
    """The enemy POV badge, drawn where the thrower name was."""
    pov = clip["pov"]
    label = f"ENEMY POV · {pov['player']}"
    size = fitted_size(label, 46, WIDTH - 220)
    width = min(WIDTH - 120, round(len(label) * size * 0.6) + 96)
    x = (WIDTH - width) / 2
    y = FRAME_BOTTOM + 52
    parts = [f'<rect x="{x:.1f}" y="{y}" width="{width}" height="92" rx="46" fill="#b3261e" fill-opacity="0.92"/>',
             text(WIDTH / 2, y + 46 + size * 0.36, label, size)]
    effect = {"blind": f"BLINDED {pov.get('value', 0):.1f}S", "damage": f"-{pov.get('value', 0)} HP"}.get(pov.get("effect"))
    if effect:
        parts.append(text(WIDTH / 2, FRAME_BOTTOM + 194, effect, 34, "#ff8a7a", 700, spacing=4))
    return layer("".join(parts))


def render_image(ffmpeg, svg, target):
    source = target.with_suffix(".svg")
    source.write_text(svg, encoding="utf-8")
    run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", source, target],
        capture_output=True, encoding="utf-8")
    if not target.is_file():
        raise ValueError("FFmpeg n’a pas pu dessiner le bandeau.")


def render_segment(ffmpeg, ffprobe, item, index, total, work):
    clip_file = item["file"]
    duration, audio = probe(ffprobe, clip_file)
    overlay = work / f"overlay-{index}.png"
    render_image(ffmpeg, overlay_svg(item, index, total), overlay)
    thrower = work / f"thrower-{index}.png"
    render_image(ffmpeg, thrower_svg(item["clip"]), thrower)
    inputs = ["-i", clip_file, "-loop", "1", "-i", overlay, "-loop", "1", "-i", thrower]
    pov_start = None
    if pov := item["clip"].get("pov"):
        pov_start = pov.get("offset")
        if isinstance(pov_start, (int, float)) and 0 < pov_start < duration:
            badge = work / f"pov-{index}.png"
            render_image(ffmpeg, pov_svg(item["clip"]), badge)
            inputs += ["-loop", "1", "-i", badge]
        else:
            pov_start = None
    if not audio:
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    fade_out = max(0, duration - FADE_SECONDS)
    graph = [
        f"[0:v]fps={FPS},split=2[bg][fg]",
        # Blur a small copy for speed, then scale it to fill the vertical frame.
        f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=12,"
        f"eq=brightness=-0.22:saturation=0.75,scale={WIDTH}:{HEIGHT}[back]",
        f"[fg]scale={WIDTH}:{FRAME_HEIGHT}:flags=lanczos,setsar=1[front]",
        f"[back][front]overlay=0:{FRAME_TOP}[base]",
        "[base][1:v]overlay=0:0:shortest=1[branded]",
    ]
    if pov_start is None:
        graph.append("[branded][2:v]overlay=0:0:shortest=1[named]")
        last = "named"
    else:
        # The thrower name gives way to the enemy POV badge when the camera switches.
        graph.append(f"[branded][2:v]overlay=0:0:shortest=1:enable='lt(t,{pov_start:.3f})'[named]")
        graph.append(f"[named][3:v]overlay=0:0:shortest=1:enable='gte(t,{pov_start:.3f})'[pov]")
        last = "pov"
    graph.append(f"[{last}]fade=t=in:st=0:d={FADE_SECONDS},fade=t=out:st={fade_out:.3f}:d={FADE_SECONDS},"
                 f"format=yuv420p[v]")
    audio_input = "0:a" if audio else f"{len([part for part in inputs if part == '-i']) - 1}:a"
    graph.append(f"[{audio_input}]aresample=48000,aformat=channel_layouts=stereo,"
                 f"afade=t=in:st=0:d={FADE_SECONDS},afade=t=out:st={fade_out:.3f}:d={FADE_SECONDS}[a]")
    segment = work / f"segment-{index:02d}.mp4"
    run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", segment], capture_output=True, encoding="utf-8")
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
                   "team": item["clip"].get("team"), "pov": bool(item["clip"].get("pov"))} for item in items],
    })
    report("Compilation terminée", target.name)
    return target
