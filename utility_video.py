import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from capture_progress import CaptureProgress
from clip_library import CATALOGUE_PREFIX, record_throw_usage, throw_references
from clip_thumbnails import create_thumbnails
from cs2_crosshair import CROSSHAIR_COMMANDS, standard_crosshair
from csdm_capture_pause import shorten_capture_pause
from demo_imports import demo_fingerprint, read_demo_imports, record_demo_import
from enemy_pov import assign_enemy_pov, clip_povs, recorded_parts
from favorite_throw_context import extract_context
from grenade_data import KINDS, UTILITY_KINDS, read_clips
from job_control import JobCancelled, check_job_cancelled, current_control, job_checkpoint
from job_log import current_job_log, log_command_output
from replay_library import save_replay_demo
from round_replay import demo_header, write_round_replay
from throw_groups import RADAR_GROUP_RADIUS, THROW_GROUP_SIZE_UNITS, group_throws
from throw_index import record_throw_index
from throw_inputs import add_held_keys, add_throw_inputs


ROOT = Path(__file__).resolve().parent
PROJECT_CSDM = ROOT / ".local" / "csdm"
CAPTURE_WORK_MARKER = ".capture-in-progress"
FALLBACK_DEMO_SOURCE = "esl"
CALIBRATION = json.loads((ROOT / "radars.json").read_text(encoding="utf-8"))
CAPTURE_SIZES = {720: (1280, 720), 1080: (1920, 1080)}


@dataclass(frozen=True)
class ExportResult:
    gallery: Path | None
    failed_demos: int


def run(command, *, log_stdout=True, **options):
    logger = current_job_log()
    if logger:
        logger.info("Commande · %s", subprocess.list2cmdline(command))
        if not options.get("capture_output"):
            options.setdefault("stdout", subprocess.PIPE)
            options.setdefault("stderr", subprocess.PIPE)
    if sys.stdout is None and os.name == "nt":
        options.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        options.setdefault("stdin", subprocess.DEVNULL)
        if not options.get("capture_output"):
            options.setdefault("stdout", subprocess.DEVNULL)
            options.setdefault("stderr", subprocess.PIPE)
    control = current_control()
    try:
        result = control.run(command, **options) if control else subprocess.run(command, check=True, **options)
    except (JobCancelled, subprocess.SubprocessError) as error:
        if logger:
            log_command_output(logger, error)
        raise
    if logger:
        log_command_output(logger, result, log_stdout=log_stdout)
        logger.info("Code de sortie · %d", result.returncode)
    return result


def find_csdm(installation):
    if installation:
        folders = [installation]
    else:
        on_path = shutil.which("csdm.cmd")
        folders = [Path(on_path).parent] if on_path else []
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        folders.append(local / "Programs" / "CS Demo Manager")
        folders.extend(Path(os.environ[key]) / "CS Demo Manager"
                       for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(key))
        folders.append(PROJECT_CSDM)
    for folder in folders:
        resources = folder / "resources"
        if ((folder / "cs-demo-manager.exe").is_file() and (resources / "app.asar").exists()
                and (resources / "static" / "csda.exe").is_file()):
            return folder.resolve()
    raise ValueError("Installe CS Demo Manager 3.21.0 ou indique son dossier avec --csdm.")


def csdm(installation, *args, **options):
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    return run([installation / "cs-demo-manager.exe", installation / "resources" / "app.asar" / "cli.js", *args],
               env=env, **options)


def seven_zip_command():
    if command := shutil.which("7z"):
        return command
    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        if folder := os.environ.get(key):
            command = Path(folder) / "7-Zip" / "7z.exe"
            if command.is_file():
                return command
    raise ValueError("7-Zip est requis pour ouvrir une archive .rar.")


def describe_archive(path):
    result = run([seven_zip_command(), "l", "-ba", "-slt", "-sccUTF-8", path], capture_output=True,
                 encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=20, log_stdout=False)
    members = {}
    for entry in re.split(r"\n(?=Path = )", result.stdout):
        fields = dict(line.split(" = ", 1) for line in entry.splitlines() if " = " in line)
        member = PurePosixPath(fields.get("Path", "").replace("\\", "/"))
        if (fields.get("Folder") == "+" or member.suffix.lower() != ".dem"
                or member.is_absolute() or ".." in member.parts):
            continue
        size, crc = fields.get("Size", ""), fields.get("CRC", "").upper()
        members[member.as_posix()] = (int(size), crc) if size.isdecimal() and crc else None
    if not members:
        raise ValueError("Cette archive ne contient aucune démo .dem.")
    return dict(sorted(members.items()))


def load_demos(source, work, map_filter, members=()):
    job_checkpoint()
    demos = work / "demos"
    demos.mkdir()
    if source.suffix.lower() == ".dem":
        path = demos / source.name
        try:
            os.link(source, path)
        except OSError:
            shutil.copyfile(source, path)
        return [path]
    if source.suffix.lower() != ".rar":
        raise ValueError("Sélectionne un fichier .dem ou une archive .rar.")
    command = [seven_zip_command(), "x", "-y", f"-o{demos}"]
    if members:
        command.extend(["-spd", source, "--", *(member.replace("/", os.sep) for member in members)])
    else:
        command.extend([source, "*.dem", "-r"])
    run(command)
    found = sorted(path for path in demos.rglob("*") if path.suffix.lower() == ".dem" and path.is_file()
                   and path.resolve().is_relative_to(demos))
    if map_filter:
        found = [path for path in found if map_filter.casefold() in path.stem.casefold()]
    if members:
        found = [path for path in found if path.relative_to(demos).as_posix() in members]
    if not found:
        raise ValueError("Aucune démo ne correspond à la sélection.")
    return found


