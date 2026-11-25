import argparse
import gzip
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import urlopen
from uuid import uuid4

from callout_zones import change_zone, read_zones
from demo_imports import read_demo_imports
from grenade_data import UTILITY_KINDS
from job_control import JobCancelled, JobControl, job_checkpoint
from job_log import bind_job_log, create_job_log, current_job_log
from clip_library import (
    CATALOGUE_PREFIX, clip_paths, delete_clip, existing_galleries, export_url, find_library_clip, library_catalogue,
    playable_matches, published_catalogues, read_catalogue, set_clip_state,
)
from clip_thumbnails import THUMBNAIL_FOLDER, THUMBNAIL_NAME, create_thumbnail
from compilation_library import (
    compilation_thumbnail, compilation_video, delete_compilation, list_compilations, post_text, record_upload,
    update_compilation,
)
import youtube_publish
from compilation_video import COMPILATION_FOLDER, DESCRIPTION_MAX, MAX_CLIPS, generate_compilation
from portrait_video import existing_portrait, generate_portrait, portrait_folder
from replay_library import delete_replay, list_replays, load_replay, replay_name, saved_demo, storage_usage
from round_replay import FIRST_HALF_ROUNDS, player_heatmap, replay_index
from throw_index import unrendered_throws
from utility_video import (
    CALIBRATION, CAPTURE_SIZES, ROOT, find_csdm, generate, generate_export, import_replays, inspect_demo_source, radar_layout,
    reserve_export, run,
)


APP_ID = "cs2-utility-videos"
STATIC = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/utility.ico": "utility.ico"}
GALLERY_ASSETS = {"index.html", "gallery.css", "gallery.js", "callouts.js", "compilation.js", "GeistVariable.woff2",
                  "GeistMonoVariable.woff2", "Geist-OFL.txt"}
GALLERY_FILES = GALLERY_ASSETS | {"catalogue.js"}
MAP_EMBLEM = re.compile(r"(de|cs|ar)_[a-z0-9_]+\.png")
PLAYER_ASSETS = {"index.html", "players.css", "players.js"}
REPLAY_ASSETS = {"index.html", "replay.css", "replay.js"}
COMPILATION_ASSETS = {"index.html", "compilations.css", "compilations.js"}
CONTENT_POLICY = ("default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'; "
                  "object-src 'none'; base-uri 'none'; form-action 'self'")
QUEUE_FINISHED = {"complete", "partial", "skipped", "cancelled", "error"}


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            # SO_REUSEADDR can let multiple Windows processes listen on the same port.
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def choose_demos():
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilenames(parent=root, title="Choisir des démos CS2 ou des archives HLTV",
                                           filetypes=[("Démos CS2 et archives HLTV", ("*.dem", "*.rar"))])
    finally:
        root.destroy()


def pick_local_demos():
    python = Path(sys.executable)
    if os.name == "nt" and python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = run([python, __file__, "--pick-demo"], capture_output=True, encoding="utf-8", creationflags=flags)
    return json.loads(result.stdout)


