import csv
import gzip
import json
import re
from collections import defaultdict
from functools import lru_cache
from uuid import uuid4

from grenade_data import PLAYER_SIDES, read_round_clock
from throw_inputs import finite, open_demo_parser


REPLAY_FOLDER = ".round-replays"
SAMPLES_PER_SECOND = 5
FIRST_HALF_ROUNDS = 12
HEATMAP_DURATION_SECONDS = 115
HEATMAP_RESOLUTION = 512


def add_round_player_state(data, folder, demo):
    rounds = {str(stage["number"]): stage for stage in data["rounds"]}
    for stage in rounds.values():
        stage["sides"] = {}
        stage["deathTicks"] = {}

    with (folder / f"{demo.stem}_player_position_samples.csv").open(encoding="utf-8", newline="") as source:
        for row in csv.reader(source):
            stage = rounds.get(row[0])
            side = PLAYER_SIDES.get(row[2])
            if stage is not None and side and row[1] in stage["tracks"]:
                stage["sides"][row[1]] = side

    with (folder / f"{demo.stem}_kills.csv").open(encoding="utf-8", newline="") as source:
        for row in csv.reader(source):
            stage = rounds.get(row[1])
            if stage is None or row[7] not in stage["tracks"]:
                continue
            offset = int(row[0]) - stage["startTick"]
            if 0 <= offset <= stage["endTick"] - stage["startTick"]:
                stage["deathTicks"][row[7]] = min(offset, stage["deathTicks"].get(row[7], offset))
    data["version"] = 2


def read_round_positions(demo, folder, map_name, tick_rate, checksum):
    rounds = []
    round_at_tick = {}
    for number, start, end in read_round_clock(folder, demo):
        if end <= start:
            continue
        stage = {"number": number, "startTick": start, "endTick": end, "tracks": {}}
        rounds.append(stage)
        count = (end - start) * SAMPLES_PER_SECOND // tick_rate + 1
        for sample in range(count):
            tick = start + round(sample * tick_rate / SAMPLES_PER_SECOND)
            round_at_tick[tick] = stage
    if not round_at_tick:
        raise ValueError("Aucun round avec une horloge dans la démo.")

    players = {}
    columns = ["tick", "steamid", "name", "X", "Y", "Z"]
    rows = open_demo_parser(demo).parse_ticks(columns[3:], ticks=sorted(round_at_tick))
    for tick, steam_id, name, x, y, z in rows[columns].itertuples(index=False, name=None):
        stage = round_at_tick.get(tick)
        if not stage or not isinstance(steam_id, int) or steam_id <= 0 or not all(map(finite, (x, y, z))):
            continue
        identity = str(int(steam_id))
        players[identity] = name if isinstance(name, str) and name else players.get(identity, identity)
        # Store the tick relative to round start so tracks share the same local clock.
        point = [int(tick) - stage["startTick"], round(x, 3), round(y, 3), round(z, 3)]
        stage["tracks"].setdefault(identity, []).append(point)
    if not players:
        raise ValueError("Aucune position de joueur retrouvée dans la démo.")

    for stage in rounds:
        for positions in stage["tracks"].values():
            positions.sort(key=lambda point: point[0])
    data = {"demo": demo.name, "checksum": checksum, "map": map_name,
            "tickRate": tick_rate, "samplesPerSecond": SAMPLES_PER_SECOND, "players": players, "rounds": rounds}
    add_round_player_state(data, folder, demo)
    return data


def write_round_replay(output, demo, folder, map_name, tick_rate, checksum):
    save_round_replay(output, read_round_positions(demo, folder, map_name, tick_rate, checksum))


def save_round_replay(output, data):
    checksum = data["checksum"]
    if not re.fullmatch(r"[0-9a-f]{1,16}", checksum):
        raise ValueError("Identifiant de démo invalide pour le relevé des positions.")
    directory = output / REPLAY_FOLDER
    directory.mkdir(exist_ok=True)
    temporary = directory / f".{uuid4().hex}.tmp"
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as target:
            json.dump(data, target, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        temporary.replace(directory / f"{checksum}.json.gz")
        rounds = [stage["number"] for stage in data["rounds"] if stage.get("sides")]
        temporary.write_text(json.dumps({"map": data["map"], "rounds": rounds}), encoding="utf-8")
        temporary.replace(directory / f"{checksum}.meta.json")
    finally:
        temporary.unlink(missing_ok=True)


def replay_index(output):
    maps = defaultdict(dict)
    directory = output / REPLAY_FOLDER
    for metadata in sorted(directory.glob("*.meta.json")):
        checksum = metadata.name.removesuffix(".meta.json")
        record = directory / f"{checksum}.json.gz"
        try:
            if metadata.resolve() != metadata or record.resolve() != record or not record.is_file():
                continue
            info = json.loads(metadata.read_text(encoding="utf-8"))
            name = info["map"]
            rounds = tuple(sorted({number for number in info["rounds"]
                                   if type(number) is int and 1 <= number <= FIRST_HALF_ROUNDS}))
            if rounds and isinstance(name, str) and re.fullmatch(r"[a-z0-9_]+", name):
                maps[name][record] = rounds
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return maps


def player_heatmap(records, radar, round_number):
    files = []
    for path, rounds in records.items():
        if round_number and round_number not in rounds:
            continue
        try:
            version = path.stat()
        except FileNotFoundError:
            continue
        files.append((path, version.st_mtime_ns, version.st_size))
    geometry = (radar["originX"], radar["originY"], radar["units"], radar["thresholdZ"], bool(radar["lower"]))
    return build_player_heatmap(tuple(files), geometry, round_number)


@lru_cache(maxsize=6)
def build_player_heatmap(files, geometry, round_number):
    origin_x, origin_y, units, threshold_z, has_lower = geometry
    sides = ("t", "ct")
    last_frame = HEATMAP_DURATION_SECONDS * SAMPLES_PER_SECOND
    floors = {level: {side: [[] for _ in range(last_frame + 1)] for side in sides}
              for level in (("upper", "lower") if has_lower else ("upper",))}
    max_pixel = HEATMAP_RESOLUTION - 1
    scale = max_pixel / units
    demos = 0
    rounds = 0
    for path, _, _ in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                record = json.load(source)
        except (OSError, ValueError, EOFError):
            continue
        tick_rate = record["tickRate"]
        included = 0
        for stage in record["rounds"]:
            if not 1 <= stage["number"] <= FIRST_HALF_ROUNDS or (round_number and stage["number"] != round_number):
                continue
            if not stage.get("sides"):
                continue
            included += 1
            for player, samples in stage["tracks"].items():
                side = stage["sides"].get(player)
                if side not in sides:
                    continue
                death = stage.get("deathTicks", {}).get(player, float("inf"))
                for tick, x, y, z in samples:
                    frame = round(tick * SAMPLES_PER_SECOND / tick_rate)
                    if tick >= death or frame > last_frame:
                        break
                    px, py = (x - origin_x) * scale, (origin_y - y) * scale
                    if not (0 <= px <= max_pixel and 0 <= py <= max_pixel):
                        continue
                    level = "lower" if has_lower and z < threshold_z else "upper"
                    floors[level][side][frame].append(round(py) * HEATMAP_RESOLUTION + round(px))
        demos += bool(included)
        rounds += included
    return {"size": HEATMAP_RESOLUTION, "samplesPerSecond": SAMPLES_PER_SECOND, "duration": HEATMAP_DURATION_SECONDS,
            "demos": demos, "rounds": rounds, "floors": floors}
