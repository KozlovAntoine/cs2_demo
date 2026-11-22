import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from clip_library import FAVORITE_SUGGESTIONS, library_catalogue
from favorite_throw_context import CONTEXT_FOLDER, CONTEXT_VERSION

try:
    import numpy as np
except ModuleNotFoundError:
    local_parser = Path(__file__).resolve().parent / ".local" / "throw-parser"
    sys.path.append(str(local_parser))
    import numpy as np


SUGGESTION_RATE = 0.54
CAPTURE_TARGET_RECALL = 0.90


@dataclass
class Throw:
    map_name: str
    clip: dict
    context: dict

    @property
    def key(self):
        return f"{self.clip['exportId']}/{self.clip['file']}"

    @property
    def label(self):
        if self.clip["favorite"]:
            return 1, 1.0
        if self.clip["potentialDismissed"]:
            return 0, 1.0
        return 0, 0.65 if self.clip["watched"] else 0.0


def read_throws(output):
    context_by_demo = {}
    for path in (output / CONTEXT_FOLDER).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") == CONTEXT_VERSION and isinstance(data.get("clips"), dict):
                context_by_demo[path.stem] = data["clips"]
        except (OSError, ValueError, AttributeError):
            continue
    throws = []
    for match in library_catalogue(output)["matches"]:
        for clip in match["clips"]:
            key = f"{clip['exportId']}/{clip['file']}"
            demo = Path(clip["source"]).stem.casefold()
            throws.append(Throw(match["map"], clip, context_by_demo.get(demo, {}).get(key, {})))
    return sorted(throws, key=lambda throw: throw.key)


def bounded(value, scale):
    return max(-2, min(2, value / scale))


def features(throw):
    clip, context = throw.clip, throw.context
    kind = clip["kind"]
    lineup = clip.get("lineup") or {}
    controls = lineup.get("controls") or {}
    pitch = (lineup.get("angles") or [0, 0])[0]
    delta = [end - start for end, start in zip(clip["impact"], clip["launch"])]
    distance = math.hypot(*delta[:2])
    time = context.get("roundSeconds", 0)
    usage = clip.get("usage", {})
    values = {
        "long": bounded(distance, 900),
        "very-long": bounded(max(0, distance - 900), 900),
        "height-difference": bounded(delta[2], 200),
        "flight": bounded(context.get("flight", clip["duration"] - 6) - 2, 2),
        "pitch": bounded(pitch, 35),
        "flat-aim": 1 - min(abs(pitch) / 35, 1),
        "estimated": float(clip["estimated"]),
        "jump": float(bool(controls.get("jump"))),
        "crouch": float(bool(controls.get("crouch"))),
        "walk": float(bool(controls.get("walk"))),
        "moving": float(bool(controls.get("directions"))),
        "repeat-demos": math.log2(max(1, usage.get("demos", 1))) / 2,
        "repeat-count": math.log2(max(1, usage.get("total", 1))) / 2,
        "context": float(bool(context)),
        "early-round": float("roundSeconds" in context and 0 <= time <= 15),
        "round-seconds": bounded(time, 60),
        "thrower-speed": bounded(context.get("speed", 0), 180),
        "apex": bounded(context.get("apex", 0), 250),
        "bounces": bounded(context.get("bounces", 0), 3),
        "ct": float(context.get("side") == "3"),
        "enemy-blind": bounded(context.get("enemyBlind", 0), 3),
        "enemy-damage": bounded(context.get("enemyDamage", 0), 45),
    }
    if mouse := controls.get("mouse"):
        values[f"mouse:{mouse}"] = 1
    for direction in controls.get("directions", ()):
        values[f"move:{direction}"] = 1
    return {"bias": 1, f"kind:{kind}": 1, f"map:{throw.map_name}": 1, **values,
            **{f"{kind}:{name}": value for name, value in values.items()}}


def feature_matrix(throws, names=None):
    rows = [features(throw) for throw in throws]
    if names is None:
        names = sorted({name for row in rows for name in row})
    return np.array([[row.get(name, 0) for name in names] for row in rows], dtype="float32")


def sigmoid(values):
    return 1 / (1 + np.exp(-np.clip(values, -20, 20)))


def fit_favorite_model(features, labels, weights):
    # Weighted logistic regression. Regularization shrinks weak correlations toward zero.
    fit = weights > 0
    matrix = features[fit]
    y, w = labels[fit], weights[fit]
    coefficients = np.zeros(matrix.shape[1], dtype="float32")
    rate = float((w @ y + 1) / (w.sum() + 2))
    coefficients[0] = math.log(rate / (1 - rate))
    penalty = np.full(matrix.shape[1], 0.035, dtype="float32")
    penalty[0] = 0
    for _ in range(350):
        errors = (sigmoid(matrix @ coefficients) - y) * w
        coefficients -= 0.2 * (matrix.T @ errors / w.sum() + penalty * coefficients)
    return coefficients


