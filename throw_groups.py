import math
from collections import Counter, defaultdict


RADAR_GROUP_RADIUS = 4  # Percent of the radar width.


def group_throws(clips, previous, radar):
    if not radar:
        return clips, Counter(id(clip) for clip in clips)

    anchors = {"launch": {False: [], True: []}, "impact": {False: [], True: []}}
    representatives = defaultdict(list)
    units = radar["units"]

    def group(point, endpoint):
        x, y, z = point
        left = (x - radar["originX"]) / units * 100
        top = (radar["originY"] - y) / units * 100
        if not (0 <= left <= 100 and 0 <= top <= 100):
            return None
        lower = bool(radar["lower"]) and z < radar["thresholdZ"]
        spots = anchors[endpoint][lower]
        for index, (anchor_x, anchor_y) in enumerate(spots):
            if math.hypot(left - anchor_x, top - anchor_y) <= RADAR_GROUP_RADIUS:
                return lower, index
        spots.append((left, top))
        return lower, len(spots) - 1

    def pair(clip):
        launch = group(clip["launch"], "launch")
        impact = group(clip["impact"], "impact")
        if launch is not None and impact is not None:
            return clip["kind"], launch, impact
        return None

    for clip in previous:
        key = pair(clip)
        if key is not None:
            representatives[key].append(clip)

    kept = []
    counts = Counter()
    for clip in clips:
        key = pair(clip)
        group_clips = representatives.get(key)
        if not group_clips:
            kept.append(clip)
            group_clips = [clip]
            if key is not None:
                representatives[key] = group_clips
        counts[id(group_clips[0])] += 1

    for first, *others in representatives.values():
        if others and (count := counts[id(first)]):
            for clip in others:
                counts[id(clip)] = count
    return kept, counts
