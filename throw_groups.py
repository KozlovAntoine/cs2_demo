import math
from collections import Counter, defaultdict


RADAR_GROUP_RADIUS = 4  # Percent of the radar width.


def group_throws(clips, previous, radar):
    if not radar:
        return clips, Counter(id(clip) for clip in clips)

    representatives = defaultdict(list)
    units = radar["units"]

    def position(point):
        x, y, z = point
        left = (x - radar["originX"]) / units * 100
        top = (radar["originY"] - y) / units * 100
        if not (0 <= left <= 100 and 0 <= top <= 100):
            return None
        lower = bool(radar["lower"]) and z < radar["thresholdZ"]
        return left, top, lower

    def close(first, second):
        x, y, lower = first
        other_x, other_y, other_lower = second
        return lower == other_lower and math.hypot(x - other_x, y - other_y) <= RADAR_GROUP_RADIUS

    for clip in previous:
        launch, impact = position(clip["launch"]), position(clip["impact"])
        if launch is not None and impact is not None:
            representatives[clip["kind"]].append((id(clip), launch, impact))

    kept = []
    counts = Counter()
    for clip in clips:
        launch, impact = position(clip["launch"]), position(clip["impact"])
        if launch is not None and impact is not None:
            candidates = representatives[clip["kind"]]
            # Both endpoints must match the same clip, regardless of the map markers.
            matches = [candidate for candidate, other_launch, other_impact in candidates
                       if close(launch, other_launch) and close(impact, other_impact)]
            if matches:
                counts.update(matches)
                continue
            candidates.append((id(clip), launch, impact))
        kept.append(clip)
        counts[id(clip)] += 1
    return kept, counts
