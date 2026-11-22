import json
import math
import os
import re
import stat
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from grenade_data import UTILITY_KINDS
from throw_groups import RADAR_GROUP_RADIUS, THROW_GROUP_SIZE_UNITS
from throw_index import indexed_maps


CATALOGUE_PREFIX = "window.utilityCatalogue = "
EXPORT_NAME = re.compile(r"[\w-]+")
CLIP_NAME = re.compile(r"[\w-]+\.mp4")
CLIP_VIDEO = re.compile(r"clips/[\w-]+\.mp4")
CLIP_FLAGS = ("favorite", "watched", "potentialDismissed")
THROW_USAGE_PREFIX = ".throw-usage-"
FAVORITE_SUGGESTIONS = ".potential-favorites.json"


def export_url(folder):
    return f"/exports/{quote(folder.name)}"


def read_catalogue(manifest):
    source = manifest.read_text(encoding="utf-8").strip()
    if not source.startswith(CATALOGUE_PREFIX):
        raise ValueError("Catalogue illisible.")
    return json.loads(source[len(CATALOGUE_PREFIX):].removesuffix(";"))


def published_catalogues(output, *, completed_only=False):
    for manifest in output.glob("*/catalogue.js"):
        folder = manifest.parent
        try:
            if (folder.resolve() != folder or (completed_only and (folder / "work").exists())
                    or not (folder / "index.html").is_file() or manifest.resolve() != manifest):
                continue
            catalogue = read_catalogue(manifest)
            if isinstance(catalogue.get("matches"), list):
                yield folder, manifest.stat().st_mtime, catalogue
        except (OSError, AttributeError, TypeError, ValueError):
            continue


def published_clips(match):
    return (clip for clip in match["clips"] if clip.get("kind") in UTILITY_KINDS
            and CLIP_VIDEO.fullmatch(clip.get("video", "")))


def playable_matches(folder, catalogue):
    try:
        with os.scandir(folder / "clips") as entries:
            videos = {os.path.normcase(entry.name) for entry in entries if entry.is_file()}
    except (FileNotFoundError, NotADirectoryError):
        return
    for match in catalogue["matches"]:
        clips = [clip for clip in published_clips(match)
                 if os.path.normcase(clip["video"].removeprefix("clips/")) in videos]
        if clips:
            yield {**match, "clips": clips}


def existing_galleries(output):
    galleries = []
    for folder, date, data in published_catalogues(output):
        matches = list(playable_matches(folder, data))
        if matches:
            count = sum(len(match["clips"]) for match in matches)
            galleries.append({"title": data["title"], "maps": [match["map"] for match in matches],
                              "count": count, "url": export_url(folder) + "/index.html", "date": date})
    return sorted(galleries, key=lambda item: item["date"], reverse=True)


def read_clip_states(folder):
    path = folder / ".clip-state.json"
    try:
        if path.resolve() == path:
            states = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(states, dict):
                return {file: state for file, state in states.items() if isinstance(state, dict)}
    except (OSError, ValueError):
        pass
    return {}


def throw_usage_path(folder, filename):
    return folder / f"{THROW_USAGE_PREFIX}{filename}.json"


def load_throw_usage(path):
    with open(path, encoding="utf-8") as source:
        usage = json.load(source)
    if not isinstance(usage, dict):
        return {}
    return {demo: entry for demo, entry in usage.items()
            if type(count := throw_count(entry)) is int and count > 0}


def throw_count(entry):
    return entry.get("count") if isinstance(entry, dict) else entry


def read_throw_usage(path):
    try:
        if path.resolve() == path:
            return load_throw_usage(path)
    except (OSError, ValueError):
        pass
    return {}


