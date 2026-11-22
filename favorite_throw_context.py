import argparse
import csv
import json
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from uuid import uuid4

from clip_library import playable_matches, published_catalogues
from grenade_data import KINDS, point, unpack


CONTEXT_FOLDER = ".favorite-throw-context"
CONTEXT_VERSION = 1


def demo_clips(output):
    clips = defaultdict(dict)
    for folder, _, catalogue in published_catalogues(output):
        for match in playable_matches(folder, catalogue):
            for clip in match["clips"]:
                clips[match["demo"].casefold()][clip_key(folder, clip)] = clip
    return clips


def clip_key(folder, clip):
    return f"{folder.name}/{Path(clip['video']).name}"


def extract_context(folder, demo, clips):
    def rows(name):
        with (folder / f"{demo.stem}_{name}.csv").open(encoding="utf-8", newline="") as source:
            yield from csv.reader(source)

    tick_rate = round(float(next(rows("demo"))[11]))
    freeze_ends = {row[0]: int(row[2]) for row in rows("rounds")}
    shots = {(row[1], row[4], row[9]): row for row in rows("shots") if row[2] in KINDS and row[4] != "0"}
    bounces = Counter((row[1], row[3], row[8]) for row in rows("grenade_bounces"))
    damage = Counter()
    for row in rows("damages"):
        if row[16] in {"HE Grenade", "Molotov", "Incendiary Grenade"} and row[9] != row[13]:
            damage[row[1], row[19], row[8]] += max(0, min(int(row[2]), int(row[4])))
    flash = Counter()
    for row in rows("players_flashed"):
        if row[5] != row[9]:
            flash[row[1], int(row[0]), row[7]] += float(row[2])

    throws = defaultdict(list)
    for row in rows("grenade_positions"):
        round_number, grenade, projectile, weapon, steam_id, player, side = row[:7]
        if not (kind := KINDS.get(weapon)):
            continue
        shot = shots.get((round_number, projectile, steam_id))
        if shot:
            tick, start = int(shot[0]), point(shot, 5)
        else:
            ticks = unpack(row[13], "I")
            if not ticks:
                continue
            tick = ticks[0]
            start = tuple(unpack(column, "f")[0] for column in row[14:17])
        key = (round_number, kind, player, tick)
        throws[key].append((start, row, grenade, projectile, steam_id, side, shot))

    context = {}
    for key, clip in clips.items():
        round_number = str(clip["round"])
        launch = clip["launchTick"]
        matches = [throw for throw in throws[round_number, clip["kind"], clip["player"], launch]
                   if all(abs(a - b) < 0.01 for a, b in zip(throw[0], clip["launch"]))]
        if len(matches) != 1:
            continue
        start, row, grenade, projectile, steam_id, side, shot = matches[0]
        result = {"side": side, "bounces": bounces[round_number, projectile, steam_id],
                  "flight": (clip["activationTick"] - launch) / tick_rate}
        if round_number in freeze_ends:
            result["roundSeconds"] = (launch - freeze_ends[round_number]) / tick_rate
        if heights := unpack(row[16], "f"):
            result["apex"] = max(0, max(heights) - max(start[2], clip["impact"][2]))
        if shot:
            vx, vy, _ = point(shot, 15)
            result["speed"] = (vx ** 2 + vy ** 2) ** 0.5
        if clip["kind"] == "flash" and not clip["estimated"]:
            result["enemyBlind"] = flash[round_number, clip["activationTick"], steam_id]
        if clip["kind"] in ("he", "molo"):
            result["enemyDamage"] = damage[round_number, grenade, steam_id]
        context[key] = result
    return context


def cache_context(output, name, context):
    directory = output / CONTEXT_FOLDER
    directory.mkdir(exist_ok=True)
    temporary = directory / f".{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps({"version": CONTEXT_VERSION, "clips": context},
                                        separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(directory / f"{Path(name).stem}.json")
    finally:
        temporary.unlink(missing_ok=True)


def refresh_context(output, sources, installation, clips):
    from utility_video import describe_archive, load_demos, run_demo_analyzer

    completed = set()
    for source in sources:
        if source.suffix.lower() == ".rar":
            members = [member for member in describe_archive(source) if PurePosixPath(member).name.casefold() in clips]
        elif source.suffix.lower() == ".dem" and source.name.casefold() in clips:
            members = [None]
        else:
            continue
        for member in members:
            name = (PurePosixPath(member).name if member else source.name).casefold()
            if name in completed:
                continue
            with tempfile.TemporaryDirectory(prefix="favorite-context-", dir=output) as directory:
                work = Path(directory).resolve()
                demo, = load_demos(source, work, None, (member,) if member else ())
                csv_folder = work / "csv"
                csv_folder.mkdir()
                run_demo_analyzer(installation, demo, csv_folder, stdin=subprocess.DEVNULL,
                                  env={**os.environ, "TEMP": str(work), "TMP": str(work)})
                context = extract_context(csv_folder, demo, clips[name])
            if context:
                cache_context(output, name, context)
                completed.add(name)
            print(f"{name} · contexte retrouvé pour {len(context)}/{len(clips[name])} clips", flush=True)
    return completed


def main():
    from utility_video import find_csdm

    parser = argparse.ArgumentParser(description="Enrichit le tri des clips existants depuis les démos GOTV.")
    parser.add_argument("demos", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "exports")
    parser.add_argument("--csdm", type=Path)
    args = parser.parse_args()
    output = args.output.resolve(strict=True)
    clips = demo_clips(output)
    completed = refresh_context(output, [source.resolve(strict=True) for source in args.demos],
                                find_csdm(args.csdm), clips)
    print(f"{len(completed)}/{len(clips)} démos de la vidéothèque analysées.")


if __name__ == "__main__":
    main()
