"""Enemy points of view at the end of a clip.

The camera always moves to enemies of the thrower:
- smoke: at most one, an enemy inside the smoke, or right next to it and looking at it;
- flash: at most two of the flashed enemies, those killed with the flash's help first;
- HE and incendiary: at most two of the damaged enemies, those killed with the grenade's help first.

Each chosen enemy gets a stretch of the clip, in the order the action happens. A victim killed later than the
normal end of the clip extends it a little, so that the kill is in the video.
"""

import math
from collections import defaultdict

from round_replay import csv_rows
from throw_inputs import finite, open_demo_parser


SIDES = {"2": "t", "3": "ct"}
TEAM_NUMBERS = {2: "t", 3: "ct"}
MAX_POVS = {"smoke": 1, "flash": 2, "he": 2, "molo": 2}
DAMAGE_WEAPONS = {"he": {"HE Grenade"}, "molo": {"Molotov", "Incendiary Grenade"}}
# Damage counted after the activation: an HE hits at once, an incendiary burns for a few seconds.
DAMAGE_WINDOW_SECONDS = {"he": 0.25, "molo": 8}
# A light flash or a scratch does not make an interesting point of view, unless the victim dies of it.
MIN_BLIND_SECONDS = 1.0
MIN_DAMAGE = 5
# A kill this soon after the hit counts as helped by the utility, like CS2's own assists.
KILL_FOLLOW_SECONDS = 4
FLASH_FOLLOW_SECONDS = 1
# Switch slightly before the effect so the victim sees the grenade arrive.
SWITCH_LEAD_SECONDS = {"flash": 0.7, "he": 0.4, "molo": 0.4, "smoke": 0.3}
# Time on a victim from the camera switch, and after a kill.
HOLD_SECONDS = 2.0
AFTER_KILL_SECONDS = 0.9
MIN_STRETCH_SECONDS = 1.0
# The grenade camera stays on the detonation this long, then each enemy view goes back in time to this long
# before it: the detonation is seen again from every enemy.
REPLAY_AFTER_SECONDS = 1.0
REPLAY_BEFORE_SECONDS = 1.0
# How far after the activation a clip may be extended to show a kill.
MAX_AFTER_ACTIVATION = {"smoke": 5, "flash": 6, "he": 6, "molo": 9}
# Smoke: inside its cloud, or within about 10 m (0.0254 m per unit) and looking at it.
SMOKE_RADIUS = 150
SMOKE_NEAR = 400
SMOKE_VIEW_DEGREES = 35
SMOKE_SEARCH_SECONDS = 3.5
SMOKE_SAMPLE_TICKS = 8
EYE_HEIGHT = 64


def flash_victims(folder, demo):
    """Per flasher: (tick, victim, victim name, blind seconds), enemies only."""
    victims = defaultdict(list)
    for row in csv_rows(folder, demo, "players_flashed"):
        if len(row) < 10 or row[5] == row[9] or row[3] in ("", "0"):
            continue
        try:
            victims[row[7]].append((int(row[0]), row[3], row[4], float(row[2])))
        except ValueError:
            continue
    return victims


def damage_victims(folder, demo):
    """Per attacker: (tick, weapon, victim, health damage), enemies only."""
    events = defaultdict(list)
    for row in csv_rows(folder, demo, "damages"):
        if len(row) < 17 or row[9] == row[13] or row[12] in ("", "0"):
            continue
        try:
            events[row[8]].append((int(row[0]), row[16], row[12], max(0, int(row[2]))))
        except ValueError:
            continue
    return events


def read_kills(folder, demo):
    """Per victim: kills as dicts, with the killer's side, the assister and the flash assist flag."""
    kills = defaultdict(list)
    for row in csv_rows(folder, demo, "kills"):
        if len(row) < 19 or row[7] in ("", "0"):
            continue
        try:
            tick = int(row[0])
        except ValueError:
            continue
        kills[row[7]].append({"tick": tick, "killer": row[3], "killerSide": SIDES.get(row[4]),
                              "victimSide": SIDES.get(row[8]), "assister": row[11], "flashAssist": row[18] == "1",
                              "weapon": row[14]})
    return kills


def player_names(folder, demo):
    names = {}
    for row in csv_rows(folder, demo, "players"):
        if len(row) >= 2 and row[1] not in ("", "0"):
            names[row[1]] = row[0]
    for row in csv_rows(folder, demo, "kills"):
        if len(row) >= 12:
            for name, steam_id in ((row[2], row[3]), (row[6], row[7]), (row[10], row[11])):
                if steam_id not in ("", "0") and name:
                    names.setdefault(steam_id, name)
    return names