def throw_usage_summary(usage):
    throwers = set()
    activation_seconds = []
    side_occurrences = []
    timed = False
    for entry in usage.values():
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("throwers"), list):
            throwers.update(tuple(row) for row in entry["throwers"] if isinstance(row, list)
                            and len(row) == 3 and all(isinstance(value, str) for value in row))
        if isinstance(observations := entry.get("observations"), list):
            timed = True
            for row in observations:
                if not (isinstance(row, list) and len(row) == 6 and all(isinstance(v, str) for v in row[:4])):
                    continue
                steam_id, player, team, side, _, activation = row
                throwers.add((steam_id, player, team))
                if type(activation) in (int, float) and math.isfinite(activation):
                    activation_seconds.append(activation)
                else:
                    activation = None
                if side in ("t", "ct"):
                    side_occurrences.append([steam_id, player, team, side, activation])
            continue
        if isinstance(times := entry.get("timings"), list):
            timed = True
            for row in times:
                if isinstance(row, list) and len(row) == 2 and type(row[1]) in (int, float) and math.isfinite(row[1]):
                    activation_seconds.append(row[1])
    summary = {"usage": {"total": sum(map(throw_count, usage.values())), "demos": len(usage)},
               "throwers": [{"steamId": steam_id, "player": player, "team": team}
                            for steam_id, player, team in sorted(throwers)]}
    if timed:
        summary["activationSeconds"] = activation_seconds
    if side_occurrences:
        summary["sideOccurrences"] = side_occurrences
    return summary


@lru_cache(maxsize=16384)
def cached_throw_usage_summary(path, _version):
    return throw_usage_summary(load_throw_usage(path))


