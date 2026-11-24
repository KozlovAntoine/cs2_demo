import csv
import gzip
import json
import re
from collections import defaultdict
from functools import lru_cache
from uuid import uuid4

from grenade_data import KINDS, PLAYER_SIDES, point, read_round_clock, unpack
from throw_inputs import finite, open_demo_parser


REPLAY_FOLDER = ".round-replays"
REPLAY_VERSION = 4
SUMMARY_VERSION = 1
SAMPLES_PER_SECOND = 5
FIRST_HALF_ROUNDS = 12
HEATMAP_DURATION_SECONDS = 115
HEATMAP_RESOLUTION = 512
# Effect lengths used when the demo does not record the end of the effect.
SMOKE_SECONDS = 22
INFERNO_SECONDS = 7
BURST_SECONDS = {"flash": 0.4, "he": 0.5}
GRENADE_PATH_STEP_SECONDS = 1 / 16
ACTIVATION_EVENTS = {"smoke": "smokes_start", "flash": "flashbangs_explode", "he": "he_grenades_explode"}


def csv_rows(folder, demo, name):
    try:
        with (folder / f"{demo.stem}_{name}.csv").open(encoding="utf-8", newline="") as source:
            return list(csv.reader(source))
    except FileNotFoundError:
        return []


def demo_header(folder, demo):
    rows = csv_rows(folder, demo, "demo")
    if not rows or len(rows[0]) < 12 or rows[0][1] != "CS2":
        raise ValueError(f"{demo.name} doit être une démo de CS2.")
    info = rows[0]
    tick_rate = round(float(info[11]))
    if tick_rate <= 0:
        raise ValueError(f"Fréquence de ticks invalide dans {demo.name}.")
    return info[7], tick_rate, info[0], info[3]


def coordinates(values, digits=1):
    return [round(value, digits) for value in values]


def round_offset(stage, tick, margin=0):
    offset = int(tick) - stage["startTick"]
    return offset if -margin <= offset <= stage["endTick"] - stage["startTick"] + margin else None


def add_round_player_state(data, folder, demo):
    rounds = {str(stage["number"]): stage for stage in data["rounds"]}
    for stage in rounds.values():
        stage["sides"] = {}
        stage["deathTicks"] = {}

    for row in csv_rows(folder, demo, "player_position_samples"):
        stage = rounds.get(row[0])
        side = PLAYER_SIDES.get(row[2])
        if stage is not None and side and row[1] in stage["tracks"]:
            stage["sides"][row[1]] = side

    for row in csv_rows(folder, demo, "kills"):
        stage = rounds.get(row[1])
        if stage is None or row[7] not in stage["tracks"]:
            continue
        offset = int(row[0]) - stage["startTick"]
        if 0 <= offset <= stage["endTick"] - stage["startTick"]:
            stage["deathTicks"][row[7]] = min(offset, stage["deathTicks"].get(row[7], offset))


def add_round_results(data, folder, demo):
    rounds = {str(stage["number"]): stage for stage in data["rounds"]}
    for row in csv_rows(folder, demo, "rounds"):
        stage = rounds.get(row[0])
        if stage is None or len(row) < 23:
            continue
        sides = (PLAYER_SIDES.get(row[9]), PLAYER_SIDES.get(row[10]))
        if None in sides:
            continue
        stage["teams"] = {sides[0]: row[5], sides[1]: row[6]}
        stage["score"] = {sides[0]: int(row[7]), sides[1]: int(row[8])}
        stage["economy"] = {sides[0]: row[17], sides[1]: row[18]}
        stage["winner"] = PLAYER_SIDES.get(row[22], "")
        stage["reason"] = int(row[20]) if row[20].isdigit() else 0
    teams = [{"name": row[0], "score": int(row[2])} for row in csv_rows(folder, demo, "teams")
             if len(row) > 2 and row[2].lstrip("-").isdigit()]
    if teams:
        data["teams"] = teams