class App:
    def __init__(self, output, csdm):
        self.output = output
        self.csdm = csdm
        self.lock = threading.Lock()
        self.picking = False
        self.selected_paths = ()
        self.selection = None
        self.job = {"state": "idle"}
        self.job_control = None
        self.queue = {}
        self.capture_progress = None

    def busy(self):
        return self.picking or self.job["state"] == "running"

    def radar_installation(self):
        try:
            return find_csdm(self.csdm)
        except ValueError:
            return None

    def callout_maps(self):
        installation = self.radar_installation()
        maps = []
        for name in CALIBRATION:
            radar = radar_layout(name, installation)
            if radar:
                for floor in ("upper", "lower"):
                    if radar[floor]:
                        radar[floor] = f"/callout-radars/{radar[floor].name}"
                maps.append({"map": name, "radar": radar, "zones": read_zones(self.output, name)})
        return maps

    def map_radar(self, name):
        radar = radar_layout(name, self.radar_installation())
        if radar:
            for level in ("upper", "lower"):
                if radar[level]:
                    radar[level] = f"/callout-radars/{radar[level].name}"
            return radar
        for folder, _, catalogue in published_catalogues(self.output):
            for match in catalogue["matches"]:
                radar = match.get("radar")
                if match["map"] == name and radar:
                    base = export_url(folder)
                    return {**radar, "upper": f"{base}/{radar['upper']}",
                            "lower": f"{base}/{radar['lower']}" if radar.get("lower") else None}
        return None

    def replays(self):
        with self.lock:
            replays = list_replays(self.output)
            running = self.job.get("replay") if self.job["state"] == "running" else None
            busy = self.busy()
        return {"replays": replays, "usage": storage_usage(replays), "busy": busy, "running": running}

    def replay(self, checksum):
        with self.lock:
            record = load_replay(self.output, checksum)
            try:
                record["zones"] = read_zones(self.output, record["map"]) if record["map"] in CALIBRATION else []
            except (ValueError, OSError):
                record["zones"] = []
        record["radar"] = self.map_radar(record["map"])
        return record

    def remove_replay(self, data):
        with self.lock:
            if self.busy():
                raise ValueError("Attends la fin du traitement en cours avant de supprimer un replay.")
            return delete_replay(self.output, data.get("id"), data.get("scope"))

    def reanalyze_replay(self, data):
        with self.lock:
            if self.busy():
                raise ValueError("Un export ou une sélection est déjà en cours.")
            checksum = data.get("id")
            demo = saved_demo(self.output, checksum)
            name = replay_name(self.output, checksum)

            def render_replay(report):
                completed, failures = import_replays([demo], self.output, find_csdm(self.csdm), keep_demo=True,
                                                     update=report, names={demo: name})
                if failures or not completed:
                    raise ValueError("La mise à jour du replay a échoué. Consulte le journal pour les détails.")
                report("Terminé", "Replay mis à jour.")
                return {}

            return self.start_job(render_replay, log_kind="replay", description=f"Mise à jour du replay · {demo.name}",
                                  replay=checksum)

    def player_maps(self):
        maps = {}
        missing = {}
        installation = self.radar_installation()
        for name, records in replay_index(self.output).items():
            radar = radar_layout(name, installation)
            if radar:
                for level in ("upper", "lower"):
                    if radar[level]:
                        radar[level] = f"/callout-radars/{radar[level].name}"
                maps[name] = (records, radar)
            else:
                missing[name] = records
        if missing:
            for folder, _, catalogue in published_catalogues(self.output):
                for match in catalogue["matches"]:
                    name = match["map"]
                    radar = match.get("radar")
                    if name in missing and radar:
                        base = export_url(folder)
                        maps[name] = (missing.pop(name), {**radar, "upper": f"{base}/{radar['upper']}",
                                                        "lower": f"{base}/{radar['lower']}" if radar.get("lower") else None})
                if not missing:
                    break
        return maps

    def select(self):
        with self.lock:
            if self.busy():
                raise ValueError("Une sélection ou un export est déjà en cours.")
            self.picking = True
        try:
            chosen = pick_local_demos()
            if not chosen:
                return {"selection": self.selection}
            paths = tuple(dict.fromkeys(Path(name).resolve(strict=True) for name in chosen))
            if any(not path.is_file() or path.suffix.lower() not in (".dem", ".rar") for path in paths):
                raise ValueError("Sélectionne des fichiers .dem ou des archives .rar.")
            imports = read_demo_imports(self.output)
            sources = []
            for path in paths:
                try:
                    demos = inspect_demo_source(path, imports)
                except (OSError, ValueError, subprocess.SubprocessError):
                    if len(paths) == 1:
                        raise
                    # Batch exports report unreadable files individually.
                    demos = []
                sources.append({"name": path.name, "demos": demos})
            with self.lock:
                self.selected_paths = paths
                self.selection = {"id": uuid4().hex, "revision": 0, "sources": sources}
                return {"selection": self.selection}
        finally:
            with self.lock:
                self.picking = False

    def start(self, data):
        with self.lock:
            if self.busy():
                raise ValueError("Un export ou une sélection est déjà en cours.")
            if self.selection is None or data.get("selection") != self.selection["id"]:
                raise ValueError("Choisis d’abord une démo à exporter.")
            kind = data.get("kind")
            member = data.get("demo")
            mode = data.get("mode", "clips")
            keep_demo = data.get("keepDemo", True)
            if mode not in ("clips", "replay") or type(keep_demo) is not bool:
                raise ValueError("Mode d’import invalide.")
            limit = data.get("limit")
            capture_mode = data.get("captureMode", "potential")
            score_threshold = data.get("scoreThreshold")
            if mode == "clips" and (not isinstance(kind, str) or (kind != "all" and kind not in UTILITY_KINDS)):
                raise ValueError("Type d’utilitaire invalide.")
            if member is not None:
                sources = self.selection["sources"]
                if len(sources) != 1 or not any(demo["member"] == member for demo in sources[0]["demos"]):
                    raise ValueError("Cette démo ne fait pas partie de l’archive sélectionnée.")
            if limit is not None and (type(limit) is not int or limit < 1):
                raise ValueError("La limite de clips doit être un entier supérieur à zéro.")
            if capture_mode not in ("potential", "all", "top"):
                raise ValueError("Mode de capture invalide.")
            top_count = data.get("topCount") if capture_mode == "top" else None
            if capture_mode == "top" and (type(top_count) is not int or not 1 <= top_count <= 100):
                raise ValueError("Le nombre de clips du top doit être compris entre 1 et 100.")
            top_mixed = data.get("topMixed", False) is True and capture_mode == "top"
            enemy_pov = data.get("enemyPov", False)
            capture_height = data.get("captureHeight", 720)
            if type(enemy_pov) is not bool or capture_height not in CAPTURE_SIZES:
                raise ValueError("Options de capture invalides.")
            if score_threshold is not None and (capture_mode != "potential"
                                                or type(score_threshold) not in (float, int)
                                                or not 0 < score_threshold < 1):
                raise ValueError("Le seuil doit être compris entre 0 et 1, exclus.")
            paths = self.selected_paths
            queue = {
                (path, entry["member"]): {
                    "name": entry["member"].rsplit("/", 1)[-1] if entry["member"] else path.name,
                    "archive": path.name if entry["member"] else None,
                    "state": "queued",
                }
                for path, source in zip(paths, self.selection["sources"])
                for entry in source["demos"] or [{"member": None}]
                if member is None or entry["member"] == member
            }
            if mode == "replay":
                return self.start_replay_import(paths, member, keep_demo, queue)
            options = argparse.Namespace(demo=paths[0], output=self.output, csdm=self.csdm,
                                         type=kind, map=None, demo_member=member, limit=limit,
                                         prepare=False, no_open=True, all_clips=capture_mode == "all",
                                         score_threshold=score_threshold, keep_demo=keep_demo,
                                         top_count=top_count, top_mixed=top_mixed, enemy_pov=enemy_pov,
                                         capture_height=capture_height)

            def render_export(report):
                if len(paths) == 1:
                    export = generate(options, report, self.mark_demo_imported, self.update_queue)
                    result = {"failedFiles": int(bool(export.failed_demos))}
                    if export.gallery:
                        result["url"] = export_url(export.gallery.parent) + "/index.html"
                    return result
                return self.render_batch(paths, options, report)

            description = f"Sources · {', '.join(path.name for path in paths)}\n"
            description += f"Utilitaire · {kind} | Limite · {limit if limit is not None else 'aucune'}"
            description += f" | Démo conservée · {'oui' if keep_demo else 'non'}"
            description += f" | Capture · {capture_height}p | POV victime · {'oui' if enemy_pov else 'non'}"
            if top_count:
                description += f" | Top · {top_count}{' varié' if top_mixed else ''}"
            if capture_mode == "potential":
                description += f" | Seuil Potentiel favori · {score_threshold if score_threshold is not None else 'auto'}"
            if member is not None:
                description += f"\nDémo sélectionnée dans l’archive · {member}"
            return self.start_job(render_export, log_kind="export", description=description, controllable=True,
                                  queue=queue)

    def start_replay_import(self, paths, member, keep_demo, queue):
        def render_replays(report):
            completed, failures = import_replays(paths, self.output, find_csdm(self.csdm),
                                                 members=(member,) if member else (), keep_demo=keep_demo,
                                                 update=report, on_progress=self.update_queue)
            label = "replay créé" if completed == 1 else "replays créés"
            if not completed:
                raise ValueError("Aucun replay créé. Consulte le journal pour les détails.")
            if failures:
                report("Terminé avec erreurs", f"{completed} {label} · {failures} en erreur.")
            else:
                report("Terminé", f"{completed} {label}.")
            return {"failedFiles": failures, "replays": completed}

        description = f"Replays uniquement · {', '.join(path.name for path in paths)}"
        description += f" | Démo conservée · {'oui' if keep_demo else 'non'}"
        if member is not None:
            description += f"\nDémo sélectionnée dans l’archive · {member}"
        return self.start_job(render_replays, log_kind="replay", description=description, controllable=True,
                              queue=queue, mode="replay")

    def mark_demo_imported(self, path, member):
        with self.lock:
            sources = self.selection["sources"].copy()
            index = self.selected_paths.index(path)
            source = sources[index]
            demos = [{**demo, "imported": True} if demo["member"] == member else demo for demo in source["demos"]]
            if demos != source["demos"]:
                sources[index] = {**source, "demos": demos}
                self.selection = {**self.selection, "sources": sources, "revision": self.selection["revision"] + 1}

    def render_batch(self, paths, options, report):
        last_gallery = None
        errors = []
        self.output.mkdir(parents=True, exist_ok=True)
        with reserve_export(self.output):
            for index, path in enumerate(paths, 1):
                options.demo = path
                label = f"Fichier {index}/{len(paths)} · {path.name}"

                def report_file(phase, message):
                    report("Export des démos" if phase == "Terminé" else phase, f"{label} · {message}")

                try:
                    job_checkpoint()
                    export = generate_export(options, self.output, report_file, self.mark_demo_imported,
                                             self.update_queue)
                    last_gallery = export.gallery or last_gallery
                    if export.failed_demos:
                        errors.append(f"{label} · {export.failed_demos} {'démo en erreur' if export.failed_demos == 1 else 'démos en erreur'}.")
                except JobCancelled as error:
                    error.gallery = error.gallery or last_gallery
                    raise
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    errors.append(f"{label} · {error_message(error)}")
                    current_job_log().exception("%s · Échec du traitement", label)
                    with self.lock:
                        self.finish_queue("error", path)
                    report("Export des démos", errors[-1])

        if len(errors) == len(paths) and last_gallery is None:
            raise ValueError("Aucun fichier traité. " + errors[-1])
        if errors:
            successful = len(paths) - len(errors)
            report("Terminé avec erreurs", f"{successful} {'fichier terminé' if successful <= 1 else 'fichiers terminés'} "
                   f"sans erreur sur {len(paths)}. Consulte le journal pour les détails.")
        elif last_gallery:
            report("Terminé", f"{len(paths)} fichiers traités.")
        else:
            report("Terminé", "Aucun nouveau clip à générer pour les fichiers sélectionnés.")
        result = {"failedFiles": len(errors)}
        if last_gallery:
            result["url"] = export_url(last_gallery.parent) + "/index.html"
        return result

    def portrait_status(self, reference):
        with self.lock:
            _, clip_file = clip_paths(self.output, reference["exportId"], reference["file"])
            if not clip_file.is_file():
                raise ValueError("Ce clip n’est plus dans la vidéothèque.")
            folder = portrait_folder(clip_file)
            mine = self.job.get("portrait") == reference
            running = mine and self.job["state"] == "running"
            return {"exists": existing_portrait(folder) is not None, "busy": self.busy(), "running": running,
                    "phase": self.job.get("phase") if running else None, "error": self.job.get("error") if mine else None}

    def start_portrait(self, reference):
        with self.lock:
            clip_file, map_name, clip = find_library_clip(self.output, reference["exportId"], reference["file"])
            folder = portrait_folder(clip_file)
            if existing_portrait(folder):
                return {"exists": True}
            if self.busy():
                raise ValueError("Un export ou une sélection est déjà en cours.")

            def render_portrait(report):
                generate_portrait(clip_file, map_name, clip, folder, report)
                return {}

            self.start_job(render_portrait, log_kind="portrait",
                           description=f"Clip 9:16 · {clip_file.name} | Carte · {map_name}", portrait=reference)
            return {"exists": False}

    def compilation_status(self):
        with self.lock:
            mine = self.job.get("compilation") is True
            running = mine and self.job["state"] == "running"
            return {"busy": self.busy(), "running": running, "phase": self.job.get("messages", [""])[-1] if running else None,
                    "error": self.job.get("error") if mine else None,
                    "file": self.job.get("file") if mine and self.job["state"] == "complete" else None}

    def start_compilation(self, data):
        entries = data.get("clips")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CLIPS:
            raise ValueError(f"Choisis entre 1 et {MAX_CLIPS} clips.")
        with self.lock:
            if self.busy():
                raise ValueError("Un export ou une sélection est déjà en cours.")
            items = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Clip invalide.")
                clip_file, map_name, clip = find_library_clip(self.output, entry.get("exportId"), entry.get("file"))
                title = entry.get("title")
                title = title.strip()[:40] if isinstance(title, str) and title.strip() else "Lineup"
                items.append({"file": clip_file, "clip": clip, "map": map_name, "title": title,
                              "reference": {"exportId": entry.get("exportId"), "file": entry.get("file")}})
            description = data.get("description")
            description = description.strip()[:DESCRIPTION_MAX] if isinstance(description, str) else ""

            def render_compilation(report):
                target = generate_compilation(self.output, items, report, description)
                return {"file": target.name}

            return self.start_job(render_compilation, log_kind="compilation", controllable=True,
                                  description=f"Compilation 9:16 · {len(items)} clips", compilation=True)

    def open_compilations(self, name=None):
        folder = self.output / COMPILATION_FOLDER
        if not folder.is_dir():
            raise ValueError("Aucune compilation dans la vidéothèque.")
        if name:
            # Explorer opens the folder with this video selected.
            video = compilation_video(self.output, name)
            subprocess.Popen(["explorer", f"/select,{video}"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        os.startfile(folder, "explore")

    def youtube_status(self):
        with self.lock:
            mine = bool(self.job.get("youtube"))
            upload = {"file": self.job.get("youtube"), "running": self.job["state"] == "running",
                      "phase": (self.job.get("messages") or [""])[-1], "error": self.job.get("error"),
                      "url": (self.job.get("upload") or {}).get("url")} if mine else None
            busy = self.busy()
        return {**youtube_publish.status(), "busy": busy, "upload": upload}

    def start_youtube_upload(self, data):
        privacy = data.get("privacy", "private")
        with self.lock:
            if self.busy():
                raise ValueError("Un export ou une compilation est déjà en cours.")
            if not youtube_publish.status()["connected"]:
                raise ValueError("Connecte d’abord ta chaîne YouTube.")
            video, title, description = post_text(self.output, data.get("file"))
            name = video.name

            def upload(report):
                result = youtube_publish.upload_video(video, title, description, privacy, report)
                with self.lock:
                    record_upload(self.output, name, "youtube", result)
                return {"upload": result}

            return self.start_job(upload, log_kind="youtube", description=f"YouTube · {name} · {privacy}", youtube=name)

    def open_portrait(self, reference):
        with self.lock:
            clip_file, _, _ = find_library_clip(self.output, reference["exportId"], reference["file"])
            folder = portrait_folder(clip_file)
            if not existing_portrait(folder):
                raise ValueError("Aucune vidéo 9:16 dans ce dossier.")
            os.startfile(folder, "explore")

    def remove(self, reference):
        with self.lock:
            if self.job["state"] == "running" and self.job.get("portrait") == reference:
                raise ValueError("Attends la fin du rendu 9:16 avant de supprimer ce clip.")
            delete_clip(self.output, reference["exportId"], reference["file"])

    def update_queue(self, source, member, state, *, capture=None, **details):
        with self.lock:
            key = (source, member)
            row = self.queue.get(key)
            if row is None:
                return
            if capture is not None:
                self.capture_progress = (row, capture)
            elif self.capture_progress and self.capture_progress[0] is row:
                row.update(self.capture_progress[1].snapshot())
                self.capture_progress = None
            if "published" in details:
                count = details["published"]
                details["generated"] = max(row.get("generated", 0), count)
                if count:
                    details["captured"] = max(row.get("captured") or 0, count)
                gallery = details.pop("gallery", None)
                if gallery:
                    details["url"] = export_url(gallery.parent) + "/index.html"
            row.update(state=state, **details)

    def finish_queue(self, state, source=None):
        if self.capture_progress:
            row, capture = self.capture_progress
            row.update(capture.snapshot())
            self.capture_progress = None
        for (path, _), row in self.queue.items():
            if (source is None or source == path) and row["state"] not in QUEUE_FINISHED:
                row["state"] = state

    def job_status(self):
        status = self.job.copy()
        if self.job_control and status["state"] == "running":
            status.update(self.job_control.status())
        if self.capture_progress:
            row, capture = self.capture_progress
            row.update(capture.snapshot())
        if self.queue:
            status["queue"] = [row.copy() for row in self.queue.values()]
        return status

    def control_job(self, data):
        with self.lock:
            if (data.get("jobId") != self.job.get("id") or self.job["state"] != "running"
                    or self.job_control is None):
                raise ValueError("Ce traitement n’est plus actif.")
            self.job_control.request(data.get("action"))
            return self.job_status()

    def start_job(self, render, *, log_kind, description, controllable=False, queue=None, **details):
        job_id = uuid4().hex
        logger = create_job_log(self.output, log_kind, job_id)
        self.job_control = JobControl() if controllable else None
        self.queue = queue or {}
        self.capture_progress = None
        self.job = {"id": job_id, "state": "running", "phase": "Préparation", "messages": [],
                    "controllable": controllable, **details}
        threading.Thread(target=self.render, args=(render, logger, description), daemon=True).start()
        return self.job_status()

    def render(self, render, logger, description):
        def report(phase, message):
            logger.info("%s · %s", phase, message)
            with self.lock:
                self.job["phase"] = phase
                self.job["messages"] = [*self.job["messages"][-29:], message]

        with bind_job_log(logger):
            logger.info("Début du traitement\n%s", description)
            try:
                if self.job_control:
                    with self.job_control.bind():
                        result = render(report)
                else:
                    result = render(report)
            except JobCancelled as error:
                state = "cancelled"
                result = {"url": export_url(error.gallery.parent) + "/index.html"} if error.gallery else {}
                report("Annulé", "Traitement annulé. Les clips finalisés sont disponibles dans les galeries.")
            except Exception as error:
                state = "error"
                result = {"error": error_message(error)}
                logger.exception("Traitement interrompu · %s", result["error"])
            else:
                state = "complete"
                logger.info("Traitement terminé%s", " avec erreurs" if result.get("failedFiles") else "")
        with self.lock:
            self.finish_queue("skipped" if state == "complete" else state)
            self.job.update(state=state, **result)


def error_message(error):
    if isinstance(error, subprocess.CalledProcessError):
        output = error.stderr or error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        lines = [line for line in output.splitlines() if line.strip()]
        name = Path(error.cmd[0]).name if isinstance(error.cmd, (list, tuple)) else "Le programme"
        return f"{name} a quitté avec le code {error.returncode}. {' '.join(lines[-3:])}".strip()
    return str(error) or type(error).__name__


def clip_reference(data):
    return {"exportId": data.get("exportId"), "file": data.get("file")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Content-Security-Policy", CONTENT_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def json_response(self, data, status=HTTPStatus.OK, *, allow_gzip=False):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(body, "application/json", status, allow_gzip=allow_gzip)

    def send_bytes(self, body, content_type, status=HTTPStatus.OK, *, allow_gzip=False):
        compress = False
        if allow_gzip:
            for encoding in self.headers.get("Accept-Encoding", "").lower().split(","):
                name, _, quality = encoding.partition(";")
                if name.strip() == "gzip":
                    try:
                        compress = not quality or float(quality.strip().removeprefix("q=")) > 0
                    except ValueError:
                        pass
                    break
        if compress:
            body = gzip.compress(body, compresslevel=1)
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        if allow_gzip:
            self.send_header("Vary", "Accept-Encoding")
        if compress:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    def valid_host(self):
        return self.headers.get("Host") == self.server.host

    def do_GET(self):
        if not self.valid_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        url = urlsplit(self.path)
        path = unquote(url.path)
        app = self.server.app
        if path == "/api/status":
            with app.lock:
                status = {"app": APP_ID, "library": True, "portrait": True, "cacheCleanup": True,
                          "selection": app.selection, "job": app.job_status()}
            self.json_response(status)
        elif path == "/api/galleries":
            with app.lock:
                galleries = existing_galleries(app.output)
            self.json_response({"galleries": galleries})
        elif path == "/api/callout-maps":
            try:
                with app.lock:
                    maps = app.callout_maps()
                self.json_response({"maps": maps})
            except (ValueError, OSError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/player-heatmap/maps":
            maps = [{"map": name, "radar": radar, "rounds": sorted({number for rounds in records.values() for number in rounds})}
                    for name, (records, radar) in sorted(app.player_maps().items())]
            self.json_response({"maps": maps})
        elif path == "/api/player-heatmap/positions":
            params = parse_qs(url.query)
            name = params.get("map", [""])[0]
            number = params.get("round", ["0"])[0]
            try:
                if not re.fullmatch(r"[0-9]{1,2}", number) or int(number) > FIRST_HALF_ROUNDS:
                    raise ValueError("Choisis un round entre 1 et 12.")
                if not (match := app.player_maps().get(name)):
                    raise ValueError("Aucune position de joueur disponible pour cette carte.")
                records, radar = match
                self.json_response(player_heatmap(records, radar, int(number)), allow_gzip=True)
            except (ValueError, OSError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/replays":
            try:
                self.json_response(app.replays())
            except (ValueError, OSError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/replay":
            try:
                record = app.replay(parse_qs(url.query).get("id", [""])[0])
                self.json_response(record, allow_gzip=True)
            except (ValueError, OSError, EOFError, KeyError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/compilation":
            self.json_response(app.compilation_status())
        elif path == "/api/youtube/status":
            self.json_response(app.youtube_status())
        elif path == "/api/compilations":
            with app.lock:
                compilations = list_compilations(app.output)
            self.json_response({"compilations": compilations, "job": app.compilation_status()})
        elif path.startswith("/compilations/media/"):
            name = path.removeprefix("/compilations/media/")
            try:
                with app.lock:
                    if name.endswith(".jpg"):
                        file = compilation_thumbnail(app.output, name.removesuffix(".jpg"))
                    else:
                        file = compilation_video(app.output, name)
            except (ValueError, OSError, subprocess.SubprocessError):
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self.send_file(file)
        elif path == "/api/portrait":
            data = {key: values[0] for key, values in parse_qs(url.query).items()}
            try:
                self.json_response(app.portrait_status(clip_reference(data)))
            except (ValueError, OSError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/api/throw-index":
            name = parse_qs(url.query).get("map", [""])[0]
            try:
                clip_ids = set()
                for folder, _, catalogue in published_catalogues(app.output):
                    for match in playable_matches(folder, catalogue):
                        if match["map"] == name:
                            clip_ids.update(f"{folder.name}/{Path(clip['video']).name}" for clip in match["clips"])
                    for match in catalogue["matches"]:
                        if match["map"] == name:
                            clip_ids.update(f"{folder.name}/{clip['file']}" for clip in match.get("removedThrows", ())
                                            if "file" in clip)
                self.json_response({"throws": unrendered_throws(app.output, name, clip_ids)}, allow_gzip=True)
            except (ValueError, OSError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/library/catalogue.js":
            with app.lock:
                data = library_catalogue(app.output)
                installation = app.radar_installation() if any(match.get("throwIndex") and not match["radar"]
                                                               for match in data["matches"]) else None
                for match in data["matches"]:
                    if match.get("throwIndex") and not match["radar"]:
                        radar = radar_layout(match["map"], installation)
                        if radar:
                            match["radar"] = {**radar, "upper": f"/callout-radars/{radar['upper'].name}",
                                              "lower": f"/callout-radars/{radar['lower'].name}" if radar["lower"] else None}
                    if match["map"] in CALIBRATION:
                        try:
                            match["zones"] = read_zones(app.output, match["map"])
                        except (ValueError, OSError):
                            match["zones"] = []
            script = ("window.utilityLibrary = true;\nwindow.utilityPortrait = true;\nwindow.utilityClipState = true;\n"
                      "window.utilityCallouts = true;\nwindow.utilityClipCallout = true;\n"
                      f"{CATALOGUE_PREFIX}{json.dumps(data, ensure_ascii=True, separators=(',', ':'))};\n")
            self.send_bytes(script.encode("ascii"), "text/javascript", allow_gzip=True)
        else:
            file = (ROOT / "app" / STATIC[path]) if path in STATIC else self.gallery_file(path)
            if path.startswith("/library/") and path.removeprefix("/library/") in GALLERY_ASSETS:
                file = ROOT / "gallery" / path.removeprefix("/library/")
            elif path.startswith("/library/maps/") and MAP_EMBLEM.fullmatch(path.removeprefix("/library/maps/")):
                file = ROOT / "gallery" / "maps" / path.removeprefix("/library/maps/")
            elif path.startswith("/players/") and path.removeprefix("/players/") in PLAYER_ASSETS:
                file = ROOT / "players" / path.removeprefix("/players/")
            elif path.startswith("/replay/") and path.removeprefix("/replay/") in REPLAY_ASSETS:
                file = ROOT / "replay" / path.removeprefix("/replay/")
            elif path.startswith("/compilations/") and path.removeprefix("/compilations/") in COMPILATION_ASSETS:
                file = ROOT / "compilations" / path.removeprefix("/compilations/")
            elif path.startswith("/callout-radars/"):
                filename = path.removeprefix("/callout-radars/")
                name = filename.removesuffix(".png").removesuffix("_lower")
                if name in CALIBRATION and filename in (f"{name}.png", f"{name}_lower.png"):
                    radar = radar_layout(name, app.radar_installation())
                    file = radar and radar["lower" if filename.endswith("_lower.png") else "upper"]
            if file is None or not file.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
            elif file.name == "catalogue.js":
                try:
                    data = read_catalogue(file)
                    data["matches"] = list(playable_matches(file.parent, data))
                except (OSError, ValueError, KeyError, TypeError):
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    script = f"{CATALOGUE_PREFIX}{json.dumps(data, ensure_ascii=True)};\n"
                    self.send_bytes(script.encode("ascii"), "text/javascript")
            else:
                self.send_file(file)

    do_HEAD = do_GET

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != self.server.url:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        app = self.server.app
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 32768:
                raise ValueError("Requête invalide.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Requête invalide.")
            if self.path == "/api/select":
                self.json_response(app.select())
            elif self.path == "/api/generate":
                self.json_response({"job": app.start(data)}, HTTPStatus.ACCEPTED)
            elif self.path == "/api/job-control":
                self.json_response({"job": app.control_job(data)})
            elif self.path == "/api/portrait":
                self.json_response(app.start_portrait(clip_reference(data)))
            elif self.path == "/api/open-portrait":
                app.open_portrait(clip_reference(data))
                self.json_response({"opened": True})
            elif self.path == "/api/clip-state":
                with app.lock:
                    state = set_clip_state(app.output, data.get("exportId"), data.get("file"), data)
                self.json_response(state)
            elif self.path == "/api/callout-zones":
                name = data.get("map")
                if not isinstance(name, str) or name not in CALIBRATION:
                    raise ValueError("Cette carte ne prend pas en charge les zones de callout.")
                with app.lock:
                    result = change_zone(app.output, name, data)
                self.json_response(result)
            elif self.path == "/api/replays/delete":
                self.json_response(app.remove_replay(data))
            elif self.path == "/api/replays/reanalyze":
                self.json_response({"job": app.reanalyze_replay(data)}, HTTPStatus.ACCEPTED)
            elif self.path == "/api/compilation":
                self.json_response({"job": app.start_compilation(data)}, HTTPStatus.ACCEPTED)
            elif self.path == "/api/youtube/connect":
                self.json_response({"url": youtube_publish.start_connection()})
            elif self.path == "/api/youtube/disconnect":
                self.json_response(youtube_publish.disconnect())
            elif self.path == "/api/youtube/upload":
                self.json_response({"job": app.start_youtube_upload(data)}, HTTPStatus.ACCEPTED)
            elif self.path == "/api/compilations/update":
                with app.lock:
                    self.json_response({"compilation": update_compilation(app.output, data)})
            elif self.path == "/api/compilations/delete":
                with app.lock:
                    self.json_response(delete_compilation(app.output, data.get("file")))
            elif self.path == "/api/open-compilations":
                app.open_compilations(data.get("file"))
                self.json_response({"opened": True})
            elif self.path == "/api/delete-clip":
                app.remove(clip_reference(data))
                self.json_response({"deleted": True})
            elif self.path == "/api/quit":
                with app.lock:
                    if app.busy():
                        raise ValueError("Attends la fin de l’export avant de quitter.")
                self.json_response({"closed": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)

    def gallery_file(self, path):
        parts = path.split("/")
        if len(parts) not in (4, 5) or parts[1] != "exports" or not re.fullmatch(r"[\w-]+", parts[2]):
            return None
        output = self.server.app.output
        directory = output / parts[2]
        if len(parts) == 4 and parts[3] in GALLERY_FILES:
            file = directory / parts[3]
        elif len(parts) == 5 and parts[3] == "maps" and MAP_EMBLEM.fullmatch(parts[4]):
            return ROOT / "gallery" / "maps" / parts[4]
        elif len(parts) == 5 and (
            (parts[3] == "radars" and re.fullmatch(r"[\w-]+\.png", parts[4])) or
            (parts[3] == "clips" and re.fullmatch(r"[\w-]+\.mp4", parts[4])) or
            (parts[3] == THUMBNAIL_FOLDER and THUMBNAIL_NAME.fullmatch(parts[4]))
        ):
            file = directory / parts[3] / parts[4]
        else:
            return None
        resolved = file.resolve()
        if resolved.is_relative_to(directory) and directory.resolve().parent == output:
            if len(parts) == 5 and parts[3] == THUMBNAIL_FOLDER and not resolved.is_file():
                clip_file = directory / "clips" / f"{resolved.stem}.mp4"
                return create_thumbnail(clip_file) if clip_file.resolve().is_relative_to(directory) else None
            if len(parts) == 4 and parts[3] in GALLERY_ASSETS and (directory / "index.html").is_file():
                return ROOT / "gallery" / parts[3]
            return resolved
        return None

    def send_file(self, file):
        size = file.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if match and (match[1] or match[2]):
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                else:
                    start = max(0, size - int(match[2]))
            if not match or not (match[1] or match[2]) or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if file.suffix == ".js":
            content_type = "text/javascript"
        if content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.PARTIAL_CONTENT if requested else HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with file.open("rb") as content:
                content.seek(start)
                while length > 0:
                    block = content.read(min(length, 65536))
                    if not block:
                        break
                    self.wfile.write(block)
                    length -= len(block)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def serve(options):
    host = f"127.0.0.1:{options.port}"
    url = f"http://{host}"
    try:
        server = LocalServer(("127.0.0.1", options.port), Handler)
    except OSError as error:
        try:
            with urlopen(f"{url}/api/status", timeout=2) as response:
                already_running = json.load(response).get("app") == APP_ID
        except (URLError, OSError, ValueError):
            already_running = False
        if not already_running:
            raise error
        if not options.no_open:
            webbrowser.open(url)
        return
    with server:
        server.host = f"127.0.0.1:{server.server_port}"
        server.url = f"http://{server.host}"
        server.app = App(options.output.resolve(), options.csdm)
        if sys.stdout is not None:
            print(server.url, flush=True)
        if not options.no_open:
            webbrowser.open(server.url)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Ouvre l’interface locale Utilitaires CS2.")
    parser.add_argument("--port", type=int, default=38473)
    parser.add_argument("--output", type=Path, default=ROOT / "exports")
    parser.add_argument("--csdm", type=Path, help="Dossier d’installation de CS Demo Manager")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--pick-demo", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.pick_demo:
        print(json.dumps(choose_demos()))
        return 0
    try:
        serve(options)
    except KeyboardInterrupt:
        return 0
    except OSError as error:
        if sys.stderr is not None:
            print(error, file=sys.stderr)
        else:
            from tkinter import messagebox
            messagebox.showerror("Utilitaires CS2", str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