def throw_usage_summaries(folder):
    summaries = {}
    with os.scandir(folder) as entries:
        for entry in entries:
            name = os.path.normcase(entry.name)
            if not (name.startswith(THROW_USAGE_PREFIX) and name.endswith(".json")):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                if stat.S_ISLNK(info.st_mode) or reparse:
                    summary = throw_usage_summary(read_throw_usage(Path(entry.path)))
                elif stat.S_ISREG(info.st_mode):
                    # Exporters replace usage files atomically. Reuse counts only for the same file version.
                    version = (info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                    summary = cached_throw_usage_summary(entry.path, version)
                else:
                    continue
            except (OSError, ValueError):
                continue
            if summary["usage"]["demos"]:
                filename = name.removeprefix(THROW_USAGE_PREFIX).removesuffix(".json")
                summaries[filename] = summary
    return summaries


def throw_observations(occurrences):
    # The final two fields are seconds since freeze end. Keep identity and side on the same observation.
    return [[clip["steamId"], clip["player"], clip["team"], clip.get("side", ""),
             clip.get("roundLaunchSeconds"), clip.get("roundActivationSeconds")] for clip in occurrences]


def record_throw_usage(folder, filename, demo, occurrences):
    clip_file = folder / "clips" / filename
    if not clip_file.is_file():
        return False
    path = throw_usage_path(folder, filename)
    usage = read_throw_usage(path)
    entry = {"count": len(occurrences), "observations": throw_observations(occurrences)}
    if usage.get(demo) == entry:
        return True
    usage[demo] = entry
    temporary = folder / f".throw-usage-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(usage, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(path)
        if not clip_file.is_file():
            path.unlink(missing_ok=True)
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def throw_references(output, map_name):
    clips = []
    removed = []
    for folder, _, data in sorted(published_catalogues(output, completed_only=True), key=lambda item: item[1], reverse=True):
        for match in data["matches"]:
            if match["map"] != map_name:
                continue
            # Use one catalogue snapshot so a concurrent deletion cannot hide a throw.
            clips.extend({**clip, "exportId": folder.name} for clip in published_clips(match))
            removed.extend(clip for clip in match.get("removedThrows", ()) if clip.get("kind") in UTILITY_KINDS)
    return clips, removed


def read_favorite_suggestions(output):
    try:
        scores = json.loads((output / FAVORITE_SUGGESTIONS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(scores, dict):
        return {}
    return {key: value for key, value in scores.items() if type(value) in (float, int) and 0 < value <= 1}


def library_catalogue(output):
    maps = {}
    suggestions = read_favorite_suggestions(output)
    for folder, _, data in sorted(published_catalogues(output), key=lambda item: item[1], reverse=True):
        base = export_url(folder)
        states = read_clip_states(folder)
        try:
            summaries = throw_usage_summaries(folder)
        except OSError:
            summaries = {}
        for match in playable_matches(folder, data):
            clips = []
            for clip in match["clips"]:
                video = clip["video"]
                filename = video.removeprefix("clips/")
                state = states.get(filename, {})
                summary = summaries.get(os.path.normcase(filename), {})
                score = suggestions.get(f"{folder.name}/{filename}")
                suggestion = {"potentialScore": score} if score and state.get("potentialDismissed") is not True else {}
                clips.append({**clip, **summary, "video": f"{base}/{video}", "file": filename,
                              "exportId": folder.name, "source": match["demo"],
                              "calloutOverride": state.get("calloutOverride"),
                              **{flag: state.get(flag) is True for flag in CLIP_FLAGS}, **suggestion})
            entry = maps.setdefault(match["map"], {"map": match["map"], "radar": None, "clips": []})
            radar = match.get("radar")
            if radar and not entry["radar"]:
                entry["radar"] = {**radar, "upper": f"{base}/{radar['upper']}",
                                  "lower": f"{base}/{radar['lower']}" if radar.get("lower") else None}
            entry["clips"].extend(clips)
    for map_name in indexed_maps(output):
        maps.setdefault(map_name, {"map": map_name, "radar": None, "clips": []})["throwIndex"] = True
    return {"title": "Vidéothèque", "groupRadius": RADAR_GROUP_RADIUS, "groupBoxUnits": THROW_GROUP_SIZE_UNITS,
            "matches": [maps[map_name] for map_name in sorted(maps)]}


def clip_paths(output, export_id, filename):
    if (not isinstance(export_id, str) or not EXPORT_NAME.fullmatch(export_id)
            or not isinstance(filename, str) or not CLIP_NAME.fullmatch(filename)):
        raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")
    folder = output / export_id
    manifest = folder / "catalogue.js"
    clip_file = folder / "clips" / filename
    if (folder.resolve() != folder or manifest.resolve() != manifest or clip_file.resolve() != clip_file
            or not manifest.is_file()):
        raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")
    return manifest, clip_file


def find_library_clip(output, export_id, filename):
    manifest, clip_file = clip_paths(output, export_id, filename)
    catalogue = read_catalogue(manifest)
    for match in catalogue["matches"]:
        for clip in published_clips(match):
            if clip["video"] == f"clips/{filename}" and clip_file.is_file():
                return clip_file, match["map"], clip
    raise ValueError("Ce clip n’est pas référencé dans la vidéothèque.")


def set_clip_state(output, export_id, filename, data):
    updates = {flag: data[flag] for flag in CLIP_FLAGS if flag in data}
    if any(type(value) is not bool for value in updates.values()):
        raise ValueError("État de clip invalide.")
    if "calloutOverride" in data:
        name = data["calloutOverride"]
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 64):
            raise ValueError("Saisis un nom de zone de 1 à 64 caractères.")
        updates["calloutOverride"] = name.strip() if name is not None else None
    if not updates:
        raise ValueError("État de clip invalide.")
    clip_file, _, _ = find_library_clip(output, export_id, filename)
    folder = clip_file.parent.parent
    states = read_clip_states(folder)
    state = states.setdefault(filename, {})
    state.update(updates)

    temporary = folder / f".clip-state-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(states, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(folder / ".clip-state.json")
    finally:
        temporary.unlink(missing_ok=True)
    return state


def delete_clip(output, export_id, filename):
    manifest, clip_file = clip_paths(output, export_id, filename)
    folder = manifest.parent
    # An active export can replace the manifest when it publishes the next demo.
    if (folder / "work").exists():
        raise ValueError("Attends la fin de cet export pour supprimer ce clip.")

    catalogue = read_catalogue(manifest)
    original = manifest.read_bytes()
    removed = False
    for match in catalogue["matches"]:
        remaining = []
        for clip in match["clips"]:
            if clip.get("video") == f"clips/{filename}":
                match.setdefault("removedThrows", []).append(
                    {**{key: clip[key] for key in ("kind", "launch", "impact")}, "file": filename})
                removed = True
            else:
                remaining.append(clip)
        match["clips"] = remaining
    if not removed:
        return

    # Publish the index first. If Windows still holds the video open, restore the previous index.
    temporary = folder / f".catalogue-{uuid4().hex}.tmp"
    try:
        temporary.write_text(CATALOGUE_PREFIX + json.dumps(catalogue, ensure_ascii=True) + ";\n", encoding="utf-8")
        temporary.replace(manifest)
        try:
            clip_file.unlink(missing_ok=True)
        except OSError:
            temporary.write_bytes(original)
            temporary.replace(manifest)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    with suppress(OSError):
        throw_usage_path(folder, filename).unlink(missing_ok=True)
