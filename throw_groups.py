import math
from collections import Counter, defaultdict


RADAR_GROUP_RADIUS = 4  # Percent of the radar width.
# Source coordinates use inches. Valve documents this in src/public/vphysics_interface.h.
METERS_PER_GAME_UNIT = 0.0254
THROW_GROUP_SIZE_METERS = 2
THROW_GROUP_SIZE_UNITS = THROW_GROUP_SIZE_METERS / METERS_PER_GAME_UNIT


def positions_close(first, second):
    return (len(first) == len(second) == 3 and all(
        math.isfinite(a) and math.isfinite(b) and abs(a - b) <= THROW_GROUP_SIZE_UNITS
        for a, b in zip(first, second)
    ))


def group_throws(clips, previous, radar, *, removed=()):
    representatives = defaultdict(list)
    for clip in (*previous, *removed):
        representatives[clip["kind"]].append(clip)

    kept = []
    counts = Counter()
    for clip in clips:
        candidates = representatives[clip["kind"]]
        # Both 3D endpoints must match the same throw, including removed videos.
        matches = [id(candidate) for candidate in candidates
                   if positions_close(clip["launch"], candidate["launch"])
                   and positions_close(clip["impact"], candidate["impact"])]
        if matches:
            counts.update(matches)
            continue
        candidates.append(clip)
        kept.append(clip)
        counts[id(clip)] += 1
    return kept, counts
