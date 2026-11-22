import gzip
import json
import re
from collections import defaultdict
from uuid import uuid4

from grenade_data import UTILITY_KINDS


INDEX_FOLDER = ".throw-index"
MAP_NAME = re.compile(r"[a-z0-9_]+")
DEMO_ID = re.compile(r"[0-9a-f]{1,16}")
THROW_FIELDS = ("kind", "player", "steamId", "team", "side", "round", "launch", "impact", "launchTick",
                "activationTick", "roundLaunchSeconds", "roundActivationSeconds", "estimated", "potentialScore")


def record_throw_index(output, map_name, checksum, demo, throws, kinds, occurrences, references=(), removed=()):
    if not MAP_NAME.fullmatch(map_name) or not DEMO_ID.fullmatch(checksum):
        raise ValueError("Identifiant invalide pour l’index des lancers.")
    directory = output / INDEX_FOLDER / map_name
    directory.mkdir(parents=True, exist_ok=True)
    linked = defaultdict(set)
    for representative, clip_id in references:
        for throw in occurrences.get(id(representative), ()):
            linked[id(throw)].add(clip_id)
    hidden = {id(throw) for representative in removed for throw in occurrences.get(id(representative), ())}
    by_kind = defaultdict(list)
    for throw in throws:
        row = {field: throw[field] for field in THROW_FIELDS if field in throw}
        row["source"] = demo
        if refs := linked.get(id(throw)):
            row["clips"] = sorted(refs)
        if id(throw) in hidden:
            row["removed"] = True
        by_kind[throw["kind"]].append(row)
    for kind in kinds:
        if kind not in UTILITY_KINDS:
            continue
        temporary = directory / f".{uuid4().hex}.tmp"
        try:
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as target:
                json.dump(by_kind[kind], target, ensure_ascii=True, separators=(",", ":"))
            temporary.replace(directory / f"{checksum}-{kind}.json.gz")
        finally:
            temporary.unlink(missing_ok=True)


def indexed_maps(output):
    return {directory.name for directory in (output / INDEX_FOLDER).glob("*")
            if MAP_NAME.fullmatch(directory.name) and directory.resolve() == directory
            and any(directory.glob("*.json.gz"))}


def unrendered_throws(output, map_name, clip_ids):
    if not MAP_NAME.fullmatch(map_name):
        raise ValueError("Carte invalide.")
    directory = output / INDEX_FOLDER / map_name
    rows = []
    for path in sorted(directory.glob("*.json.gz")):
        if path.resolve() != path:
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                data = json.load(source)
        except (OSError, ValueError, EOFError):
            continue
        rows.extend({key: value for key, value in row.items() if key not in ("clips", "removed")}
                    for row in data if not row.get("removed") and clip_ids.isdisjoint(row.get("clips", ())))
    return rows