def add_round_kills(data, folder, demo):
    rounds = {str(stage["number"]): stage for stage in data["rounds"]}
    for stage in rounds.values():
        stage["kills"] = []
        stage["health"] = []
    tick_rate = data["tickRate"]
    for row in csv_rows(folder, demo, "kills"):
        stage = rounds.get(row[1])
        if stage is None or len(row) < 40 or (offset := round_offset(stage, row[0], tick_rate)) is None:
            continue
        kill = {"t": offset, "k": row[3] if row[3] != "0" else "", "v": row[7], "w": row[14],
                "kp": coordinates(map(float, row[22:25])), "vp": coordinates(map(float, row[27:30]))}
        if row[11] not in ("", "0"):
            kill["a"] = row[11]
        flags = [name for name, column in (("hs", 16), ("flash", 18), ("smoke", 38), ("noscope", 39))
                 if row[column] == "1"]
        if row[17] not in ("", "0"):
            flags.append("wall")
        if flags:
            kill["f"] = flags
        stage["kills"].append(kill)
    for row in csv_rows(folder, demo, "damages"):
        stage = rounds.get(row[1])
        if stage is None or len(row) < 13 or (offset := round_offset(stage, row[0])) is None:
            continue
        if row[12] in stage["tracks"] and row[5].lstrip("-").isdigit():
            stage["health"].append([offset, row[12], max(0, int(row[5]))])
    for stage in rounds.values():
        stage["kills"].sort(key=lambda kill: kill["t"])
        stage["health"].sort(key=lambda event: event[0])


def add_round_bomb(data, folder, demo):
    rounds = {str(stage["number"]): stage for stage in data["rounds"]}
    margin = data["tickRate"] * 2
    for name, column_count in (("bombs_planted", 9), ("bombs_defused", 3), ("bombs_exploded", 3),
                               ("bombs_defuse_start", 4)):
        for row in csv_rows(folder, demo, name):
            stage = rounds.get(row[1]) if len(row) >= column_count else None
            if stage is None or (offset := round_offset(stage, row[0], margin)) is None:
                continue
            bomb = stage.setdefault("bomb", {})
            if name == "bombs_planted":
                bomb["plant"] = {"t": offset, "site": row[2], "p": row[3],
                                 "at": coordinates(map(float, row[6:9]))}
            elif name == "bombs_defused":
                bomb["defuse"] = offset
            elif name == "bombs_exploded":
                bomb["explode"] = offset
            else:
                bomb.setdefault("defuseStarts", []).append([offset, row[2]])


def read_infernos(folder, demo, tick_rate):
    """Each row is one inferno: the ticks where its fire changes and the 2D convex hull at each of them.

    The fire is out at the first empty hull. The entity itself can remain in the demo for several more seconds.
    """
    infernos = []
    for row in csv_rows(folder, demo, "inferno_positions"):
        ticks = unpack(row[7], "I") if len(row) > 8 and len(row[7]) > 2 else []
        if not ticks:
            continue
        hulls = re.findall(r'"([^"]*)"', row[8])
        timeline = []
        end = None
        if len(hulls) == len(ticks):
            for tick, hull in zip(ticks, hulls):
                try:
                    values = [round(float(value)) for value in hull.split(",")] if hull else []
                except ValueError:
                    values = []
                timeline.append([tick, values])
                if not values:
                    end = tick
                    break
        if not timeline:
            timeline = [[ticks[0], [round(float(row[4])), round(float(row[5]))]]]
        infernos.append({"player": row[2], "start": ticks[0], "hulls": timeline,
                         "end": end or min(ticks[-1], ticks[0] + INFERNO_SECONDS * tick_rate)})
    return infernos


