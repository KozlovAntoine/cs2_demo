"""1080p captures with the enemy point of view for the clips of a compilation.

Library clips may come from older 720p captures without enemy POV. Before a compilation, each clip is captured
again from its demo, at 1080p, with the victim's view when an enemy is close to the impact. The result is kept in
the export's `hq/` folder and reused by later compilations.

The demo is the copy kept with the round replay, or the original archive or .dem when it can still be found.
When neither exists, the compilation stops and lists the matches to import again.
"""

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from clip_library import clip_paths, read_catalogue
from job_control import JobCancelled, job_checkpoint
from enemy_pov import clip_povs
from job_log import current_job_log
from replay_library import list_replays, saved_demo
from utility_video import analyze_demo, clip_videos, create_video_job, describe_archive, load_demos, record_video, reserve_export


HQ_FOLDER = "hq"
HQ_HEIGHT = 1080
# 2: up to two enemy POVs per clip, chat hidden. 3: the enemy view replays the detonation.
HQ_VERSION = 3
SEARCH_FOLDERS = ("Downloads", "Desktop", "Documents")
# HLTV demos are named team-vs-team-m1-map.dem, inside event-team-vs-team-bo3-<id>.rar archives.
DEMO_NAME = re.compile(r"^(?P<first>.+?)-vs-(?P<second>.+?)-m\d+-", re.I)
LAUNCH_TOLERANCE = 8


class MissingDemos(ValueError):
    def __init__(self, missing):
        self.missing = missing
        names = ", ".join(item["label"] for item in missing)
        super().__init__(f"Démo introuvable pour {names}. Réimporte ces matchs pour les réenregistrer en 1080p, "
                         "ou décoche le réenregistrement pour utiliser les clips actuels.")


def hq_paths(output, export_id, filename):
    folder = output / export_id / HQ_FOLDER
    return folder / filename, folder / f"{Path(filename).stem}.json"


def read_hq(output, export_id, filename):
    """The 1080p capture of a clip and its POV, when it was already made."""
    video, meta = hq_paths(output, export_id, filename)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not video.is_file() or data.get("version") != HQ_VERSION or data.get("height", 0) < HQ_HEIGHT:
        return None
    return {"file": video, "povs": data.get("povs") or []}


def library_entry(output, export_id, filename):
    manifest, clip_file = clip_paths(output, export_id, filename)
    catalogue = read_catalogue(manifest)
    for match in catalogue["matches"]:
        for clip in match["clips"]:
            if clip.get("video") == f"clips/{filename}":
                return catalogue, match, clip
    raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")


def humanize(text):
    return re.sub(r"[-_]+", " ", text).strip()


def match_info(output, export_id, catalogue, match, replays):
    """What helps to find the match again: teams, score, date, archive and an HLTV search."""
    title = catalogue.get("title") or export_id
    stem = Path(title).stem
    parsed = DEMO_NAME.match(match["demo"])
    event = None
    if parsed and (marker := f"-{parsed['first']}-vs-{parsed['second']}-") in stem:
        event = humanize(stem.split(marker, 1)[0]).title()
    replay = next((item for item in replays if item["demo"] == match["demo"] and item["map"] == match["map"]), None)
    teams = [team["name"] for team in (replay or {}).get("teams") or [] if team.get("name")]
    scores = [team.get("score") for team in (replay or {}).get("teams") or []]
    score = " – ".join(map(str, scores)) if scores and all(isinstance(value, int) for value in scores) else None
    if not teams and parsed:
        teams = [humanize(parsed["first"]).title(), humanize(parsed["second"]).title()]
    query = " ".join(teams) if teams else humanize(stem)
    map_label = re.sub(r"^(de|cs|ar)_", "", match["map"]).title()
    return {
        "exportId": export_id, "demo": match["demo"], "map": map_label, "archive": title,
        "event": event,
        "teams": teams, "score": score, "date": (replay or {}).get("date"),
        "hltv": f"https://www.hltv.org/search?query={quote(query)}",
        "label": f"{' vs '.join(teams) or humanize(Path(title).stem)} · {map_label}",
    }


def archive_candidates(catalogue):
    """The original archive or demo: its recorded path, or the same file name in the usual folders."""
    found = []
    if recorded := catalogue.get("sourcePath"):
        found.append(Path(recorded))
    if title := catalogue.get("title"):
        found.extend(Path.home() / folder / Path(title).name for folder in SEARCH_FOLDERS)
    return [path for path in dict.fromkeys(found) if path.is_file()]


def demo_sources(output, catalogue, match, replays):
    """Ways to get the demo back, best first: (kind, path, archive member)."""
    sources = []
    for replay in replays:
        if replay["demo"] == match["demo"] and replay["map"] == match["map"] and replay.get("demoSaved"):
            if demo := saved_demo(output, replay["id"]):
                sources.append(("stored", demo, None))
    for path in archive_candidates(catalogue):
        if path.suffix.lower() == ".dem":
            if path.name == match["demo"]:
                sources.append(("file", path, None))
            continue
        try:
            members = describe_archive(path)
        except (OSError, ValueError):
            continue
        member = next((name for name in members if Path(name).name == match["demo"]), None)
        if member:
            sources.append(("archive", path, member))
    return sources


