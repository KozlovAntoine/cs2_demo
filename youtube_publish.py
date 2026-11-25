"""YouTube Shorts upload for compilations.

Connection: OAuth 2.0 for an installed app with PKCE and a one-shot loopback listener on 127.0.0.1.
Upload: YouTube Data API v3 resumable upload. Nothing secret is printed or logged.
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from utility_video import ROOT


FOLDER = ROOT / ".local" / "youtube"
CLIENT_FILE = FOLDER / "client_secret.json"
TOKEN_FILE = FOLDER / "token.json"
# Upload, plus read-only access to show which channel is connected.
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly")
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
CHANNEL_URL = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"
CHUNK = 8 * 1024 * 1024
AUTH_TIMEOUT = 300
GAMING_CATEGORY = "20"
PRIVACY = {"private", "unlisted", "public"}

_lock = threading.Lock()
_pending = {"active": False, "error": None}


def read_client():
    try:
        data = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))["installed"]
        return {key: data[key] for key in ("client_id", "client_secret", "auth_uri", "token_uri")}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("Fichier client YouTube absent ou invalide : .local/youtube/client_secret.json") from error


def read_token():
    try:
        token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return token if isinstance(token, dict) and token.get("refresh_token") else None


def write_token(token):
    FOLDER.mkdir(parents=True, exist_ok=True)
    temporary = TOKEN_FILE.with_name(f".{TOKEN_FILE.name}.tmp")
    temporary.write_text(json.dumps(token, indent=2), encoding="utf-8")
    temporary.replace(TOKEN_FILE)


def google_error(error):
    try:
        data = json.loads(error.read().decode("utf-8"))
    except (ValueError, OSError):
        return f"Google a répondu {error.code}."
    detail = data.get("error")
    if isinstance(detail, dict):
        reasons = [item.get("reason") for item in detail.get("errors", ()) if isinstance(item, dict)]
        message = detail.get("message") or ""
        return f"Google a refusé ({error.code}) · {message} {' '.join(filter(None, reasons))}".strip()
    return f"Google a refusé ({error.code}) · {data.get('error_description') or detail}"


def post_form(url, fields):
    request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("ascii"),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ValueError(google_error(error)) from None


def access_token():
    token = read_token()
    if not token:
        raise ValueError("YouTube n’est pas connecté.")
    if token.get("access_token") and token.get("expires_at", 0) > time.time() + 60:
        return token["access_token"]
    client = read_client()
    fresh = post_form(client["token_uri"], {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    })
    token.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)))
    write_token(token)
    return token["access_token"]


def api_get(url):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token()}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ValueError(google_error(error)) from None


def fetch_channel():
    items = api_get(CHANNEL_URL).get("items") or []
    if not items:
        raise ValueError("Ce compte Google n’a pas de chaîne YouTube.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def status():
    with _lock:
        pending = dict(_pending)
    token = read_token()
    return {"configured": CLIENT_FILE.is_file(), "connected": bool(token),
            "channel": token.get("channel") if token else None,
            "pending": pending["active"], "error": pending["error"]}


def disconnect():
    TOKEN_FILE.unlink(missing_ok=True)
    return status()


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _Callback(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if query.get("state", [""])[0] != self.server.state:
            self.send_response(400)
            self.end_headers()
            return
        self.server.result = {"code": query.get("code", [None])[0], "error": query.get("error", [None])[0]}
        ok = bool(self.server.result["code"])
        body = ("<!doctype html><meta charset='utf-8'><title>YouTube</title>"
                "<body style='font:16px system-ui;background:#0f0f11;color:#ececef;padding:40px'>"
                f"<h1>{'YouTube est connecté' if ok else 'Connexion annulée'}</h1>"
                "<p>Tu peux fermer cet onglet et revenir à l’app.</p></body>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_connection():
    """Returns the Google consent URL. A background thread waits for the browser to come back."""
    client = read_client()
    with _lock:
        if _pending["active"]:
            raise ValueError("Une connexion YouTube est déjà en attente dans le navigateur.")
        _pending.update(active=True, error=None)
    server = HTTPServer(("127.0.0.1", 0), _Callback)
    server.state = secrets.token_urlsafe(24)
    server.result = None
    server.timeout = 1
    redirect = f"http://127.0.0.1:{server.server_port}"
    verifier, challenge = _pkce()
    url = client["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "state": server.state, "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent select_account",
    })

    def wait():
        error = None
        try:
            deadline = time.time() + AUTH_TIMEOUT
            while server.result is None and time.time() < deadline:
                server.handle_request()
            result = server.result
            if not result:
                raise ValueError("Connexion YouTube expirée : relance-la.")
            if not result["code"]:
                raise ValueError("Connexion YouTube refusée dans le navigateur.")
            token = post_form(client["token_uri"], {
                "client_id": client["client_id"], "client_secret": client["client_secret"], "code": result["code"],
                "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
            })
            if not token.get("refresh_token"):
                raise ValueError("Google n’a pas fourni d’accès durable. Relance la connexion.")
            stored = {"refresh_token": token["refresh_token"], "access_token": token["access_token"],
                      "expires_at": time.time() + int(token.get("expires_in", 3600)), "scope": token.get("scope"),
                      "connectedAt": datetime.now().astimezone().isoformat(timespec="seconds")}
            write_token(stored)
            stored["channel"] = fetch_channel()
            write_token(stored)
        except Exception as caught:  # Reported to the page; the token stays absent.
            error = str(caught) or "Connexion YouTube impossible."
        finally:
            server.server_close()
            with _lock:
                _pending.update(active=False, error=error)

    threading.Thread(target=wait, daemon=True).start()
    return url


def clean_text(value, limit):
    # YouTube rejects angle brackets in titles and descriptions.
    return re.sub(r"[<>]", "", value or "").strip()[:limit]


def upload_video(video, title, description, privacy, report):
    if privacy not in PRIVACY:
        raise ValueError("Visibilité inconnue.")
    title = clean_text(title, 100) or video.stem[:100]
    description = clean_text(description, 5000)
    tags = list(dict.fromkeys(re.findall(r"#(\w+)", description)))[:15]
    metadata = {
        "snippet": {"title": title, "description": description, "categoryId": GAMING_CATEGORY, "tags": tags},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    size = video.stat().st_size
    report("YouTube", "Ouverture de l’envoi…")
    request = urllib.request.Request(UPLOAD_URL, data=json.dumps(metadata).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {access_token()}", "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size),
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            session = response.headers["Location"]
    except urllib.error.HTTPError as error:
        raise ValueError(google_error(error)) from None
    sent = 0
    with video.open("rb") as source:
        while True:
            source.seek(sent)
            block = source.read(CHUNK)
            end = sent + len(block) - 1
            put = urllib.request.Request(session, data=block, method="PUT", headers={
                "Authorization": f"Bearer {access_token()}", "Content-Length": str(len(block)),
                "Content-Range": f"bytes {sent}-{end}/{size}",
            })
            try:
                with urllib.request.urlopen(put, timeout=300) as response:
                    result = json.loads(response.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as error:
                if error.code != 308:
                    raise ValueError(google_error(error)) from None
                # 308 Resume Incomplete: Google says how much it kept.
                received = re.fullmatch(r"bytes=0-(\d+)", error.headers.get("Range") or "")
                sent = int(received[1]) + 1 if received else 0
            report("YouTube", f"Envoi {round(100 * sent / size)} % · {sent // 1_000_000} / {size // 1_000_000} Mo")
    video_id = result["id"]
    report("YouTube", "Vidéo reçue par YouTube, traitement en cours.")
    return {"id": video_id, "url": f"https://youtube.com/shorts/{video_id}",
            "privacy": result.get("status", {}).get("privacyStatus", privacy),
            "uploadedAt": datetime.now().astimezone().isoformat(timespec="seconds")}
