import gzip
import json
import os
import re
import shutil
from uuid import uuid4

from round_replay import (
    BURST_SECONDS, INFERNO_SECONDS, REPLAY_FOLDER, REPLAY_VERSION, SMOKE_SECONDS, SUMMARY_VERSION, write_replay_meta,
)
from throw_index import INDEX_FOLDER


CHECKSUM = re.compile(r"[0-9a-f]{1,16}")
MAP_NAME = re.compile(r"[a-z0-9_]+")


def replay_directory(output):
    return output / REPLAY_FOLDER


def replay_paths(output, checksum):
    if not isinstance(checksum, str) or not CHECKSUM.fullmatch(checksum):
        raise ValueError("Replay introuvable.")
    directory = replay_directory(output)
    return {name: directory / f"{checksum}{suffix}"
            for name, suffix in (("record", ".json.gz"), ("meta", ".meta.json"), ("demo", ".dem"))}


def save_replay_demo(output, demo, checksum):
    """Keep the analysed demo next to its replay. A hard link avoids a second copy on the same disk."""
    target = replay_paths(output, checksum)["demo"]
    if target.is_file() and target.stat().st_size == demo.stat().st_size:
        return target
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_name(f".{uuid4().hex}.tmp")
    try:
        try:
            os.link(demo, temporary)
        except OSError:
            shutil.copyfile(demo, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_record(path):
    with gzip.open(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def indexed_teams(output, map_name, checksum):
    teams = {}
    directory = output / INDEX_FOLDER / map_name
    for path in directory.glob(f"{checksum}-*.json.gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                rows = json.load(source)
        except (OSError, ValueError, EOFError):
            continue
        for row in rows:
            if row.get("team"):
                teams[row["team"]] = teams.get(row["team"], 0) + 1
    return [{"name": name} for name in sorted(teams, key=teams.get, reverse=True)[:2]]


def summary_for(output, checksum, paths):
    """Return the listing summary of a replay. Older relevés gain a cached summary on first read."""
    try:
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if meta.get("summaryVersion") == SUMMARY_VERSION:
        return meta
    record = load_record(paths["record"])
    rounds = [stage["number"] for stage in record["rounds"] if stage.get("sides")]
    summary = {"map": record["map"], "rounds": rounds, "version": record.get("version", 1),
               "recordVersion": record.get("version", 1), "demo": record.get("demo", ""),
               "players": len(record.get("players", {})), "summaryVersion": SUMMARY_VERSION}
    if record.get("date"):
        summary["date"] = record["date"]
    teams = record.get("teams")
    if not teams and MAP_NAME.fullmatch(record["map"]):
        teams = indexed_teams(output, record["map"], checksum)
    if teams:
        summary["teams"] = teams
    write_replay_meta(paths["record"].parent, checksum, summary)
    return summary


def list_replays(output):
    directory = replay_directory(output)
    replays = []
    for record in sorted(directory.glob("*.json.gz")):
        checksum = record.name.removesuffix(".json.gz")
        if not CHECKSUM.fullmatch(checksum) or record.resolve() != record:
            continue
        paths = replay_paths(output, checksum)
        try:
            summary = summary_for(output, checksum, paths)
            stat = record.stat()
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            continue
        demo_size = paths["demo"].stat().st_size if paths["demo"].is_file() else 0
        replays.append({
            "id": checksum, "map": summary["map"], "demo": summary.get("demo", ""), "date": summary.get("date"),
            "importedAt": stat.st_mtime, "teams": summary.get("teams", []), "rounds": len(summary.get("rounds", [])),
            "version": summary.get("version", 1), "current": summary.get("version", 1) >= REPLAY_VERSION,
            "simplified": summary.get("version", 1) < 3,
            "replaySize": stat.st_size, "demoSize": demo_size, "demoSaved": bool(demo_size),
        })
    replays.sort(key=lambda replay: replay.get("date") or "", reverse=True)
    return replays


def indexed_grenades(output, record):
    """Approximate grenades for relevés made before version 3, from the utility index."""
    map_name, checksum, tick_rate = record["map"], record["checksum"], record["tickRate"]
    if not MAP_NAME.fullmatch(map_name):
        return {}
    rounds = {stage["number"]: stage for stage in record["rounds"]}
    grenades = {number: [] for number in rounds}
    for path in (output / INDEX_FOLDER / map_name).glob(f"{checksum}-*.json.gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                rows = json.load(source)
        except (OSError, ValueError, EOFError):
            continue
        for row in rows:
            stage = rounds.get(row.get("round"))
            kind = row.get("kind")
            if stage is None or kind not in ("smoke", "flash", "he", "molo"):
                continue
            launch = row["launchTick"] - stage["startTick"]
            activation = row["activationTick"] - stage["startTick"]
            duration = {"smoke": SMOKE_SECONDS, "molo": INFERNO_SECONDS}.get(kind) or BURST_SECONDS[kind]
            grenades[stage["number"]].append({
                "k": kind, "p": row.get("steamId", ""), "t": [launch, activation, activation + round(duration * tick_rate)],
                "at": row["impact"], "path": [launch, *map(round, row["launch"]), activation, *map(round, row["impact"])],
                "approximate": True,
            })
    return grenades


def load_replay(output, checksum):
    paths = replay_paths(output, checksum)
    if not paths["record"].is_file():
        raise ValueError("Ce replay n’existe plus.")
    record = load_record(paths["record"])
    if record.get("version", 1) == 3:
        # Version 3 kept incendiaries until their entity disappeared, about 20 s. Cap them to the burn time.
        burn = INFERNO_SECONDS * record["tickRate"]
        for stage in record["rounds"]:
            for grenade in stage.get("grenades", ()):
                if grenade["k"] == "molo":
                    grenade.pop("fire", None)
                    grenade["t"][2] = min(grenade["t"][2], grenade["t"][1] + burn)
    elif record.get("version", 1) < 3:
        grenades = indexed_grenades(output, record)
        for stage in record["rounds"]:
            stage.setdefault("grenades", sorted(grenades.get(stage["number"], ()), key=lambda grenade: grenade["t"][0]))
        teams = summary_for(output, checksum, paths).get("teams")
        if teams and not record.get("teams"):
            record["teams"] = teams
    record["id"] = checksum
    record["demoSaved"] = paths["demo"].is_file()
    return record


def replay_name(output, checksum):
    paths = replay_paths(output, checksum)
    try:
        return summary_for(output, checksum, paths).get("demo") or None
    except (OSError, ValueError, KeyError, TypeError, EOFError):
        return None


def saved_demo(output, checksum):
    demo = replay_paths(output, checksum)["demo"]
    if not demo.is_file():
        raise ValueError("La démo de ce replay n’a pas été conservée. Réimporte-la pour mettre le replay à jour.")
    return demo


def delete_replay(output, checksum, scope):
    if scope not in ("demo", "all"):
        raise ValueError("Suppression invalide.")
    paths = replay_paths(output, checksum)
    if not paths["record"].is_file() and not paths["demo"].is_file():
        raise ValueError("Ce replay n’existe plus.")
    freed = 0
    targets = ("demo",) if scope == "demo" else ("demo", "record", "meta")
    for name in targets:
        path = paths[name]
        if path.is_file():
            freed += path.stat().st_size
            path.unlink()
    return {"freed": freed}


def storage_usage(replays):
    return {"replays": sum(replay["replaySize"] for replay in replays),
            "demos": sum(replay["demoSize"] for replay in replays)}