def same_throw(library_clip, throw):
    return (throw.get("round") == library_clip.get("round") and throw.get("kind") == library_clip.get("kind")
            and throw.get("player") == library_clip.get("player")
            and abs(throw.get("launchTick", -10_000) - library_clip.get("launchTick", 10_000)) <= LAUNCH_TOLERANCE)


def stage_demo(source, work):
    kind, path, member = source
    if kind == "archive":
        return load_demos(path, work, None, (member,))[0]
    staged = work / "demos"
    staged.mkdir(exist_ok=True)
    target = staged / path.name
    try:
        target.hardlink_to(path)
    except OSError:
        shutil.copyfile(path, target)
    return target


def capture_group(output, installation, entries, sources, report):
    """Analyses one demo, finds each clip's throw again, then captures them in one CS2 session."""
    log = current_job_log()
    for source in sources:
        job_checkpoint()
        with tempfile.TemporaryDirectory(prefix=".hq-capture-", dir=output) as directory:
            work = Path(directory)
            report("Réenregistrement", f"Analyse de {entries[0]['match']['demo']}…")
            demo = stage_demo(source, work)
            _, tick_rate, throws, _ = analyze_demo(installation, demo, "all", work, enemy_pov=True)
            pairs = []
            for entry in entries:
                throw = next((item for item in throws if same_throw(entry["clip"], item)), None)
                if throw:
                    pairs.append((entry, throw))
            if not pairs:
                if log:
                    log.warning("Aucun lancer retrouvé dans %s (%s)", demo.name, source[0])
                continue
            clips = [throw for _, throw in pairs]
            config, render_folder = create_video_job(demo, clips, tick_rate, work / "capture", HQ_HEIGHT)
            with_pov = sum(bool(clip_povs(clip)) for clip in clips)
            report("Réenregistrement", f"CS2 enregistre {len(clips)} {'clip' if len(clips) == 1 else 'clips'} en 1080p"
                   f" · POV ennemie {with_pov}/{len(clips)} · {demo.name}")
            source_options = {"source": clips[0]["analysisSource"]} if "analysisSource" in clips[0] else {}
            record_video(installation, demo, config, lambda: None, **source_options)
            done = []
            videos = clip_videos(render_folder, clips)
            for number, (entry, throw) in enumerate(pairs, 1):
                if not (video := videos.get(number)):
                    continue
                target, meta = hq_paths(output, entry["exportId"], entry["file"])
                target.parent.mkdir(exist_ok=True)
                shutil.move(video, target)
                meta.write_text(json.dumps({
                    "version": HQ_VERSION, "height": HQ_HEIGHT, "povs": clip_povs(throw), "source": source[0],
                    "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                done.append(entry)
            return done, len(pairs)
    return [], 0


def prepare_hq_clips(output, installation, references, report):
    """Makes sure each referenced clip has a 1080p capture. Returns {(exportId, file): {"file", "povs"}}."""
    output = output.resolve()
    ready, todo = {}, []
    for reference in references:
        key = (reference["exportId"], reference["file"])
        if hq := read_hq(output, *key):
            ready[key] = hq
        else:
            catalogue, match, clip = library_entry(output, *key)
            todo.append({"exportId": key[0], "file": key[1], "catalogue": catalogue, "match": match, "clip": clip})
    if not todo:
        return ready
    replays = list_replays(output)
    groups = {}
    for entry in todo:
        groups.setdefault((entry["exportId"], entry["match"]["demo"]), []).append(entry)
    plans, missing = [], []
    for entries in groups.values():
        first = entries[0]
        sources = demo_sources(output, first["catalogue"], first["match"], replays)
        if sources:
            plans.append((entries, sources))
        else:
            info = match_info(output, first["exportId"], first["catalogue"], first["match"], replays)
            missing.append({**info, "clips": len(entries)})
    if missing:
        raise MissingDemos(missing)
    with reserve_export(output):
        for number, (entries, sources) in enumerate(plans, 1):
            report("Réenregistrement", f"Démo {number}/{len(plans)} · {len(entries)} "
                   f"{'clip' if len(entries) == 1 else 'clips'} à réenregistrer")
            try:
                done, found = capture_group(output, installation, entries, sources, report)
            except JobCancelled:
                raise
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                # A failed capture keeps the current clips of this demo instead of stopping the compilation.
                if log := current_job_log():
                    log.exception("Réenregistrement impossible · %s", entries[0]["match"]["demo"])
                report("Réenregistrement", f"Capture impossible pour {entries[0]['match']['demo']} · {error}")
                done, found = [], len(entries)
            for entry in done:
                ready[(entry["exportId"], entry["file"])] = read_hq(output, entry["exportId"], entry["file"])
            if len(done) < len(entries):
                lost = len(entries) - len(done)
                report("Réenregistrement", f"{lost} {'clip gardé' if lost == 1 else 'clips gardés'} en version actuelle"
                       f" ({'lancer introuvable dans la démo' if found < len(entries) else 'capture incomplète'}).")
    return ready