def add_round_grenades(data, folder, demo):
    rounds = {str(stage["number"]): stage for stage in data["rounds"]}
    tick_rate = data["tickRate"]
    activations = {}
    for kind, name in ACTIVATION_EVENTS.items():
        for row in csv_rows(folder, demo, name):
            activations.setdefault((kind, row[3]), (int(row[0]), point(row, 4)))
    destroyed = {row[3]: (int(row[0]), point(row, 5)) for row in csv_rows(folder, demo, "grenade_projectiles_destroy")}
    infernos = read_infernos(folder, demo, tick_rate)

    step = max(1, round(tick_rate * GRENADE_PATH_STEP_SECONDS))
    for stage in rounds.values():
        stage["grenades"] = []
    for row in csv_rows(folder, demo, "grenade_positions"):
        stage = rounds.get(row[0])
        kind = KINDS.get(row[3]) if len(row) >= 17 else None
        if stage is None or kind is None or row[4] in ("", "0"):
            continue
        ticks = unpack(row[13], "I")
        if not ticks:
            continue
        trajectory = list(zip(ticks, *(unpack(column, "f") for column in row[14:17]), strict=True))
        end_event = destroyed.get(row[2])
        fire = None
        if kind == "molo":
            activation, impact = end_event or (trajectory[-1][0], trajectory[-1][1:])
            # An incendiary starts burning when its projectile disappears.
            match = min((inferno for inferno in infernos if inferno["player"] == row[4]
                         and abs(inferno["start"] - activation) <= tick_rate // 4),
                        key=lambda inferno: abs(inferno["start"] - activation), default=None)
            if match:
                infernos.remove(match)
                end = match["end"]
                fire = [[int(tick) - stage["startTick"], hull] for tick, hull in match["hulls"]]
            else:
                end = activation + tick_rate
        else:
            activation, impact = activations.get((kind, row[2])) or end_event or (trajectory[-1][0], trajectory[-1][1:])
            if kind == "smoke":
                # The smoke projectile is destroyed when the cloud fades or is cleared.
                destroyed_tick = end_event[0] if end_event else 0
                end = destroyed_tick if destroyed_tick > activation else activation + SMOKE_SECONDS * tick_rate
            else:
                end = activation + round(BURST_SECONDS[kind] * tick_rate)
        launch = round_offset(stage, trajectory[0][0], tick_rate * 5)
        if launch is None:
            continue
        flight = [sample for sample in trajectory if sample[0] < activation]
        sampled = flight[::step]
        if flight and sampled[-1] is not flight[-1]:
            sampled.append(flight[-1])
        path = []
        for tick, x, y, z in [*sampled, (activation, *impact)]:
            path.extend((int(tick) - stage["startTick"], round(x), round(y), round(z)))
        grenade = {"k": kind, "p": row[4],
                   "t": [launch, int(activation) - stage["startTick"], int(end) - stage["startTick"]],
                   "at": coordinates(impact), "path": path}
        if fire:
            grenade["fire"] = fire
        stage["grenades"].append(grenade)
    for stage in rounds.values():
        stage["grenades"].sort(key=lambda grenade: grenade["t"][0])


def read_round_positions(demo, folder, map_name, tick_rate, checksum, date=None):
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
    columns = ["tick", "steamid", "name", "X", "Y", "Z", "yaw"]
    rows = open_demo_parser(demo).parse_ticks(columns[3:], ticks=sorted(round_at_tick))
    for tick, steam_id, name, x, y, z, yaw in rows[columns].itertuples(index=False, name=None):
        stage = round_at_tick.get(tick)
        if not stage or not isinstance(steam_id, int) or steam_id <= 0 or not all(map(finite, (x, y, z))):
            continue
        identity = str(int(steam_id))
        players[identity] = name if isinstance(name, str) and name else players.get(identity, identity)
        # Store the tick relative to round start so tracks share the same local clock.
        sample = [int(tick) - stage["startTick"], round(x, 1), round(y, 1), round(z, 1),
                  round(yaw) if finite(yaw) else 0]
        stage["tracks"].setdefault(identity, []).append(sample)
    if not players:
        raise ValueError("Aucune position de joueur retrouvée dans la démo.")

    for stage in rounds:
        for positions in stage["tracks"].values():
            positions.sort(key=lambda sample: sample[0])
    data = {"demo": demo.name, "checksum": checksum, "map": map_name,
            "tickRate": tick_rate, "samplesPerSecond": SAMPLES_PER_SECOND, "players": players, "rounds": rounds}
    if date:
        data["date"] = date
    add_round_player_state(data, folder, demo)
    add_round_results(data, folder, demo)
    add_round_kills(data, folder, demo)
    add_round_bomb(data, folder, demo)
    add_round_grenades(data, folder, demo)
    data["version"] = REPLAY_VERSION
    return data


def write_round_replay(output, demo, folder, map_name, tick_rate, checksum, name=None):
    try:
        date = demo_header(folder, demo)[3] or None
    except (ValueError, IndexError):
        date = None
    data = read_round_positions(demo, folder, map_name, tick_rate, checksum, date)
    if name:
        # A stored demo is named after its checksum. Keep the file name it was imported with.
        data["demo"] = name
    save_round_replay(output, data)
    return data


def replay_summary(data):
    rounds = [stage["number"] for stage in data["rounds"] if stage.get("sides")]
    version = data.get("version", 1)
    summary = {"map": data["map"], "rounds": rounds, "version": version, "recordVersion": version,
               "demo": data.get("demo", ""), "players": len(data.get("players", {})), "summaryVersion": SUMMARY_VERSION}
    for key in ("date", "teams"):
        if data.get(key):
            summary[key] = data[key]
    return summary


def write_replay_meta(directory, checksum, summary):
    temporary = directory / f".{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(summary, ensure_ascii=True), encoding="utf-8")
        temporary.replace(directory / f"{checksum}.meta.json")
    finally:
        temporary.unlink(missing_ok=True)


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
    finally:
        temporary.unlink(missing_ok=True)
    write_replay_meta(directory, checksum, replay_summary(data))


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
                for tick, x, y, z, *_ in samples:
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
