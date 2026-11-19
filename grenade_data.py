import csv
import math
import struct
from collections import defaultdict
from pathlib import Path


# Column order follows CS Demo Analyzer's CSDM export (CS:DM 3.21).
KINDS = {
    "Smoke Grenade": "smoke",
    "Flashbang": "flash",
    "HE Grenade": "he",
    "Molotov": "molo",
    "Incendiary Grenade": "molo",
}
UTILITY_KINDS = frozenset(KINDS.values())
PLAYER_SIDES = {"2": "t", "3": "ct"}

ACTIVATIONS = {
    "smoke": "smokes_start",
    "flash": "flashbangs_explode",
    "he": "he_grenades_explode",
}


def unpack(value, format_code):
    return [part[0] for part in struct.iter_unpack(">" + format_code, bytes.fromhex(value[2:]))]


def point(row, offset):
    return tuple(float(value) for value in row[offset:offset + 3])


def read_round_clock(folder, demo):
    # CSDM rounds rows contain the freeze end and the round result at columns 2 and 3.
    try:
        with (folder / f"{demo.stem}_rounds.csv").open(encoding="utf-8", newline="") as source:
            return [(int(row[0]), int(row[2]), int(row[3])) for row in csv.reader(source)
                    if len(row) > 3 and row[2].isdigit() and row[3].isdigit()]
    except FileNotFoundError:
        return []


def read_clips(folder, demo: Path, kind):
    def rows(name):
        with (folder / f"{demo.stem}_{name}.csv").open(encoding="utf-8", newline="") as source:
            yield from csv.reader(source)

    demo_info = next(rows("demo"))
    if demo_info[1] != "CS2" or demo_info[5] != "GOTV":
        raise ValueError(f"{demo.name} doit être une démo GOTV de CS2.")
    map_name = demo_info[7]
    tick_count = int(demo_info[10])
    tick_rate = round(float(demo_info[11]))
    if tick_rate <= 0:
        raise ValueError(f"Fréquence de ticks invalide dans {demo.name}.")

    round_starts = {number: start for number, start, _ in read_round_clock(folder, demo)}

    shots = {row[4]: row for row in rows("shots") if row[2] in KINDS}
    activations = defaultdict(list)
    for nade, file_name in ACTIVATIONS.items():
        if kind in ("all", nade):
            for row in rows(file_name):
                activations[nade, row[3]].append((int(row[0]), point(row, 4)))

    destroyed = {row[3]: (int(row[0]), point(row, 5)) for row in rows("grenade_projectiles_destroy")}
    fires = defaultdict(list)
    if kind in ("all", "molo"):
        for row in rows("inferno_positions"):
            tick = unpack(row[7], "I")[0]
            fires[tick, row[2]].append(point(row, 4))

    clips = []
    for row in rows("grenade_positions"):
        round_number, _, projectile, weapon, steam_id, player = row[:6]
        nade = KINDS.get(weapon)
        if nade is None or kind not in ("all", nade):
            continue
        ticks = unpack(row[13], "I")
        coordinates = [unpack(column, "f") for column in row[14:17]]
        trajectory = list(zip(ticks, *coordinates, strict=True))
        if not trajectory:
            continue
        shot = shots.get(projectile)
        launch_tick = int(shot[0]) if shot and shot[9] == steam_id else ticks[0]
        launch = point(shot, 5) if shot and shot[9] == steam_id else trajectory[0][1:]
        last_position = trajectory[-1][1:]
        end_event = destroyed.get(projectile)
        if nade == "molo" and end_event:
            end_tick, end_position = end_event
            fire_positions = fires.get((end_tick, steam_id), ())
            if fire_positions:
                activations[nade, projectile] = [(end_tick, pos) for pos in fire_positions]
        candidates = [event for event in activations.get((nade, projectile), ()) if event[0] >= launch_tick]
        if candidates:
            # Some demos emit two flash events at the same tick. Keep the position closest
            # to the projectile disappearance and emit a single clip.
            reference = end_event[1] if end_event else last_position
            activation_tick, impact = min(candidates, key=lambda event: (event[0], math.dist(event[1], reference)))
            estimated = False
        else:
            activation_tick, impact = end_event or (ticks[-1], last_position)
            estimated = True

        start_tick = max(100 + tick_rate, launch_tick - 3 * tick_rate)
        end_tick = min(tick_count - 1, activation_tick + 3 * tick_rate)
        if start_tick >= launch_tick or activation_tick >= end_tick or steam_id == "0":
            continue
        flight = [sample for sample in trajectory if sample[0] < activation_tick]
        # Preserve the last projectile position for orientation and use the effect center
        # as the point on the radar. Those positions can differ for an incendiary.
        if not flight:
            flight = [trajectory[0]]
        flight.append((activation_tick, *(end_event[1] if nade == "molo" and end_event else impact)))
        round_number = int(round_number)
        clip = {
            "kind": nade, "player": player, "steamId": steam_id, "team": row[7].strip(), "round": round_number,
            "side": PLAYER_SIDES.get(row[6], ""),
            "launch": launch, "impact": impact, "launchTick": launch_tick,
            "activationTick": activation_tick, "startTick": start_tick, "endTick": end_tick,
            "estimated": estimated, "flight": flight, "yaw": float(row[11]),
        }
        if round_number in round_starts:
            round_start = round_starts[round_number]
            clip["roundLaunchSeconds"] = (launch_tick - round_start) / tick_rate
            if not estimated:
                clip["roundActivationSeconds"] = (activation_tick - round_start) / tick_rate
        clips.append(clip)

    return map_name, tick_rate, sorted(clips, key=lambda clip: clip["launchTick"]), demo_info[0]