def geometry_similarity(throws, reviewed):
    similarity = np.zeros((len(throws), len(reviewed)), dtype="float32")
    by_map_kind = defaultdict(list)
    for index, throw in enumerate(throws):
        by_map_kind[throw.map_name, throw.clip["kind"]].append(index)
    reviewed_columns = {index: column for column, index in enumerate(reviewed)}
    for indices in by_map_kind.values():
        columns = [reviewed_columns[index] for index in indices if index in reviewed_columns]
        if not columns:
            continue
        train = [reviewed[column] for column in columns]
        launch, impact = (np.array([throws[index].clip[field] for index in indices], dtype="float32")
                          for field in ("launch", "impact"))
        aim_samples = [(throws[index].clip.get("lineup") or {}).get("angles") for index in indices]
        has_aim = np.array([sample is not None for sample in aim_samples])
        angles = np.array([sample or [0, 0] for sample in aim_samples], dtype="float32")
        local = {index: row for row, index in enumerate(indices)}
        train_rows = [local[index] for index in train]
        start = np.sum(((launch[:, None] - launch[train_rows]) / [550, 550, 240]) ** 2, axis=2)
        end = np.sum(((impact[:, None] - impact[train_rows]) / [440, 440, 170]) ** 2, axis=2)
        aim = angles[:, None] - angles[train_rows]
        yaw_difference = (aim[:, :, 1] + 180) % 360 - 180
        aim_gap = ((aim[:, :, 0] / 45) ** 2 + (yaw_difference / 80) ** 2) * (has_aim[:, None] & has_aim[train_rows])
        similarity[np.ix_(indices, columns)] = (0.65 * np.exp(-0.5 * (start + end + aim_gap))
                                                  + 0.35 * np.exp(-0.5 * (0.2 * start + end)))
    return similarity


def predictions(matrix, similarity, reviewed, labels, weights):
    base = sigmoid(matrix @ fit_favorite_model(matrix, labels, weights))
    # Correct the general score using comparable launches and activations on the same map.
    evidence = weights[reviewed]
    residual = (labels[reviewed] - base[reviewed]) * evidence
    adjustment = (similarity @ residual) / (3 + similarity @ evidence)
    return np.clip(base + adjustment, 0.001, 0.999)


@dataclass
class CaptureRanker:
    references: list
    columns: list
    coefficients: np.ndarray
    residual: np.ndarray
    weights: np.ndarray
    threshold: float
    favorites: int
    recovered: int

    def scores(self, candidates):
        if not candidates:
            return []
        matrix = feature_matrix(candidates, self.columns)
        first = len(self.references)
        similarity = geometry_similarity([*self.references, *candidates], np.arange(first))[first:]
        base = sigmoid(matrix @ self.coefficients)
        adjustment = (similarity @ self.residual) / (3 + similarity @ self.weights)
        return np.clip(base + adjustment, 0.001, 0.999)


def train_capture_ranker(output, threshold=None):
    references = [throw for throw in read_throws(output) if throw.label[1]]
    positive_count = sum(throw.label[0] for throw in references)
    size = len(references)
    by_demo = defaultdict(list)
    for index, throw in enumerate(references):
        by_demo[throw.clip["source"].casefold()].append(index)
    if positive_count < 5 or size - positive_count < 5 or len(by_demo) < 2:
        raise ValueError("Le tri par score a besoin de 5 favoris, 5 clips vus ou écartés et de retours sur deux démos.")

    # Reproduce the inputs of a new capture, which has no history in other demos yet.
    novel = [replace(throw, clip={**throw.clip, "usage": {"demos": 1, "total": 1}}) for throw in references]
    examples = [*references, *novel]
    columns = sorted({name for throw in examples for name in features(throw)})
    matrix = feature_matrix(examples, columns)
    reviewed = np.arange(size)
    similarity = geometry_similarity(examples, reviewed)
    labels, weights = np.array([throw.label for throw in references] + [(0, 0)] * size, dtype="float32").T
    validation = np.zeros(size)
    for test in by_demo.values():
        training = weights.copy()
        training[test] = 0
        validation[test] = predictions(matrix, similarity, reviewed, labels, training)[np.array(test) + size]
    favorite_scores = np.sort(validation[labels[:size] == 1])
    if threshold is None:
        wanted = math.ceil(CAPTURE_TARGET_RECALL * len(favorite_scores))
        # Round down to the nearest lower thousandth so the cutoff can use a strict inequality.
        threshold = (math.ceil(float(favorite_scores[-wanted]) * 1000) - 1) / 1000
    coefficients = fit_favorite_model(matrix[:size], labels[:size], weights[:size])
    residual = (labels[:size] - sigmoid(matrix[:size] @ coefficients)) * weights[:size]
    return CaptureRanker(references, columns, coefficients, residual, weights[:size], threshold,
                         positive_count, int(np.count_nonzero(favorite_scores > threshold)))