def helped_kill(kills, victim, clip, first, last, tick_rate, *, flash=False):
    """The victim's death that the utility helped: by the thrower's team soon after the hit, or credited to it.

    For a flash, "soon" means while still blind or just after; for damage, within a few seconds.
    """
    follow = round((FLASH_FOLLOW_SECONDS if flash else KILL_FOLLOW_SECONDS) * tick_rate)
    for kill in kills.get(victim, ()):
        if kill["tick"] < first:
            continue
        by_team = kill["killerSide"] and kill["killerSide"] == clip.get("side")
        credited = kill["assister"] == clip["steamId"] and (not flash or kill["flashAssist"])
        if by_team and (kill["tick"] <= last + follow or (credited and kill["tick"] <= last + 2 * follow)):
            return kill["tick"]
    return None


def flash_candidates(clip, flashes, kills, tick_rate):
    activation = clip["activationTick"]
    blinded = {}
    for tick, victim, name, duration in flashes.get(clip["steamId"], ()):
        if abs(tick - activation) <= 2 and victim != clip["steamId"] and duration > 0:
            if duration > blinded.get(victim, (0,))[0]:
                blinded[victim] = (duration, name)
    found = []
    for victim, (duration, name) in blinded.items():
        blind_end = activation + round(duration * tick_rate)
        kill = helped_kill(kills, victim, clip, activation, blind_end, tick_rate, flash=True)
        if duration < MIN_BLIND_SECONDS and kill is None:
            continue
        found.append({"steamId": victim, "name": name, "event": activation, "kill": kill,
                      "effect": "blind", "value": round(duration, 1), "rank": duration})
    return found


def damage_candidates(clip, damages, kills, tick_rate):
    kind, activation = clip["kind"], clip["activationTick"]
    window = round(DAMAGE_WINDOW_SECONDS[kind] * tick_rate)
    hits = defaultdict(list)
    for tick, weapon, victim, damage in damages.get(clip["steamId"], ()):
        if weapon in DAMAGE_WEAPONS[kind] and activation - 2 <= tick <= activation + window and victim != clip["steamId"]:
            hits[victim].append((tick, damage))
    found = []
    for victim, events in hits.items():
        total = sum(damage for _, damage in events)
        if total < MIN_DAMAGE:
            continue
        first, last = min(tick for tick, _ in events), max(tick for tick, _ in events)
        kill = helped_kill(kills, victim, clip, first, last, tick_rate)
        found.append({"steamId": victim, "name": "", "event": first, "kill": kill,
                      "effect": "damage", "value": total, "rank": total})
    return found


def replays(clip, chosen, tick_rate):
    """One replay per chosen victim, each from just before the detonation, in the order they are killed.

    Returns the victims with their [tick, stop] demo range, and the tick where the grenade camera stops.
    """
    kind, activation = clip["kind"], clip["activationTick"]
    lead = round(SWITCH_LEAD_SECONDS[kind] * tick_rate)
    limit = activation + round(MAX_AFTER_ACTIVATION[kind] * tick_rate)
    shortest = round(MIN_STRETCH_SECONDS * tick_rate)
    after = round(REPLAY_AFTER_SECONDS * tick_rate)
    rewind = round((REPLAY_AFTER_SECONDS + REPLAY_BEFORE_SECONDS) * tick_rate)
    # Victims who die first are shown first.
    chosen = sorted(chosen, key=lambda victim: (victim["kill"] if victim["kill"] is not None else math.inf,
                                                victim["event"], -victim["rank"]))
    placed = []
    for victim in chosen:
        switch = victim["event"] - lead
        # Back to 1 s before the detonation, or 2 s before a later hit (someone walking into the fire).
        start = max(clip["startTick"], max(activation + after, switch) - rewind)
        if victim["kill"] is not None:
            stop = min(limit, victim["kill"] + round(AFTER_KILL_SECONDS * tick_rate))
        else:
            stop = min(limit, max(switch, start) + round(HOLD_SECONDS * tick_rate))
        if stop - start >= shortest:
            placed.append({**victim, "tick": start, "stop": stop})
    if not placed:
        return [], None
    cut = max(activation + after, min(victim["event"] - lead for victim in placed))
    return placed, cut


