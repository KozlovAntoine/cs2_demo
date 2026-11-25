"""A standard static green crosshair during captures, without touching the player's own CS2 settings.

The capture changes crosshair convars through the sequence cfg. CS2 saves convars to the Steam user config when
it closes, so the config files are copied before the capture and put back once CS2 has exited. A copy left by an
interrupted capture is restored before the next one.
"""

import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from job_log import current_job_log


ROOT = Path(__file__).resolve().parent


BACKUP = ROOT / ".local" / "cs2-config-backup"
CONFIG_NAMES = ("cs2_user_convars_0_slot0.vcfg", "cs2_user_convars_0_slot0.vcfg_lastclouded")
# Static, thin, green, with an outline: readable on every map.
CROSSHAIR_COMMANDS = (
    "cl_show_observer_crosshair 0",
    "cl_crosshairstyle 4",
    "cl_crosshaircolor 1",
    "cl_crosshairsize 2.5",
    "cl_crosshairthickness 0.6",
    "cl_crosshairgap -1.5",
    "cl_crosshairdot 0",
    "cl_crosshair_t 0",
    "cl_crosshairalpha 255",
    "cl_crosshairusealpha 1",
    "cl_crosshair_drawoutline 1",
    "cl_crosshair_outlinethickness 1",
    "cl_crosshairgap_useweaponvalue 0",
    "cl_crosshair_recoil 0",
    # Server announcements and chat from the match stay out of the clips (cl_chatfilters has no effect in CS2).
    "sv_cheats 1",
    "hidehud 128",
)


def steam_folder():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except (ImportError, OSError):
        folder = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
        return folder if folder.is_dir() else None


def config_files():
    steam = steam_folder()
    if not steam:
        return []
    return [file for file in (steam / "userdata").glob(f"*/730/local/cfg/cs2_user_convars_*")
            if file.name in CONFIG_NAMES and file.is_file()]


def cs2_running():
    try:
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq cs2.exe", "/NH"], capture_output=True, text=True,
                                timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return "cs2.exe" in result.stdout.lower()


def wait_for_cs2_exit(timeout=90):
    deadline = time.time() + timeout
    while cs2_running() and time.time() < deadline:
        time.sleep(1)
    # CS2 writes its config while it closes.
    time.sleep(2)
    return not cs2_running()


def restore_backup():
    """Puts back the config files saved before a capture. Returns False while CS2 still runs."""
    manifest = BACKUP / "manifest.json"
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if cs2_running() and not wait_for_cs2_exit():
        return False
    for entry in entries:
        copy = BACKUP / entry["copy"]
        if copy.is_file():
            shutil.copy2(copy, entry["path"])
    shutil.rmtree(BACKUP, ignore_errors=True)
    if logger := current_job_log():
        logger.info("Viseur · configuration CS2 restaurée (%d fichiers).", len(entries))
    return True


@contextmanager
def standard_crosshair():
    if not restore_backup():
        raise ValueError("CS2 est encore ouvert : ferme-le avant de lancer une capture.")
    files = config_files()
    BACKUP.mkdir(parents=True, exist_ok=True)
    entries = []
    for number, file in enumerate(files):
        copy = f"{number}-{file.name}"
        shutil.copy2(file, BACKUP / copy)
        entries.append({"path": str(file), "copy": copy})
    (BACKUP / "manifest.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
    try:
        yield
    finally:
        if not restore_backup() and (logger := current_job_log()):
            logger.warning("Viseur · CS2 encore ouvert, configuration restaurée à la prochaine capture.")
