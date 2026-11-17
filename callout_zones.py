import json
import math
from uuid import uuid4


MAX_VERTICES = 128
MAX_NAME_LENGTH = 64


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a, b, p):
    return all(min(a[i], b[i]) <= p[i] <= max(a[i], b[i]) for i in (0, 1))


def segments_intersect(a, b, c, d):
    first = cross(a, b, c), cross(a, b, d)
    second = cross(c, d, a), cross(c, d, b)
    return (first[0] * first[1] < 0 and second[0] * second[1] < 0) or any((
        first[0] == 0 and on_segment(a, b, c), first[1] == 0 and on_segment(a, b, d),
        second[0] == 0 and on_segment(c, d, a), second[1] == 0 and on_segment(c, d, b),
    ))


def validate_polygon(vertices):
    if not isinstance(vertices, list) or not 3 <= len(vertices) <= MAX_VERTICES:
        raise ValueError(f"Trace une zone de 3 à {MAX_VERTICES} points.")
    if any(not isinstance(point, list) or len(point) != 2 or any(
        type(value) not in (int, float) or not -100_000 <= value <= 100_000 for value in point
    ) for point in vertices):
        raise ValueError("Les coordonnées de la zone sont invalides.")

    points = [tuple(round(value, 3) for value in point) for point in vertices]
    if len(set(points)) != len(points):
        raise ValueError("Chaque sommet doit avoir une position différente.")
    edges = list(zip(points, points[1:] + points[:1]))
    area = abs(math.fsum(a[0] * b[1] - a[1] * b[0] for a, b in edges)) / 2
    for index, (a, b) in enumerate(edges):
        for other in range(index + 2, len(edges) - (index == 0)):
            if segments_intersect(a, b, *edges[other]):
                raise ValueError("Les côtés de la zone ne peuvent pas se croiser.")
    if area <= 0:
        raise ValueError("La zone doit couvrir une surface.")
    return points, area


def zones_path(output, map_name):
    return output / f".callouts-{map_name}.json"


def read_zones(output, map_name):
    path = zones_path(output, map_name)
    if path.is_symlink():
        raise ValueError("Le fichier des zones doit se trouver dans la vidéothèque.")
    try:
        zones = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(zones, list) or any(not isinstance(zone, dict) for zone in zones):
        raise ValueError("Le fichier des zones est illisible.")
    return zones


def change_zone(output, map_name, data):
    zones = read_zones(output, map_name)
    action = data.get("action")
    zone_id = data.get("id")
    existing = next((zone for zone in zones if zone["id"] == zone_id), None)
    if action == "delete" and existing:
        zones.remove(existing)
    elif action == "save":
        name = data.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME_LENGTH:
            raise ValueError(f"Saisis un nom de 1 à {MAX_NAME_LENGTH} caractères.")
        placement = data.get("placement")
        if placement not in (None, "front", "back"):
            raise ValueError("La position de la zone est invalide.")
        if zone_id is not None and not existing:
            raise ValueError("Cette zone a été supprimée. Rouvre l’éditeur pour actualiser la carte.")
        level = existing["level"] if existing else data.get("level")
        if level not in ("upper", "lower"):
            raise ValueError("Le niveau de la zone est invalide.")
        zone = existing if existing else {"id": uuid4().hex, "level": level}
        if not existing or "points" in data:
            zone["points"], zone["area"] = validate_polygon(data.get("points"))
        zone["name"] = name.strip()
        if placement:
            priorities = [0, *(item.get("priority", 0) for item in zones if item is not zone and item["level"] == level)]
            zone["priority"] = max(priorities) + 1 if placement == "front" else min(priorities) - 1
        if not existing:
            zones.append(zone)
        zone_id = zone["id"]
    else:
        raise ValueError("Cette action ne correspond à aucune zone.")

    output.mkdir(parents=True, exist_ok=True)
    temporary = output / f".callouts-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(zones, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(zones_path(output, map_name))
    finally:
        temporary.unlink(missing_ok=True)
    return {"zones": zones, "id": zone_id}
