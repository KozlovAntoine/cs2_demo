import math
from collections import defaultdict

from round_replay import csv_rows
from throw_inputs import finite, open_demo_parser


SIDES = {"2": "t", "3": "ct"}
TEAM_NUMBERS = {2: "t", 3: "ct"}
DAMAGE_WEAPONS = {"he": {"HE Grenade"}, "molo": {"Molotov", "Incendiary Grenade"}}
# Damage counted after the activation: an HE hits at once, an incendiary burns for a few seconds.
DAMAGE_WINDOW_SECONDS = {"he": 0.25, "molo": 8}
# Switch slightly before the activation so the victim sees the grenade arrive.
SWITCH_LEAD_SECONDS = {"flash": 0.7, "he": 0.4, "molo": 0.4, "smoke": 0.4}
# An enemy within about 10 m of the impact (0.0254 m per game unit) counts as right next to it.
NEAR_DISTANCE = 400


def flash_victims(folder, demo, tick_rate):
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
    events = defaultdict(list)
    for row in csv_rows(folder, demo, "damages"):
        if len(row) < 17 or row[9] == row[13] or row[12] in ("", "0"):
            continue
        try:
            events[row[8]].append((int(row[0]), row[16], row[12], max(0, int(row[2]))))
        except ValueError:
            continue
    return events


def nearest_enemies(demo, clips):
    """Positions of every living player at each activation, read in one pass of the demo."""
    ticks = sorted({clip["activationTick"] for clip in clips})
    if not ticks:
        return {}
    columns = ["tick", "steamid", "name", "X", "Y", "Z", "team_num", "health"]
    frame = open_demo_parser(demo).parse_ticks(columns[3:], ticks=ticks)
    players = defaultdict(list)
    for tick, steam_id, name, x, y, z, team, health in frame[columns].itertuples(index=False, name=None):
        side = TEAM_NUMBERS.get(int(team)) if finite(team) else None
        if side and finite(health) and health > 0 and all(map(finite, (x, y, z))) and steam_id:
            players[int(tick)].append((str(int(steam_id)), name if isinstance(name, str) else "", side, (x, y, z)))
    return players


def assign_enemy_pov(folder, demo, clips, tick_rate):
    """Add the enemy camera to each clip: the most affected enemy, or the nearest living one."""
    flashes = flash_victims(folder, demo, tick_rate)
    damages = damage_victims(folder, demo)
    names = {}
    positions = nearest_enemies(demo, clips)
    for living in positions.values():
        for steam_id, name, _, _ in living:
            names.setdefault(steam_id, name)
    assigned = 0
    for clip in clips:
        kind = clip["kind"]
        activation = clip["activationTick"]
        # Only enemies right next to the impact qualify, whatever the utility. Without one, the clip keeps
        # the grenade camera until the end.
        nearby = {steam_id: math.dist(position, clip["impact"]) for steam_id, _, side, position
                  in positions.get(activation, ()) if side and side != clip.get("side")
                  and math.dist(position, clip["impact"]) <= NEAR_DISTANCE}
        if not nearby:
            continue
        effect = {}
        if kind == "flash":
            blinded = defaultdict(float)
            for tick, steam_id, name, duration in flashes.get(clip["steamId"], ()):
                if abs(tick - activation) <= 2 and steam_id in nearby:
                    blinded[steam_id] = max(blinded[steam_id], duration)
                    names.setdefault(steam_id, name)
            if blinded:
                victim = max(blinded, key=blinded.get)
                effect = {"effect": "blind", "value": round(blinded[victim], 1)}
        elif kind in DAMAGE_WEAPONS:
            window = DAMAGE_WINDOW_SECONDS[kind] * tick_rate
            totals = defaultdict(int)
            for tick, weapon, steam_id, damage in damages.get(clip["steamId"], ()):
                if (weapon in DAMAGE_WEAPONS[kind] and steam_id in nearby
                        and activation - 2 <= tick <= activation + window):
                    totals[steam_id] += damage
            if totals:
                victim = max(totals, key=totals.get)
                effect = {"effect": "damage", "value": totals[victim]}
        if not effect:
            victim = min(nearby, key=nearby.get)
            effect = {"effect": "near"}
        if victim == clip["steamId"]:
            continue
        lead = round(SWITCH_LEAD_SECONDS[kind] * tick_rate)
        switch = max(clip["launchTick"] + tick_rate // 4, activation - lead)
        clip["pov"] = {"steamId": victim, "player": names.get(victim) or victim, "tick": switch,
                       "distance": round(nearby[victim]), **effect,
                       "offset": round((switch - clip["startTick"]) / tick_rate, 3)}
        assigned += 1
    return assigned