def inspect_demo_source(source, imports):
    if source.suffix.lower() == ".dem":
        size = source.stat().st_size
        imported = any(size == info[0] for info in imports.values()) and demo_fingerprint(source)[0] in imports
        return [{"member": None, "imported": imported}]

    members = describe_archive(source)
    # Use the RAR directory to shortlist candidates. Only the SHA-256 can confirm a match.
    known_files = {tuple(info) for info in imports.values()}
    candidates = [member for member, info in members.items() if imports and (info is None or info in known_files)]
    imported = set()
    if candidates:
        with tempfile.TemporaryDirectory(prefix="cs2-import-") as temp:
            work = Path(temp).resolve()
            for demo in load_demos(source, work, None, candidates):
                if demo_fingerprint(demo)[0] in imports:
                    imported.add(demo.relative_to(work / "demos").as_posix())
    return [{"member": member, "imported": member in imported} for member in members]


def radar_layout(map_name, installation):
    if installation is None or map_name not in CALIBRATION:
        return None
    origin_x, origin_y, scale, threshold_z = CALIBRATION[map_name]
    assets = installation / "resources" / "static" / "images" / "maps" / "cs2" / "radars"
    upper = assets / f"{map_name}.png"
    if not upper.is_file():
        return None
    lower = assets / f"{map_name}_lower.png"
    with upper.open("rb") as image:
        image.seek(16)  # Width in the PNG IHDR chunk.
        size = struct.unpack(">I", image.read(4))[0]
    return {"originX": origin_x, "originY": origin_y, "units": scale * size, "thresholdZ": threshold_z,
            "upper": upper, "lower": lower if lower.is_file() else None}


def publish_radar(radar, out):
    if not radar:
        return None
    radars = out / "radars"
    radars.mkdir(exist_ok=True)
    published = radar.copy()
    for level in ("upper", "lower"):
        if asset := radar[level]:
            shutil.copyfile(asset, radars / asset.name)
            published[level] = f"radars/{asset.name}"
    return published


def sequence_parts(clips):
    """Each recorded sequence as (clip number, startTick, endTick), numbered from 1 in this order."""
    return [(number, start, end) for number, clip in enumerate(clips, 1) for start, end in recorded_parts(clip)]


