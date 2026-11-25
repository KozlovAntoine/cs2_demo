"""Shows a file in Windows Explorer, in front of the browser that asked for it."""

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path


WAIT_SECONDS = 4
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x2


def explorer_windows(title):
    """Explorer windows whose title is the folder name."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(window, _):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(window, name, 64)
        if name.value == "CabinetWClass" and user32.IsWindowVisible(window):
            text = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(window, text, 512)
            # "Folder : Explorateur de fichiers", "Folder - File Explorer"… depending on the Windows language.
            if text.value == title or text.value.startswith(f"{title} "):
                found.append(window)
        return True

    user32.EnumWindows(visit, 0)
    return found


def bring_forward(window):
    user32 = ctypes.windll.user32
    if user32.IsIconic(window):
        user32.ShowWindow(window, SW_RESTORE)
    # Windows only lets the foreground app hand the focus over; a tap of Alt counts as user input and lifts that lock.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(window)


def show_in_explorer(file):
    file = Path(file).resolve()
    before = set(explorer_windows(file.parent.name)) if os.name == "nt" else set()
    # A string, not a list: Explorer only reads /select when the quotes wrap the path alone.
    subprocess.Popen(f'explorer /select,"{file}"', creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if os.name != "nt":
        return

    def focus():
        started = time.monotonic()
        while time.monotonic() - started < WAIT_SECONDS:
            time.sleep(0.15)
            windows = explorer_windows(file.parent.name)
            target = next((window for window in windows if window not in before), None)
            # Explorer may reuse a window already open on this folder instead of opening a new one.
            if target is None and windows and time.monotonic() - started > 1:
                target = windows[0]
            if target:
                # A window still opening can lose the focus again: retry until it holds.
                bring_forward(target)
                time.sleep(0.2)
                if ctypes.windll.user32.GetForegroundWindow() == target:
                    return

    threading.Thread(target=focus, daemon=True).start()
