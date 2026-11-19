import gzip
import json
import re
from uuid import uuid4

from grenade_data import read_round_clock
from throw_inputs import finite, open_demo_parser


REPLAY_FOLDER = ".round-replays"
SAMPLES_PER_SECOND = 5


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
    return {"version": 1, "demo": demo.name, "checksum": checksum, "map": map_name,
            "tickRate": tick_rate, "samplesPerSecond": SAMPLES_PER_SECOND, "players": players, "rounds": rounds}


def write_round_replay(output, demo, folder, map_name, tick_rate, checksum):
    if not re.fullmatch(r"[0-9a-f]{1,16}", checksum):
        raise ValueError("Identifiant de démo invalide pour le relevé des positions.")
    data = read_round_positions(demo, folder, map_name, tick_rate, checksum)
    directory = output / REPLAY_FOLDER
    directory.mkdir(exist_ok=True)
    temporary = directory / f".{uuid4().hex}.tmp"
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as target:
            json.dump(data, target, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        temporary.replace(directory / f"{checksum}.json.gz")
    finally:
        temporary.unlink(missing_ok=True)