def top_indices(scores, groups, rate):
    return [index for candidates in groups
            for index in sorted(candidates, key=lambda i: -scores[i])[:math.ceil(len(candidates) * rate)]]


def evaluate(scores, labels, groups, rate):
    picks = top_indices(scores, groups, rate)
    hits = int(labels[picks].sum())
    return len(picks), hits, hits / len(picks), hits / labels.sum()


def rank_throws(throws, rate):
    labels, weights = np.array([throw.label for throw in throws], dtype="float32").T
    reviewed = np.flatnonzero(weights)
    if sum(labels) < 5 or len(reviewed) - sum(labels) < 5:
        raise ValueError("Il faut au moins 5 favoris et 5 clips vus ou écartés.")
    matrix = feature_matrix(throws)
    similarity = geometry_similarity(throws, reviewed)
    scores = predictions(matrix, similarity, reviewed, labels, weights)
    by_demo = defaultdict(list)
    by_map = defaultdict(list)
    for index in reviewed:
        by_demo[throws[index].clip["source"].casefold()].append(index)
        by_map[throws[index].map_name].append(index)
    print(f"{int(labels.sum())} favoris · {len(reviewed) - int(labels.sum())} vus sans étoile ou écartés "
          f"· {len(by_demo)} démos avec des retours")
    if len(by_demo) < 2:
        return scores

    validation = scores.copy()
    baseline = scores.copy()
    for test in by_demo.values():
        training_weights = weights.copy()
        training_weights[test] = 0
        validation[test] = predictions(matrix, similarity, reviewed, labels, training_weights)[test]
        seen = defaultdict(list)
        for index in np.flatnonzero(training_weights):
            seen[throws[index].clip["kind"]].append(index)
        for index in test:
            same_kind = seen[throws[index].clip["kind"]]
            baseline[index] = (1 + labels[same_kind].sum()) / (4 + len(same_kind))
    # Previously opened clips are scored by a model that did not see their demo's feedback.
    scores[reviewed] = validation[reviewed]
    for name, ranking in (("Classement", validation), ("Taux de favoris par type", baseline)):
        count, hits, precision, recall = evaluate(ranking, labels, by_demo.values(), rate)
        print(f"{name} sur démos laissées de côté · {hits}/{count} favoris en tête "
              f"({precision:.1%}) · {recall:.1%} des favoris retrouvés")
    if len(by_map) > 1:
        for test in by_map.values():
            training_weights = weights.copy()
            training_weights[test] = 0
            validation[test] = predictions(matrix, similarity, reviewed, labels, training_weights)[test]
        count, hits, precision, recall = evaluate(validation, labels, by_map.values(), rate)
        print(f"Classement sur cartes laissées de côté · {hits}/{count} favoris en tête "
              f"({precision:.1%}) · {recall:.1%} des favoris retrouvés")
    return scores


def save_suggestions(output, throws, scores, rate):
    by_map = defaultdict(list)
    for index, throw in enumerate(throws):
        if not throw.clip["favorite"] and not throw.clip["potentialDismissed"]:
            by_map[throw.map_name].append(index)
    picks = top_indices(scores, by_map.values(), rate)
    suggestions = {throws[index].key: round(float(scores[index]), 6) for index in picks}
    temporary = output / f".potential-favorites-{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(suggestions, separators=(",", ":")) + "\n", encoding="utf-8")
        temporary.replace(output / FAVORITE_SUGGESTIONS)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"{len(picks)} suggestions sur {sum(map(len, by_map.values()))} clips candidats "
          f"· {sum(bool(throw.context) for throw in throws)}/{len(throws)} avec contexte de démo")
    for map_name in sorted({throws[index].map_name for index in picks}):
        clips = [throws[index].clip for index in picks if throws[index].map_name == map_name]
        print(f"{map_name} · {len(clips)} suggestions · {sum(not clip['watched'] for clip in clips)} encore à traiter")


def main():
    parser = argparse.ArgumentParser(description="Recalcule les potentiels favoris de la vidéothèque.")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "exports")
    parser.add_argument("--rate", type=float, default=SUGGESTION_RATE, help="Part suggérée par carte (0 à 1 exclu).")
    args = parser.parse_args()
    if not 0 < args.rate < 1:
        parser.error("--rate doit être compris entre 0 et 1 (exclus).")
    output = args.output.resolve(strict=True)
    throws = read_throws(output)
    if not throws:
        parser.error("Aucun clip dans la vidéothèque.")
    try:
        scores = rank_throws(throws, args.rate)
    except ValueError as error:
        parser.error(str(error))
    save_suggestions(output, throws, scores, args.rate)


if __name__ == "__main__":
    main()