def create_video_job(demo, clips, tick_rate, job_folder, capture_height=720):
    job_folder.mkdir()
    render_folder = job_folder / "video"
    render_folder.mkdir()
    script = job_folder / "camera.js"
    # One entry per sequence: the grenade camera of the clip, or null where an enemy view replays the detonation.
    camera_data = []
    sequences = []
    for number, clip in enumerate(clips, 1):
        start, launch = clip["startTick"], clip["launchTick"]
        povs = clip_povs(clip)
        thrower = {"tick": start - tick_rate // 2, "playerSteamId": clip["steamId"], "playerName": clip["player"]}
        enemies = [{"tick": pov["tick"], "playerSteamId": pov["steamId"], "playerName": pov["player"]} for pov in povs]
        grenade = {key: clip[key] for key in ("launchTick", "activationTick", "endTick", "flight", "impact", "yaw")}
        parts = recorded_parts(clip)
        if len(parts) > 1:
            # The grenade camera runs to the cut, then the demo goes back once for each enemy view.
            grenade["cameraEndTick"] = parts[0][1]
            plan = [(parts[0], grenade, [thrower])]
            plan += [(part, None, [{**enemy, "tick": part[0] - tick_rate // 2}])
                     for part, enemy in zip(parts[1:], enemies)]
        else:
            if povs:
                # Older clips: the grenade camera stops when the first enemy point of view starts.
                grenade["cameraEndTick"] = povs[0]["tick"]
            plan = [(parts[0], grenade, [thrower, *enemies])]
        for (part_start, part_end), camera, cameras in plan:
            sequence = len(sequences) + 1
            camera_data.append(camera)
            cfg = [
                f'mirv_script_load "{script.as_posix()}"',
                f"mirv_script_exec globalThis.csUtilityVideo({sequence})",
                "mirv_cmd clear",
                "cl_demo_freecam 0",
                # The same static green crosshair in every clip, whatever the player used.
                *CROSSHAIR_COMMANDS,
            ]
            if camera:
                cfg.append(f'mirv_cmd addAtTick {launch} "spec_mode 6"')
            sequences.append({
                "number": sequence, "startTick": part_start, "endTick": part_end, "cfg": "\n".join(cfg),
                "playerCameras": cameras,
                "cameras": [], "playersOptions": [], "showXRay": False, "showAssists": True,
                "showOnlyDeathNotices": False, "deathNoticesDuration": 5, "recordAudio": True,
                "playerVoicesEnabled": False,
            })
    script.write_text("{\nconst grenadeClips = " + json.dumps(camera_data, separators=(",", ":"), allow_nan=False) + ";\n"
                      + (ROOT / "follow-grenade.js").read_text(encoding="utf-8") + "\n}\n", encoding="utf-8")
    config = {
        "demoPath": str(demo), "outputFolderPath": str(render_folder), "recordingSystem": "HLAE",
        "recordingOutput": "video", "encoderSoftware": "FFmpeg",
        "width": CAPTURE_SIZES[capture_height][0], "height": CAPTURE_SIZES[capture_height][1],
        "framerate": 60, "closeGameAfterRecording": True, "concatenateSequences": False,
        "trueView": False, "sequences": sequences,
        "ffmpegSettings": {"customLocationEnabled": False, "customExecutableLocation": "", "audioBitrate": 192,
                           "constantRateFactor": 20, "videoCodec": "libx264", "audioCodec": "aac",
                           "videoContainer": "mp4", "inputParameters": "", "outputParameters": ""},
    }
    config_path = job_folder / "csdm-video.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config_path, render_folder


def sequence_videos(render_folder):
    return {file.name: file for file in render_folder.rglob("sequence-*.mp4")}


def video_tools():
    folder = Path.home() / ".csdm" / "ffmpeg" / "bin"
    ffmpeg = folder / "ffmpeg.exe" if (folder / "ffmpeg.exe").is_file() else shutil.which("ffmpeg")
    ffprobe = folder / "ffprobe.exe" if (folder / "ffprobe.exe").is_file() else shutil.which("ffprobe")
    return ffmpeg, ffprobe


def completed_video(file):
    probe = video_tools()[1]
    if not probe:
        return False
    try:
        result = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=duration", "-of", "csv=p=0", file], capture_output=True,
                                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def join_videos(parts, target):
    """Puts the sequences of one clip end to end, without encoding them again."""
    ffmpeg = video_tools()[0]
    if not ffmpeg:
        raise ValueError("FFmpeg est nécessaire pour assembler les clips avec POV ennemie.")
    playlist = target.with_suffix(".txt")
    playlist.write_text("".join(f"file '{part.resolve().as_posix()}'\n" for part in parts), encoding="utf-8")
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", playlist, "-c", "copy", "-movflags", "+faststart", target], capture_output=True,
                   timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    playlist.unlink(missing_ok=True)
    if not target.is_file() or not target.stat().st_size:
        raise ValueError(f"FFmpeg n’a pas assemblé {target.name}.")
    for part in parts:
        part.unlink(missing_ok=True)
    return target


def clip_videos(render_folder, clips, *, completed_only=False):
    """The finished video of each clip, {clip number: path}, its sequences joined when it has several."""
    videos = sequence_videos(render_folder)
    logger = current_job_log()
    parts = {}
    for sequence, (number, start, end) in enumerate(sequence_parts(clips), 1):
        parts.setdefault(number, []).append(videos.get(f"sequence-{sequence}-tick-{start}-to-{end}.mp4"))
    found = {}
    for number, files in parts.items():
        missing = [file for file in files if file is None or not file.is_file() or not file.stat().st_size
                   or (completed_only and not completed_video(file))]
        if missing:
            if logger:
                logger.warning("MP4 absent, vide ou illisible · clip %d", number)
            continue
        if len(files) == 1:
            found[number] = files[0]
            continue
        try:
            found[number] = join_videos(files, render_folder / f"clip-{number}.mp4")
        except (OSError, ValueError, subprocess.SubprocessError):
            if logger:
                logger.warning("Assemblage impossible · clip %d", number, exc_info=True)
    return found


def capture_filename(map_number, number, clip):
    return f"m{map_number}-{number:04d}-{clip['kind']}.mp4"


def publish_videos(render_folder, clips, out, map_number, *, completed_only=False):
    logger = current_job_log()
    completed_only = completed_only or len(sequence_videos(render_folder)) < len(sequence_parts(clips))
    found = clip_videos(render_folder, clips, completed_only=completed_only)
    if logger:
        logger.info("Démo %d · MP4 détectés / clips attendus · %d/%d", map_number, len(found), len(clips))
    sources = [(number, clip, found[number]) for number, clip in enumerate(clips, 1) if number in found]
    if not sources:
        return []
    clips_dir = out / "clips"
    clips_dir.mkdir(exist_ok=True)
    published = []
    for number, clip, source in sources:
        if completed_only and not completed_video(source):
            if logger:
                logger.warning("MP4 illisible · %s", source.name)
            continue
        destination = clips_dir / capture_filename(map_number, number, clip)
        try:
            shutil.move(source, destination)
        except PermissionError:
            if completed_only:
                if logger:
                    logger.warning("MP4 verrouillé · %s", source.name, exc_info=True)
                continue
            raise
        clip["video"] = destination.relative_to(out).as_posix()
        published.append(clip)
    create_thumbnails([out / clip["video"] for clip in published])
    return published


def publish_gallery(out, source, matches):
    for file_name in ("index.html", "gallery.css", "gallery.js", "callouts.js", "compilation.js", "GeistVariable.woff2",
                      "GeistMonoVariable.woff2", "Geist-OFL.txt"):
        shutil.copyfile(ROOT / "gallery" / file_name, out / file_name)
    shutil.copytree(ROOT / "gallery" / "maps", out / "maps", dirs_exist_ok=True)
    # The source path lets a later compilation find the demo again to capture it in 1080p.
    catalogue = {"title": source.name, "sourcePath": str(source), "groupRadius": RADAR_GROUP_RADIUS,
                 "groupBoxUnits": THROW_GROUP_SIZE_UNITS, "matches": matches}
    temporary = out / ".catalogue.js.tmp"
    try:
        temporary.write_text(CATALOGUE_PREFIX + json.dumps(catalogue, ensure_ascii=True) + ";\n", encoding="utf-8")
        temporary.replace(out / "catalogue.js")
    finally:
        temporary.unlink(missing_ok=True)
    return out / "index.html"


def run_demo_analyzer(installation, demo, folder, **options):
    analyzer = installation / "resources" / "static" / "csda.exe"
    command = [analyzer, f"-demo-path={demo}", f"-output={folder}", "-format=csdm", "-positions"]
    try:
        run(command, capture_output=True, **options)
        return None
    except subprocess.CalledProcessError as error:
        output = error.stderr or error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if "unknown demo source" not in output.lower():
            raise
    if logger := current_job_log():
        logger.info("Provenance non détectée pour %s · nouvelle analyse en mode tournoi (%s).",
                    demo.name, FALLBACK_DEMO_SOURCE)
    run([*command, f"-source={FALLBACK_DEMO_SOURCE}"], capture_output=True, **options)
    return FALLBACK_DEMO_SOURCE


def analyze_demo(installation, demo, kind, work, *, replay_output=None, contexts=None, keep_demo=False,
                 enemy_pov=False):
    job_checkpoint()
    with tempfile.TemporaryDirectory(dir=work) as temp:
        folder = Path(temp)
        analysis_source = run_demo_analyzer(installation, demo, folder)
        result = read_clips(folder, demo, kind)
        if analysis_source:
            for clip in result[2]:
                clip["analysisSource"] = analysis_source
        if enemy_pov and result[2]:
            try:
                assigned = assign_enemy_pov(folder, demo, result[2], result[1])
                if logger := current_job_log():
                    logger.info("POV ennemie · %d/%d lancers de %s", assigned, len(result[2]), demo.name)
            except JobCancelled:
                raise
            except Exception:
                if logger := current_job_log():
                    logger.warning("POV ennemie indisponible pour %s", demo.name, exc_info=True)
        if contexts is not None:
            try:
                contexts.update(extract_context(folder, demo, {id(clip): clip for clip in result[2]}))
            except (OSError, ValueError, IndexError) as error:
                if logger := current_job_log():
                    logger.warning("Contexte de tri incomplet pour %s · %s", demo.name, error)
        if replay_output is not None:
            map_name, tick_rate, _, checksum = result
            try:
                write_round_replay(replay_output, demo, folder, map_name, tick_rate, checksum)
                if keep_demo:
                    save_replay_demo(replay_output, demo, checksum)
            except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
                message = f"Relevé des joueurs indisponible pour {demo.name} · {error}"
                if logger := current_job_log():
                    logger.warning(message, exc_info=True)
                elif sys.stderr is not None:
                    print(message, file=sys.stderr)
            job_checkpoint()
        return result


def record_known_throw_usage(output, previous, occurrences, checksum, *, current_export=None):
    indexed = 0
    for clip in previous:
        if throws := occurrences.get(id(clip)):
            folder = output / clip["exportId"] if "exportId" in clip else current_export
            if record_throw_usage(folder, Path(clip["video"]).name, checksum, throws):
                indexed += 1
    return indexed


def clip_index_references(previous, current_export=None):
    for clip in previous:
        export_id = clip["exportId"] if "exportId" in clip else current_export.name
        yield clip, f"{export_id}/{Path(clip['video']).name}"


def index_throw_metadata(args):
    output = args.output.resolve(strict=True)
    installation = find_csdm(args.csdm)
    failed = False
    with reserve_export(output):
        for source in args.demo:
            try:
                source = source.resolve(strict=True)
                with tempfile.TemporaryDirectory(prefix=".throw-index-", dir=output) as temp:
                    work = Path(temp)
                    print(f"Indexation · {source.name}", flush=True)
                    for demo in load_demos(source, work, args.map):
                        map_name, _, clips, checksum = analyze_demo(installation, demo, args.type, work, replay_output=output)
                        previous, removed = throw_references(output, map_name)
                        _, occurrences = group_throws(clips, previous, removed=removed)
                        indexed = record_known_throw_usage(output, previous, occurrences, checksum)
                        kinds = UTILITY_KINDS if args.type == "all" else (args.type,)
                        record_throw_index(output, map_name, checksum, demo.name, clips, kinds,
                                           occurrences, clip_index_references(previous), removed)
                        print(f"{demo.name} · {indexed} clips associés à cette démo.", flush=True)
                        release_staged_demo(demo, work)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                failed = True
                print(f"Échec de l’indexation de {source.name} · {error}", file=sys.stderr)
    return int(failed)


def import_replay_demo(installation, demo, output, work, keep_demo, name=None):
    job_checkpoint()
    with tempfile.TemporaryDirectory(dir=work) as temp:
        folder = Path(temp)
        run_demo_analyzer(installation, demo, folder)
        map_name, tick_rate, checksum, _ = demo_header(folder, demo)
        job_checkpoint()
        data = write_round_replay(output, demo, folder, map_name, tick_rate, checksum, name)
    if keep_demo:
        save_replay_demo(output, demo, checksum)
    return map_name, sum(1 for stage in data["rounds"] if stage.get("sides"))


def import_replays(sources, output, installation, *, members=(), keep_demo=True, update=print, on_progress=None,
                   names=None):
    """Build round replays without touching the clip library, the utility index or the import registry."""
    failures = 0
    completed = 0
    with reserve_export(output):
        for index, source in enumerate(sources, 1):
            label = f"Fichier {index}/{len(sources)} · {source.name}"
            with tempfile.TemporaryDirectory(prefix=".replay-import-", dir=output) as temp:
                work = Path(temp).resolve()
                try:
                    if source.suffix.lower() == ".rar":
                        update("Extraction", f"{label} · Extraction des démos…")
                    demos = load_demos(source, work, None, members if len(sources) == 1 else ())
                except JobCancelled:
                    raise
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    failures += 1
                    update("Replay", f"{label} · {error}")
                    if on_progress is not None:
                        on_progress(source, None, "error", note=str(error))
                    continue
                for number, demo in enumerate(demos, 1):
                    member = demo.relative_to(work / "demos").as_posix() if source.suffix.lower() == ".rar" else None

                    def queue(state, **details):
                        if on_progress is not None:
                            on_progress(source, member, state, **details)

                    queue("analysis")
                    update("Replay", f"{label} · Démo {number}/{len(demos)} · {demo.name}")
                    try:
                        map_name, rounds = import_replay_demo(installation, demo, output, work, keep_demo,
                                                              (names or {}).get(source))
                    except JobCancelled:
                        raise
                    except Exception as error:
                        failures += 1
                        if logger := current_job_log():
                            logger.exception("Échec du replay · %s", demo.name)
                        message = str(error) or type(error).__name__
                        if isinstance(error, ImportError):
                            message = "Demoparser est requis pour les replays (étape 6 du README)."
                        update("Replay", f"{demo.name} · {message}")
                        queue("error", note=message)
                    else:
                        completed += 1
                        note = f"Replay · {rounds} rounds" + (" · démo conservée" if keep_demo else "")
                        update("Replay", f"{demo.name} · {map_name} · {note}")
                        queue("complete", map=map_name, note=note)
                    finally:
                        release_staged_demo(demo, work)
                    check_job_cancelled()
    return completed, failures


def record_video(installation, demo, config, before_capture, *, source=None):
    job_checkpoint()
    source_options = ("--source", source) if source else ()
    analysis = csdm(installation, "analyze", demo, "--json", *source_options, stdout=subprocess.PIPE, encoding="utf-8",
                    errors="replace", log_stdout=False)
    try:
        if json.loads(analysis.stdout)["failed"]:
            raise ValueError(f"L’analyse CS Demo Manager a échoué pour {demo.name}.")
    except (ValueError, TypeError, KeyError):
        if logger := current_job_log():
            logger.error("Réponse de CS Demo Manager pour %s\n%s", demo.name, analysis.stdout)
        raise
    job_checkpoint()
    before_capture()
    # The crosshair settings of the capture are undone once CS2 has closed.
    with standard_crosshair():
        csdm(installation, "video", "--config-file", config)


@contextmanager
def reserve_export(output):
    with (output / ".utility-export.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Un export utilise déjà cette vidéothèque.") from error
        yield


def generate(args, report=None, on_import=None, on_progress=None):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with reserve_export(output):
        return generate_export(args, output, report, on_import, on_progress)


def remove_work_directory(path, parent):
    if path.parent != parent or path.resolve() != path:
        raise ValueError("Dossier de travail invalide.")
    for attempt in range(30):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(0.25)


def recover_interrupted_exports(output, update):
    # Actual exports share a lock. A marked work directory from an earlier run can only be stale.
    for gallery in output.iterdir():
        if not gallery.is_dir() or gallery.resolve() != gallery:
            continue
        work = gallery / "work"
        marker = gallery / CAPTURE_WORK_MARKER
        if work.resolve() != work or not marker.is_file():
            continue
        try:
            if work.exists():
                remove_work_directory(work, gallery)
            marker.unlink()
            if not any(gallery.iterdir()):
                gallery.rmdir()
        except OSError as error:
            update("Nettoyage", f"Cache de {gallery.name} encore inaccessible · {error}")
        else:
            update("Nettoyage", f"Fichiers temporaires d’un export interrompu supprimés · {gallery.name}")


def release_staged_demo(demo, work):
    if demo.is_relative_to(work / "demos") and demo.resolve() == demo:
        demo.unlink(missing_ok=True)


def top_key(demo, clip):
    return demo.name, clip["kind"], clip["steamId"], clip["launchTick"]


def score_candidates(args, out, demo, map_name, tick_rate, candidates, occurrences, contexts, update):
    """Read the throw inputs once per candidate, then score them when the export selects by score."""
    pending = [clip for clip in candidates if "duration" not in clip]
    if pending:
        try:
            add_throw_inputs(demo, pending, tick_rate)
        except JobCancelled:
            raise
        except Exception as error:
            if logger := current_job_log():
                logger.warning("Commandes de lancer indisponibles pour %s", demo.name, exc_info=True)
            update("Analyse", f"Commandes de lancer indisponibles pour {demo.name} · {error}")
        for clip in pending:
            clip["duration"] = round((clip["endTick"] - clip["startTick"]) / tick_rate, 1)
    if args.prepare or getattr(args, "all_clips", False):
        return None
    try:
        from favorite_suggestions import Throw, train_capture_ranker
    except ImportError as error:
        raise ValueError("Le tri par score nécessite NumPy, fourni avec Demoparser.") from error
    model = getattr(args, "capture_ranker", None)
    if model is None:
        try:
            model = train_capture_ranker(out.parent, getattr(args, "score_threshold", None))
        except ValueError as error:
            raise ValueError(f"{error} Sélectionne « Tous les lancers distincts » pour créer des clips.") from error
        args.capture_ranker = model
        if not getattr(args, "top_count", None):
            update("Analyse", f"Seuil de capture · {model.threshold:.3f} · "
                   f"{model.recovered}/{model.favorites} favoris retrouvés sur les démos de validation.")
    unscored = [clip for clip in candidates if "potentialScore" not in clip]
    if unscored:
        samples = [Throw(map_name, {**clip, "usage": {"demos": 1, "total": len(occurrences[id(clip)])}},
                         contexts.get(id(clip), {})) for clip in unscored]
        for clip, score in zip(unscored, model.scores(samples), strict=True):
            clip["potentialScore"] = float(score)
    return model


def export_demos(args, source, installation, out, work, update, on_import, on_progress):
    if source.suffix.lower() == ".rar":
        update("Extraction", f"Extraction des démos de {source.name}…")
    member = getattr(args, "demo_member", None)
    demos = load_demos(source, work, args.map, (member,) if member else ())
    imports = read_demo_imports(out.parent) if not args.prepare else {}
    matches = []
    gallery = None
    duplicate_count = 0
    failed_demos = 0
    indexed_throws = 0
    selective = not args.prepare and not getattr(args, "all_clips", False)
    top_count = getattr(args, "top_count", None) if selective else None
    enemy_pov = getattr(args, "enemy_pov", False) and not args.prepare
    analyses = {}

    def analyze(demo):
        if demo in analyses:
            return analyses.pop(demo)
        contexts = {} if selective else None
        result = analyze_demo(installation, demo, args.type if args.prepare else "all", work,
                              replay_output=None if args.prepare else out.parent, contexts=contexts,
                              keep_demo=getattr(args, "keep_demo", False), enemy_pov=enemy_pov)
        return result, contexts

    if top_count:
        # Rank every demo of the source first, then capture the best throws wherever they are.
        ranked = []
        for number, demo in enumerate(demos, 1):
            job_checkpoint()
            update("Classement", f"Démo {number}/{len(demos)} · {demo.name}")
            result, contexts = analyze(demo)
            analyses[demo] = (result, contexts)
            map_name, tick_rate, throws, _ = result
            previous, removed = throw_references(out.parent, map_name)
            unique, occurrences = group_throws(throws, previous, removed=removed)
            candidates = [clip for clip in unique if args.type == "all" or clip["kind"] == args.type]
            score_candidates(args, out, demo, map_name, tick_rate, candidates, occurrences, contexts, update)
            ranked.extend((clip["potentialScore"], clip["kind"], top_key(demo, clip)) for clip in candidates)
        ranked.sort(reverse=True)
        chosen = []
        if getattr(args, "top_mixed", False):
            # Start with the best throw of each utility, then complete the selection by score.
            for kind in dict.fromkeys(kind for _, kind, _ in ranked):
                chosen.append(next(entry for entry in ranked if entry[1] == kind))
            chosen = sorted(chosen, reverse=True)[:top_count]
        chosen += [entry for entry in ranked if entry not in chosen][:top_count - len(chosen)]
        args.top_keys = {key for _, _, key in chosen}
        kinds = Counter(kind for _, kind, _ in chosen)
        update("Classement", f"Top {len(chosen)} sur {len(ranked)} lancers distincts · "
               f"{', '.join(f'{name} {count}' for name, count in sorted(kinds.items()))}.")

    def finish_demo(demo, fingerprint, demo_member):
        if args.prepare:
            return
        release_staged_demo(demo, work)
        record_demo_import(out.parent, imports, *fingerprint)
        if on_import is not None:
            on_import(source, demo_member)

    for map_number, demo in enumerate(demos, 1):
        job_checkpoint()
        progress = f"{map_number}/{len(demos)}"
        demo_member = demo.relative_to(work / "demos").as_posix() if source.suffix.lower() == ".rar" else None

        def queue(state, **details):
            if on_progress is not None:
                on_progress(source, demo_member, state, **details)

        queue("analysis")
        update("Analyse", f"Démo {progress} · {demo.name}")
        fingerprint = demo_fingerprint(demo) if not args.prepare else None
        if fingerprint and fingerprint[0] in imports:
            update("Analyse", f"{demo.name} · Déjà importé.")
        (map_name, tick_rate, throws, checksum), contexts = analyze(demo)
        if not throws:
            update("Analyse", f"{map_name} : aucun lancer correspondant.")
            finish_demo(demo, fingerprint, demo_member)
            queue("skipped", map=map_name, note="Aucun lancer correspondant.")
            continue
        previous, removed = throw_references(out.parent, map_name)
        previous.extend(clip for match in matches if match["map"] == map_name for clip in match["clips"])
        unique, occurrences = group_throws(throws, previous, removed=removed)
        skipped = len(throws) - len(unique)
        duplicate_count += skipped
        if skipped:
            update("Analyse", f"{map_name} · {skipped} {'doublon ignoré' if skipped == 1 else 'doublons ignorés'}.")
        if not args.prepare:
            record_known_throw_usage(out.parent, previous, occurrences, checksum, current_export=out)
        candidates = [clip for clip in unique if args.type == "all" or clip["kind"] == args.type]
        if not selective:
            candidates = candidates[:args.limit]
        clips = []
        try:
            if candidates:
                model = score_candidates(args, out, demo, map_name, tick_rate, candidates, occurrences, contexts,
                                         update)
                if top_count:
                    clips = sorted((clip for clip in candidates if top_key(demo, clip) in args.top_keys),
                                   key=lambda clip: clip["potentialScore"], reverse=True)
                    update("Analyse", f"{len(clips)} {'lancer retenu' if len(clips) == 1 else 'lancers retenus'} "
                           f"dans le top {top_count}.")
                elif selective:
                    clips = [clip for clip in candidates if clip["potentialScore"] > model.threshold]
                    update("Analyse", f"{len(clips)}/{len(candidates)} lancers distincts au-dessus du seuil "
                           f"de {model.threshold:.3f}.")
                    clips = clips[:args.limit]
                else:
                    clips = candidates
        finally:
            if not args.prepare:
                references = list(clip_index_references(previous, out))
                references.extend((clip, f"{out.name}/{capture_filename(map_number, number, clip)}")
                                  for number, clip in enumerate(clips, 1))
                record_throw_index(out.parent, map_name, checksum, demo.name, throws, UTILITY_KINDS,
                                   occurrences, references, removed)
                indexed_throws += len(throws)
        if not clips:
            finish_demo(demo, fingerprint, demo_member)
            queue("skipped", map=map_name, duplicates=skipped, note="Lancers indexés. Aucun clip à générer.")
            continue
        radar = radar_layout(map_name, installation)
        queue("analysis", map=map_name, total=len(clips), duplicates=skipped)
        counts = Counter(clip["kind"] for clip in clips)
        estimates = sum(clip["estimated"] for clip in clips)
        update("Analyse", f"{map_name} : {len(clips)} {'clip' if len(clips) == 1 else 'clips'} "
               f"({', '.join(f'{name} {count}' for name, count in sorted(counts.items()))})"
               f", {estimates} fins estimées.")
        if enemy_pov:
            with_pov = sum(bool(clip_povs(clip)) for clip in clips)
            update("Analyse", f"POV ennemie · {with_pov}/{len(clips)} clips.")
        try:
            add_held_keys(demo, clips, tick_rate)
        except JobCancelled:
            raise
        except Exception:
            # The compilation keys then light up at the release only.
            if logger := current_job_log():
                logger.warning("Touches tenues indisponibles pour %s", demo.name, exc_info=True)
        config, render_folder = create_video_job(demo, clips, tick_rate, work / f"map-{map_number}",
                                                 getattr(args, "capture_height", 720))
        cancelled = False
        capture_failed = False
        missing = 0
        if not args.prepare:
            expected = len(clips)

            def before_capture():
                if on_progress is not None:
                    queue("starting", capture=CaptureProgress(
                        demo, render_folder, expected, [number for number, _, _ in sequence_parts(clips)]))
                update("Capture", f"Démo {progress} · CS2 enregistre {expected} "
                       f"{'clip' if expected == 1 else 'clips'} sur {map_name}.")

            try:
                source_options = {"source": clips[0]["analysisSource"]} if "analysisSource" in clips[0] else {}
                record_video(installation, demo, config, before_capture, **source_options)
            except JobCancelled:
                cancelled = True
                update("Annulation", "Arrêt de CS2 et récupération des clips finalisés…")
                # Closing this CLI disconnects its owned CSDM job, which stops the game and encoder.
                time.sleep(2.5)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                capture_failed = True
                if logger := current_job_log():
                    logger.exception("Échec de la capture · %s", demo.name)
                update("Export", f"Démo {progress} · Erreur de capture · {error}")
            queue("publishing")
            update("Export", f"Démo {progress} · Ajout des MP4 à la galerie.")
            clips = publish_videos(render_folder, clips, out, map_number, completed_only=cancelled or capture_failed)
            missing = expected - len(clips)
            if not cancelled and (missing or capture_failed):
                failed_demos += 1
                update("Export", f"Démo {progress} · {demo.name} · {len(clips)}/{expected} clips conservés.")
            for clip in clips:
                record_throw_usage(out, Path(clip["video"]).name, checksum, occurrences[id(clip)])
        else:
            update("Préparation", f"Configuration : {config}")
        for clip in clips:
            del clip["flight"], clip["yaw"], clip["steamId"]
        if clips:
            matches.append({"map": map_name, "demo": demo.name, "radar": publish_radar(radar, out),
                            "clips": clips})
            gallery = publish_gallery(out, source, matches)
        if not args.prepare:
            remove_work_directory(config.parent, work)
        if cancelled:
            queue("cancelled", published=len(clips), gallery=gallery if clips else None)
            raise JobCancelled()
        if missing:
            release_staged_demo(demo, work)
        else:
            finish_demo(demo, fingerprint, demo_member)
        queue("partial" if missing or capture_failed else "complete",
              published=len(clips), gallery=gallery if clips else None)
        check_job_cancelled()
    return gallery, duplicate_count, failed_demos, indexed_throws


def generate_export(args, output, report, on_import=None, on_progress=None):
    def update(phase, message):
        if report is not None:
            report(phase, message)
        else:
            print(message, flush=True)

    if not args.prepare:
        recover_interrupted_exports(output, update)
    source = args.demo.resolve(strict=True)
    installation = find_csdm(args.csdm)
    if not args.prepare and installation == PROJECT_CSDM:
        if shorten_capture_pause(installation):
            update("Préparation", "Capture · libx264 · pause de 0,5 s avec le plugin CS Demo Manager compatible.")
        else:
            update("Préparation", "Capture · libx264 · pause du plugin CS Demo Manager installé.")
    prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", source.stem)[:64] + "-"
    out = Path(tempfile.mkdtemp(prefix=prefix, dir=output))
    work = out / "work"
    work.mkdir()
    gallery = None
    try:
        if not args.prepare:
            (out / CAPTURE_WORK_MARKER).touch(exist_ok=False)
        update("Préparation", f"Dossier de la galerie : {out}")
        gallery, duplicate_count, failed_demos, indexed_throws = export_demos(args, source, installation, out, work,
                                                                            update, on_import, on_progress)
    except JobCancelled as error:
        if (out / "index.html").is_file():
            error.gallery = out / "index.html"
        raise
    finally:
        if not args.prepare or gallery is None:
            remove_work_directory(work, out)
            (out / CAPTURE_WORK_MARKER).unlink(missing_ok=True)
            if not any(out.iterdir()):
                out.rmdir()
    if gallery is None and not failed_demos:
        if indexed_throws:
            update("Terminé", f"{indexed_throws} lancers indexés. Aucun nouveau clip à générer.")
            return ExportResult(None, 0)
        if duplicate_count:
            update("Terminé", "Aucun nouveau clip à générer. Tous les lancers correspondants sont déjà connus.")
            return ExportResult(None, 0)
        raise ValueError("Aucun lancer correspondant au filtre dans les démos sélectionnées.")
    if failed_demos:
        message = f"{failed_demos} {'démo en erreur' if failed_demos == 1 else 'démos en erreur'}."
        if gallery:
            message += f" Clips disponibles dans la galerie · {gallery}"
        update("Terminé avec erreurs", message)
    else:
        update("Terminé", f"Galerie : {gallery}")
    if gallery and not args.no_open:
        webbrowser.open(gallery.as_uri())
    return ExportResult(gallery, failed_demos)


def main():
    parser = argparse.ArgumentParser(description="Génère des clips de grenades CS2 et leur galerie locale.")
    parser.add_argument("demo", type=Path, nargs="+", help="Démo GOTV .dem ou archive HLTV .rar")
    parser.add_argument("--type", choices=["all", *dict.fromkeys(KINDS.values())], default="all")
    parser.add_argument("--map", help="Partie du nom de fichier .dem à sélectionner dans l’archive")
    parser.add_argument("--output", type=Path, default=ROOT / "exports", help="Dossier parent des galeries")
    parser.add_argument("--csdm", type=Path, help="Dossier d’installation de CS Demo Manager")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="Prépare les séquences et le radar sans lancer CS2")
    mode.add_argument("--index-only", action="store_true", help="Indexe les lancers et les positions des joueurs sans capture")
    mode.add_argument("--replay-only", action="store_true", help="Crée uniquement les replays 2D, sans clip ni index")
    parser.add_argument("--keep-demo", action="store_true", help="Conserve la démo à côté de son replay")
    parser.add_argument("--top", type=int, dest="top_count", help="Capture les N meilleurs lancers de la source")
    parser.add_argument("--mixed", action="store_true", dest="top_mixed",
                        help="Avec --top, prend au moins un lancer de chaque utilitaire")
    parser.add_argument("--enemy-pov", action="store_true", help="Termine chaque clip dans la vue de la victime")
    parser.add_argument("--capture-height", type=int, choices=sorted(CAPTURE_SIZES), default=720,
                        help="Hauteur de capture en pixels")
    parser.add_argument("--limit", type=int, help="Limite de lancers par carte pour un essai")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all-clips", action="store_true", help="Génère tous les lancers distincts")
    selection.add_argument("--score-threshold", type=float, help="Seuil minimal du score Potentiel favori (entre 0 et 1)")
    parser.add_argument("--no-open", action="store_true", help="N’ouvre pas le navigateur après export")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit doit être au moins égal à 1.")
    if args.score_threshold is not None and not 0 < args.score_threshold < 1:
        parser.error("--score-threshold doit être compris entre 0 et 1, exclus.")
    try:
        if args.index_only:
            return index_throw_metadata(args)
        if args.replay_only:
            output = args.output.resolve()
            output.mkdir(parents=True, exist_ok=True)
            completed, failures = import_replays([source.resolve(strict=True) for source in args.demo], output,
                                                 find_csdm(args.csdm), keep_demo=args.keep_demo,
                                                 update=lambda phase, message: print(message, flush=True))
            return int(bool(failures) or not completed)
        if len(args.demo) != 1:
            parser.error("Indique une seule source pour une capture, ou utilise --index-only ou --replay-only.")
        args.demo = args.demo[0]
        result = generate(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Échec : {error}", file=sys.stderr)
        return 1
    return int(bool(result.failed_demos))


if __name__ == "__main__":
    sys.exit(main())
