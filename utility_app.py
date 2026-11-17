import argparse
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
from grenade_data import KINDS
from clip_library import (
    CATALOGUE_PREFIX, clip_paths, delete_clip, existing_galleries, export_url, find_library_clip, library_catalogue,
    set_clip_state,
)
from portrait_video import existing_portrait, generate_portrait, portrait_folder
from utility_video import (
    CALIBRATION, ROOT, find_csdm, generate, generate_export, inspect_demo_source, radar_layout, reserve_export, run,
)


APP_ID = "cs2-utility-videos"
STATIC = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js", "/utility.ico": "utility.ico"}
GALLERY_ASSETS = {"index.html", "gallery.css", "gallery.js", "callouts.js"}
GALLERY_FILES = GALLERY_ASSETS | {"catalogue.js"}
CONTENT_POLICY = ("default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'; "
                  "object-src 'none'; base-uri 'none'; form-action 'self'")


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
            limit = data.get("limit")
            if kind not in ("all", *KINDS.values()):
                raise ValueError("Type d’utilitaire invalide.")
            if member is not None:
                sources = self.selection["sources"]
                if len(sources) != 1 or not any(demo["member"] == member for demo in sources[0]["demos"]):
                    raise ValueError("Cette démo ne fait pas partie de l’archive sélectionnée.")
            if limit is not None and (type(limit) is not int or limit < 1):
                raise ValueError("La limite de clips doit être un entier supérieur à zéro.")
            paths = self.selected_paths
            options = argparse.Namespace(demo=paths[0], output=self.output, csdm=self.csdm,
                                         type=kind, map=None, demo_member=member, limit=limit,
                                         prepare=False, no_open=True)

            def render_export(report):
                if len(paths) == 1:
                    gallery = generate(options, report, self.mark_demo_imported)
                    return {"url": export_url(gallery.parent) + "/index.html"} if gallery else {}
                return self.render_batch(paths, options, report)

            return self.start_job(render_export)

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
                    gallery = generate_export(options, self.output, report_file, self.mark_demo_imported)
                    last_gallery = gallery or last_gallery
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    errors.append(f"{label} · {error_message(error)}")
                    report("Export des démos", errors[-1])

        if len(errors) == len(paths):
            raise ValueError("Aucun fichier traité. " + errors[-1])
        if errors:
            report("Terminé avec erreurs", f"{len(paths) - len(errors)} fichiers traités sur {len(paths)}. Consulte le journal pour les erreurs.")
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

            self.start_job(render_portrait, portrait=reference)
            return {"exists": False}

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

    def start_job(self, render, **details):
        self.job = {"id": uuid4().hex, "state": "running", "phase": "Préparation", "messages": [], **details}
        threading.Thread(target=self.render, args=(render,), daemon=True).start()
        return self.job.copy()

    def render(self, render):
        def report(phase, message):
            with self.lock:
                self.job["phase"] = phase
                self.job["messages"] = [*self.job["messages"][-29:], message]

        try:
            result = render(report)
            with self.lock:
                self.job.update(state="complete", **result)
        except Exception as error:
            with self.lock:
                self.job.update(state="error", error=error_message(error))


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

    def json_response(self, data, status=HTTPStatus.OK):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json", status)

    def send_bytes(self, body, content_type, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
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
                          "selection": app.selection, "job": app.job.copy()}
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
        elif path == "/api/portrait":
            data = {key: values[0] for key, values in parse_qs(url.query).items()}
            try:
                self.json_response(app.portrait_status(clip_reference(data)))
            except (ValueError, OSError) as error:
                self.json_response({"error": error_message(error)}, HTTPStatus.BAD_REQUEST)
        elif path == "/library/catalogue.js":
            with app.lock:
                data = library_catalogue(app.output)
                for match in data["matches"]:
                    if match["map"] in CALIBRATION:
                        try:
                            match["zones"] = read_zones(app.output, match["map"])
                        except (ValueError, OSError):
                            match["zones"] = []
            script = ("window.utilityLibrary = true;\nwindow.utilityPortrait = true;\nwindow.utilityClipState = true;\n"
                      "window.utilityCallouts = true;\n"
                      f"{CATALOGUE_PREFIX}{json.dumps(data, ensure_ascii=True)};\n")
            self.send_bytes(script.encode("ascii"), "text/javascript")
        else:
            file = (ROOT / "app" / STATIC[path]) if path in STATIC else self.gallery_file(path)
            if path.startswith("/library/") and path.removeprefix("/library/") in GALLERY_ASSETS:
                file = ROOT / "gallery" / path.removeprefix("/library/")
            elif path.startswith("/callout-radars/"):
                filename = path.removeprefix("/callout-radars/")
                name = filename.removesuffix(".png").removesuffix("_lower")
                if name in CALIBRATION and filename in (f"{name}.png", f"{name}_lower.png"):
                    radar = radar_layout(name, app.radar_installation())
                    file = radar and radar["lower" if filename.endswith("_lower.png") else "upper"]
            if file is None or not file.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
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
            if self.headers.get_content_type() != "application/json" or not 0 < length <= 8192:
                raise ValueError("Requête invalide.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Requête invalide.")
            if self.path == "/api/select":
                self.json_response(app.select())
            elif self.path == "/api/generate":
                self.json_response({"job": app.start(data)}, HTTPStatus.ACCEPTED)
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
        elif len(parts) == 5 and (
            (parts[3] == "radars" and re.fullmatch(r"[\w-]+\.png", parts[4])) or
            (parts[3] == "clips" and re.fullmatch(r"[\w-]+\.mp4", parts[4]))
        ):
            file = directory / parts[3] / parts[4]
        else:
            return None
        resolved = file.resolve()
        if resolved.is_relative_to(directory) and directory.resolve().parent == output:
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
