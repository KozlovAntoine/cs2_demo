import hashlib
import struct
from tempfile import NamedTemporaryFile
from pathlib import Path


CAPTURE_PAUSE_MS = 500
_ORIGINAL_SHA256 = "bdf4abb58608a400e5798f397731dfd9953931bfca02a0d5d5f188a41ba2e27c"
_FAST_SHA256 = "4ac904b9be74ff1db807b33926ed89711315677ca561af3806d95cd255a147fe"
_PAUSE_OFFSET = 0xAF854


def shorten_capture_pause(installation):
    """Apply the tested delay to the default Windows plugin in a project-local CSDM copy."""
    plugin = installation / "resources" / "static" / "cs2" / "server.dll"
    if plugin.resolve() != plugin or not plugin.is_file():
        return False
    binary = plugin.read_bytes()
    digest = hashlib.sha256(binary).hexdigest()
    if digest == _FAST_SHA256:
        return True
    if digest != _ORIGINAL_SHA256:
        return False

    # CS Demo Manager 3.21.0 x64: milliseconds in ProcessSequences, between pause and resume.
    # Match the full DLL and the resulting build before replacing the project-local plugin.
    patched = binary[:_PAUSE_OFFSET] + struct.pack("<I", CAPTURE_PAUSE_MS) + binary[_PAUSE_OFFSET + 4:]
    if hashlib.sha256(patched).hexdigest() != _FAST_SHA256:
        return False

    with NamedTemporaryFile(dir=plugin.parent, prefix="capture-pause-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(patched)
        temporary.replace(plugin)
    finally:
        temporary.unlink(missing_ok=True)
    return True
