"""Install the latest HLAE release next to the project and point CS Demo Manager to it.

CS2 updates regularly break HLAE ("Failed to detour ... functions"). The advancedfx team then publishes a fixed
release within hours. Run this script, or double-click « Mettre à jour HLAE.cmd », to install it.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
RELEASES = "https://api.github.com/repos/advancedfx/advancedfx/releases/latest"
SETTINGS = Path.home() / ".csdm" / "settings.json"
USER_AGENT = "cs2-utility-hlae-updater"
ASSET = re.compile(r"hlae_(\d+)_(\d+)_(\d+)\.zip")


def fetch(url, accept="application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return urllib.request.urlopen(request, timeout=60)


def latest_release():
    with fetch(RELEASES) as response:
        release = json.load(response)
    for asset in release.get("assets", ()):
        if match := ASSET.fullmatch(asset.get("name", "")):
            version = ".".join(match.groups())
            return version, asset, release.get("body", "")
    raise RuntimeError("Aucune archive HLAE dans la dernière version publiée.")


def installed_version(executable):
    changelog = Path(executable).parent / "changelog.xml"
    try:
        found = re.search(r"<version>([\d.]+)</version>", changelog.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return found[1] if found else None


def version_key(version):
    return tuple(int(part) for part in version.split(".")) if version else ()


def read_settings():
    if not SETTINGS.is_file():
        raise RuntimeError(f"Réglages de CS Demo Manager introuvables : {SETTINGS}. Ouvre CS Demo Manager une fois.")
    data = SETTINGS.read_bytes()
    settings = json.loads(data)
    return data, settings["video"]["hlae"]


def running(name):
    result = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"], capture_output=True, text=True,
                            errors="replace")
    return name.lower() in result.stdout.lower()


def download(asset, folder):
    target = folder / asset["name"]
    with fetch(asset["browser_download_url"], "application/octet-stream") as response, target.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    if target.stat().st_size != asset["size"]:
        raise RuntimeError("Téléchargement incomplet : la taille ne correspond pas à celle publiée.")
    return target


def extract(archive, destination):
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            path = (destination / member).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise RuntimeError(f"Chemin suspect dans l’archive : {member}")
        bundle.extractall(destination)
    for required in ("hlae.exe", "x64/AfxHookSource2.dll"):
        if not (destination / required).is_file():
            raise RuntimeError(f"Fichier manquant après extraction : {required}")


def point_csdm_to(executable, data):
    text = data.decode("utf-8")
    # Edit only the "hlae" object so the rest of the file keeps its exact bytes. FFmpeg has the same key names.
    blocks = list(re.finditer(r'"hlae"\s*:\s*\{[^{}]*\}', text))
    if len(blocks) != 1:
        raise RuntimeError("Format inattendu dans les réglages de CS Demo Manager. Rien n’a été modifié.")
    block = blocks[0]
    edited = re.sub(r'("customLocationEnabled"\s*:\s*)(true|false)', r"\1true", block[0], count=1)
    edited = re.sub(r'("customExecutableLocation"\s*:\s*)"(?:[^"\\]|\\.)*"',
                    lambda match: match[1] + json.dumps(str(executable)), edited, count=1)
    updated = (text[:block.start()] + edited + text[block.end():]).encode("utf-8")
    result = json.loads(updated)["video"]["hlae"]
    if result.get("customExecutableLocation") != str(executable) or result.get("customLocationEnabled") is not True:
        raise RuntimeError("Les réglages de CS Demo Manager n’ont pas pu être modifiés. Rien n’a été modifié.")
    backup = SETTINGS.with_name(f"settings.json.bak-{datetime.now():%Y%m%d-%H%M%S}")
    backup.write_bytes(data)
    SETTINGS.write_bytes(updated)
    return backup


def main():
    parser = argparse.ArgumentParser(description="Met à jour HLAE pour CS Demo Manager.")
    parser.add_argument("--check", action="store_true", help="Affiche les versions sans rien installer")
    parser.add_argument("--force", action="store_true", help="Réinstalle même si la version est déjà à jour")
    args = parser.parse_args()

    data, hlae = read_settings()
    current_path = hlae.get("customExecutableLocation") if hlae.get("customLocationEnabled") else None
    current = installed_version(current_path) if current_path else None
    print(f"HLAE utilisé par CS Demo Manager : {current or 'inconnu'}" + (f" ({current_path})" if current_path else ""))
    print("Recherche de la dernière version sur GitHub (advancedfx/advancedfx)…")
    version, asset, notes = latest_release()
    print(f"Dernière version publiée : {version}")
    for line in notes.splitlines()[:6]:
        if line.strip():
            print(f"  {line.strip()}")

    destination = LOCAL / f"hlae-{version}"
    if not args.force and version_key(current) >= version_key(version) and Path(current_path or "").is_file():
        print("\nHLAE est déjà à jour. Si CS2 affiche encore une erreur, la correction n’est peut-être pas encore publiée.")
        return 0
    if args.check:
        print(f"\nUne mise à jour est disponible. Relance sans --check pour l’installer dans {destination}.")
        return 0
    if running("cs2.exe"):
        raise RuntimeError("CS2 est ouvert. Ferme le jeu, puis relance la mise à jour.")

    LOCAL.mkdir(exist_ok=True)
    if not (destination / "hlae.exe").is_file():
        print(f"\nTéléchargement de {asset['name']} ({asset['size'] / 1e6:.1f} Mo)…")
        with tempfile.TemporaryDirectory(prefix="hlae-download-", dir=LOCAL) as folder:
            archive = download(asset, Path(folder))
            staging = Path(folder) / "extract"
            extract(archive, staging)
            shutil.move(str(staging), destination)
        print(f"Installé dans {destination}")
    else:
        print(f"\nHLAE {version} est déjà présent dans {destination}")

    backup = point_csdm_to(destination / "hlae.exe", data)
    print(f"CS Demo Manager utilise maintenant HLAE {version}.")
    print(f"Ancien réglage sauvegardé : {backup.name}")
    if current_path and Path(current_path).parent != destination:
        print(f"L’ancienne version reste dans {Path(current_path).parent} : tu peux la supprimer si tout fonctionne.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"\nÉchec : {error}", file=sys.stderr)
        sys.exit(1)
