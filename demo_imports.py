import hashlib
import json
import zlib
from uuid import uuid4


INDEX_NAME = ".demo-imports.json"


def demo_fingerprint(path):
    digest = hashlib.sha256()
    crc = 0
    size = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
            crc = zlib.crc32(block, crc)
            size += len(block)
    return digest.hexdigest(), [size, f"{crc:08X}"]


def read_demo_imports(output):
    try:
        return json.loads((output / INDEX_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def record_demo_import(output, imports, digest, file_info):
    # The caller holds the export lock, so each update includes all prior imports.
    if digest in imports:
        return
    temporary = output / f".demo-imports-{uuid4().hex}.tmp"
    imports[digest] = file_info
    try:
        temporary.write_text(json.dumps(imports, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / INDEX_NAME)
    finally:
        temporary.unlink(missing_ok=True)
