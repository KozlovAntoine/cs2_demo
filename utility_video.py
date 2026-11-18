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

from clip_library import CATALOGUE_PREFIX, record_throw_usage, throw_references
from demo_imports import demo_fingerprint, read_demo_imports, record_demo_import
from grenade_data import KINDS, read_clips
from job_control import JobCancelled, check_job_cancelled, current_control, job_checkpoint
from job_log import current_job_log, log_command_output
from throw_groups import RADAR_GROUP_RADIUS, THROW_GROUP_SIZE_UNITS, group_throws
from throw_inputs import add_throw_inputs


ROOT = Path(__file__).resolve().parent
CALIBRATION = json.loads((ROOT / "radars.json").read_text(encoding="utf-8"))


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
        folders.append(ROOT / ".local" / "csdm")
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


def create_video_job(demo, clips, tick_rate, job_folder):
    job_folder.mkdir()
    render_folder = job_folder / "video"
    render_folder.mkdir()
    script = job_folder / "camera.js"
    camera_data = [{key: clip[key] for key in ("launchTick", "activationTick", "endTick", "flight", "impact", "yaw")}
                   for clip in clips]
    script.write_text("{\nconst grenadeClips = " + json.dumps(camera_data, separators=(",", ":"), allow_nan=False) + ";\n"
                      + (ROOT / "follow-grenade.js").read_text(encoding="utf-8") + "\n}\n", encoding="utf-8")
    sequences = []
    for number, clip in enumerate(clips, 1):
        start = clip["startTick"]
        launch = clip["launchTick"]
        cfg = "\n".join([
            f'mirv_script_load "{script.as_posix()}"',
            f"mirv_script_exec globalThis.csUtilityVideo({number})",
            "mirv_cmd clear",
            "cl_demo_freecam 0",
            f'mirv_cmd addAtTick {launch} "spec_mode 6"',
        ])
        sequences.append({
            "number": number, "startTick": start, "endTick": clip["endTick"], "cfg": cfg,
            "playerCameras": [{"tick": start - tick_rate // 2, "playerSteamId": clip["steamId"],
                               "playerName": clip["player"]}],
            "cameras": [], "playersOptions": [], "showXRay": False, "showAssists": True,
            "showOnlyDeathNotices": False, "deathNoticesDuration": 5, "recordAudio": True,
            "playerVoicesEnabled": False,
        })
    config = {
        "demoPath": str(demo), "outputFolderPath": str(render_folder), "recordingSystem": "HLAE",
        "recordingOutput": "video", "encoderSoftware": "FFmpeg", "width": 1280, "height": 720,
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


def completed_video(file):
    bundled = Path.home() / ".csdm" / "ffmpeg" / "bin" / "ffprobe.exe"
    probe = str(bundled) if bundled.is_file() else shutil.which("ffprobe")
    if not probe:
        return False
    try:
        result = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=duration", "-of", "csv=p=0", file], capture_output=True,
                                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def publish_videos(render_folder, clips, out, map_number, *, completed_only=False):
    videos = sequence_videos(render_folder)
    logger = current_job_log()
    if logger:
        logger.info("Démo %d · MP4 détectés / clips attendus · %d/%d", map_number, len(videos), len(clips))
    sources = []
    for number, clip in enumerate(clips, 1):
        name = f"sequence-{number}-tick-{clip['startTick']}-to-{clip['endTick']}.mp4"
        source = videos.get(name)
        if source is not None and source.is_file() and source.stat().st_size:
            sources.append((number, clip, source))
        elif logger:
            logger.warning("MP4 absent ou vide · %s", name)
    if not sources:
        return []
    completed_only = completed_only or len(sources) < len(clips)
    clips_dir = out / "clips"
    clips_dir.mkdir(exist_ok=True)
    published = []
    for number, clip, source in sources:
        if completed_only and not completed_video(source):
            if logger:
                logger.warning("MP4 illisible · %s", source.name)
            continue
        destination = clips_dir / f"m{map_number}-{number:04d}-{clip['kind']}.mp4"
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
    return published


def publish_gallery(out, source, matches):
    for file_name in ("index.html", "gallery.css", "gallery.js", "callouts.js"):
        shutil.copyfile(ROOT / "gallery" / file_name, out / file_name)
    catalogue = {"title": source.name, "groupRadius": RADAR_GROUP_RADIUS,
                 "groupBoxUnits": THROW_GROUP_SIZE_UNITS, "matches": matches}
    temporary = out / ".catalogue.js.tmp"
    try:
        temporary.write_text(CATALOGUE_PREFIX + json.dumps(catalogue, ensure_ascii=True) + ";\n", encoding="utf-8")
        temporary.replace(out / "catalogue.js")
    finally:
        temporary.unlink(missing_ok=True)
    return out / "index.html"


def analyze_demo(installation, demo, kind, work):
    job_checkpoint()
    with tempfile.TemporaryDirectory(dir=work) as temp:
        analyzer = installation / "resources" / "static" / "csda.exe"
        run([analyzer, f"-demo-path={demo}", f"-output={temp}", "-format=csdm", "-positions"])
        return read_clips(Path(temp), demo, kind)


def record_video(installation, demo, config, before_capture):
    job_checkpoint()
    analysis = csdm(installation, "analyze", demo, "--json", stdout=subprocess.PIPE, encoding="utf-8",
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


def generate(args, report=None, on_import=None):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with reserve_export(output):
        return generate_export(args, output, report, on_import)


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


def release_staged_demo(demo, work):
    if demo.is_relative_to(work / "demos") and demo.resolve() == demo:
        demo.unlink(missing_ok=True)


def export_demos(args, source, installation, out, work, update, on_import):
    if source.suffix.lower() == ".rar":
        update("Extraction", f"Extraction des démos de {source.name}…")
    member = getattr(args, "demo_member", None)
    demos = load_demos(source, work, args.map, (member,) if member else ())
    imports = read_demo_imports(out.parent) if not args.prepare else {}
    matches = []
    gallery = None
    duplicate_count = 0
    failed_demos = 0

    def finish_demo(demo, fingerprint):
        if args.prepare:
            return
        release_staged_demo(demo, work)
        record_demo_import(out.parent, imports, *fingerprint)
        if on_import is not None:
            member = demo.relative_to(work / "demos").as_posix() if source.suffix.lower() == ".rar" else None
            on_import(source, member)

    for map_number, demo in enumerate(demos, 1):
        job_checkpoint()
        progress = f"{map_number}/{len(demos)}"
        update("Analyse", f"Démo {progress} · {demo.name}")
        fingerprint = demo_fingerprint(demo) if not args.prepare else None
        if fingerprint and fingerprint[0] in imports:
            update("Analyse", f"{demo.name} · Déjà importé.")
        map_name, tick_rate, clips, checksum = analyze_demo(installation, demo, args.type, work)
        if not clips:
            update("Analyse", f"{map_name} : aucun lancer correspondant.")
            finish_demo(demo, fingerprint)
            continue
        radar = radar_layout(map_name, installation)
        saved_radar, previous, removed = throw_references(out.parent, map_name)
        previous.extend(clip for match in matches if match["map"] == map_name for clip in match["clips"])
        unique, usage_counts = group_throws(clips, previous, saved_radar or radar, removed=removed)
        skipped = len(clips) - len(unique)
        duplicate_count += skipped
        if skipped:
            update("Analyse", f"{map_name} · {skipped} {'doublon ignoré' if skipped == 1 else 'doublons ignorés'}.")
        if not args.prepare:
            for clip in previous:
                count = usage_counts[id(clip)]
                if count:
                    folder = out.parent / clip["exportId"] if "exportId" in clip else out
                    record_throw_usage(folder, Path(clip["video"]).name, checksum, count)
        clips = unique[:args.limit]
        if not clips:
            finish_demo(demo, fingerprint)
            continue
        try:
            add_throw_inputs(demo, clips, tick_rate)
        except JobCancelled:
            raise
        except Exception as error:
            if logger := current_job_log():
                logger.warning("Commandes de lancer indisponibles pour %s", demo.name, exc_info=True)
            update("Analyse", f"Commandes de lancer indisponibles pour {demo.name} · {error}")
        counts = Counter(clip["kind"] for clip in clips)
        estimates = sum(clip["estimated"] for clip in clips)
        update("Analyse", f"{map_name} : {len(clips)} {'clip' if len(clips) == 1 else 'clips'} "
               f"({', '.join(f'{name} {count}' for name, count in sorted(counts.items()))})"
               f", {estimates} fins estimées.")
        config, render_folder = create_video_job(demo, clips, tick_rate, work / f"map-{map_number}")
        cancelled = False
        missing = 0
        if not args.prepare:
            expected = len(clips)
            capture_failed = False

            def before_capture():
                update("Capture", f"Démo {progress} · CS2 enregistre {expected} "
                       f"{'clip' if expected == 1 else 'clips'} sur {map_name}.")

            try:
                record_video(installation, demo, config, before_capture)
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
            update("Export", f"Démo {progress} · Ajout des MP4 à la galerie.")
            clips = publish_videos(render_folder, clips, out, map_number, completed_only=cancelled or capture_failed)
            missing = expected - len(clips)
            if not cancelled and (missing or capture_failed):
                failed_demos += 1
                update("Export", f"Démo {progress} · {demo.name} · {len(clips)}/{expected} clips conservés.")
            for clip in clips:
                record_throw_usage(out, Path(clip["video"]).name, checksum, usage_counts[id(clip)])
        else:
            update("Préparation", f"Configuration : {config}")
        for clip in clips:
            del clip["flight"], clip["yaw"], clip["steamId"]
            clip["duration"] = round((clip["endTick"] - clip["startTick"]) / tick_rate, 1)
        if clips:
            matches.append({"map": map_name, "demo": demo.name, "radar": publish_radar(radar, out),
                            "clips": clips})
            gallery = publish_gallery(out, source, matches)
        if not args.prepare:
            remove_work_directory(config.parent, work)
        if cancelled:
            raise JobCancelled()
        if missing:
            release_staged_demo(demo, work)
        else:
            finish_demo(demo, fingerprint)
        check_job_cancelled()
    return gallery, duplicate_count, failed_demos


def generate_export(args, output, report, on_import=None):
    def update(phase, message):
        if report is not None:
            report(phase, message)
        else:
            print(message, flush=True)

    source = args.demo.resolve(strict=True)
    installation = find_csdm(args.csdm)
    prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", source.stem)[:64] + "-"
    out = Path(tempfile.mkdtemp(prefix=prefix, dir=output))
    work = out / "work"
    work.mkdir()
    gallery = None
    try:
        update("Préparation", f"Dossier de la galerie : {out}")
        gallery, duplicate_count, failed_demos = export_demos(args, source, installation, out, work, update, on_import)
    except JobCancelled as error:
        if (out / "index.html").is_file():
            error.gallery = out / "index.html"
        raise
    finally:
        if not args.prepare or gallery is None:
            remove_work_directory(work, out)
            if not any(out.iterdir()):
                out.rmdir()
    if gallery is None and not failed_demos:
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
    parser.add_argument("demo", type=Path, help="Démo GOTV .dem ou archive HLTV .rar")
    parser.add_argument("--type", choices=["all", *dict.fromkeys(KINDS.values())], default="all")
    parser.add_argument("--map", help="Partie du nom de fichier .dem à sélectionner dans l’archive")
    parser.add_argument("--output", type=Path, default=ROOT / "exports", help="Dossier parent des galeries")
    parser.add_argument("--csdm", type=Path, help="Dossier d’installation de CS Demo Manager")
    parser.add_argument("--prepare", action="store_true", help="Prépare les séquences et le radar sans lancer CS2")
    parser.add_argument("--limit", type=int, help="Limite de lancers par carte pour un essai")
    parser.add_argument("--no-open", action="store_true", help="N’ouvre pas le navigateur après export")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit doit être au moins égal à 1.")
    try:
        result = generate(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Échec : {error}", file=sys.stderr)
        return 1
    return int(bool(result.failed_demos))


if __name__ == "__main__":
    sys.exit(main())