def smoke_watcher(clip, positions, tick_rate):
    """The first enemy inside the smoke, or close to it and looking at it."""
    impact = clip["impact"]
    lead = round(SWITCH_LEAD_SECONDS["smoke"] * tick_rate)
    for tick in sorted(positions):
        best = None
        for steam_id, name, side, (x, y, z), yaw, pitch in positions[tick]:
            if not side or side == clip.get("side") or steam_id == clip["steamId"]:
                continue
            flat = math.hypot(x - impact[0], y - impact[1])
            inside = flat <= SMOKE_RADIUS and abs(z - impact[2]) <= 200
            eye = (x, y, z + EYE_HEIGHT)
            to_smoke = tuple(target - source for target, source in zip(impact, eye))
            distance = math.sqrt(sum(value * value for value in to_smoke)) or 1.0
            angle = 0.0
            if not inside:
                if distance > SMOKE_NEAR or not (finite(yaw) and finite(pitch)):
                    continue
                yaw_r, pitch_r = math.radians(yaw), math.radians(pitch)
                view = (math.cos(pitch_r) * math.cos(yaw_r), math.cos(pitch_r) * math.sin(yaw_r), -math.sin(pitch_r))
                cosine = sum(a * b for a, b in zip(view, to_smoke)) / distance
                angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
                if angle > SMOKE_VIEW_DEGREES:
                    continue
            if best is None or (angle, distance) < best[0]:
                best = ((angle, distance), steam_id, name, "inside" if inside else "watching", distance)
        if best:
            _, steam_id, name, effect, distance = best
            start = max(clip["launchTick"] + tick_rate // 4, tick - lead)
            return {"steamId": steam_id, "name": name, "event": start + lead, "kill": None, "effect": effect,
                    "value": round(distance), "rank": 0}
    return None


def smoke_positions(demo, clips, tick_rate):
    """Living players with their view, sampled from each smoke's activation."""
    span = round(SMOKE_SEARCH_SECONDS * tick_rate)
    wanted = {}
    for clip in clips:
        if clip["kind"] == "smoke":
            for tick in range(clip["activationTick"], clip["activationTick"] + span + 1, SMOKE_SAMPLE_TICKS):
                wanted[tick] = True
    if not wanted:
        return {}
    columns = ["tick", "steamid", "name", "X", "Y", "Z", "yaw", "pitch", "team_num", "health"]
    frame = open_demo_parser(demo).parse_ticks(columns[3:], ticks=sorted(wanted))
    players = defaultdict(list)
    for tick, steam_id, name, x, y, z, yaw, pitch, team, health in frame[columns].itertuples(index=False, name=None):
        side = TEAM_NUMBERS.get(int(team)) if finite(team) else None
        if side and finite(health) and health > 0 and all(map(finite, (x, y, z))) and steam_id:
            players[int(tick)].append((str(int(steam_id)), name if isinstance(name, str) else "", side, (x, y, z),
                                       yaw, pitch))
    return players


def assign_enemy_pov(folder, demo, clips, tick_rate):
    """Add the enemy cameras to each clip (clip["povs"]) and return how many clips got at least one."""
    flashes = flash_victims(folder, demo)
    damages = damage_victims(folder, demo)
    kills = read_kills(folder, demo)
    names = player_names(folder, demo)
    sampled = smoke_positions(demo, clips, tick_rate)
    assigned = 0
    for clip in clips:
        kind = clip["kind"]
        if kind == "smoke":
            span = round(SMOKE_SEARCH_SECONDS * tick_rate)
            window = {tick: sampled[tick] for tick in sampled
                      if clip["activationTick"] <= tick <= clip["activationTick"] + span}
            watcher = smoke_watcher(clip, window, tick_rate)
            candidates = [watcher] if watcher else []
        elif kind == "flash":
            candidates = flash_candidates(clip, flashes, kills, tick_rate)
        else:
            candidates = damage_candidates(clip, damages, kills, tick_rate)
        # Kills the utility helped come first, then the strongest effect.
        candidates.sort(key=lambda victim: (victim["kill"] is None, -victim["rank"]))
        placed, cut = replays(clip, candidates[:MAX_POVS[kind]], tick_rate)
        if not placed:
            continue
        clip["replay"] = {"cutTick": cut, "parts": [[victim["tick"], victim["stop"]] for victim in placed]}
        clip["endTick"] = max(clip["endTick"], *(victim["stop"] for victim in placed))
        clip["povs"] = [{
            "steamId": victim["steamId"], "player": names.get(victim["steamId"]) or victim["name"] or victim["steamId"],
            "tick": victim["tick"], "offset": round(offset / tick_rate, 3),
            "effect": victim["effect"], "value": victim["value"], "killed": victim["kill"] is not None,
        } for victim, offset in zip(placed, part_offsets(clip))]
        assigned += 1
    return assigned


def part_offsets(clip):
    """Where each enemy replay starts in the clip's video, in ticks after the clip start."""
    replay = clip["replay"]
    offset, offsets = replay["cutTick"] - clip["startTick"], []
    for first, last in replay["parts"]:
        offsets.append(offset)
        offset += last - first
    return offsets


def recorded_parts(clip):
    """The demo tick ranges recorded for a clip: the grenade, then one replay per enemy view."""
    replay = clip.get("replay") if clip_povs(clip) else None
    if replay and replay.get("parts"):
        return [(clip["startTick"], replay["cutTick"]), *(tuple(part) for part in replay["parts"])]
    return [(clip["startTick"], clip["endTick"])]


def clip_povs(clip):
    """The enemy cameras of a clip, also for clips captured with the older single-POV format."""
    povs = clip.get("povs")
    if isinstance(povs, list):
        return [pov for pov in povs if isinstance(pov, dict)]
    return [clip["pov"]] if isinstance(clip.get("pov"), dict) else []
