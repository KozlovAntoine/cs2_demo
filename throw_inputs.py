import math
import sys
from collections import Counter
from pathlib import Path

from grenade_data import KINDS


TICK_FIELDS = [
    "X", "Y", "Z", "pitch", "yaw", "active_weapon_name", "buttons", "usercmd_buttonstate_1",
    "is_walking", "duck_amount", "is_airborne", "m_flThrowStrength", "m_bPinPulled", "m_bJumpThrow",
]
ATTACK = 1 << 0
JUMP = 1 << 1
DUCK = 1 << 2
ATTACK2 = 1 << 11
WALK = 1 << 16
DIRECTION_KEYS = {"W": 1 << 3, "Q": 1 << 9, "S": 1 << 4, "D": 1 << 10}
STRENGTH_MODES = {0.0: "right", 0.5: "middle", 1.0: "left"}
ATTACK_MODES = {ATTACK: "left", ATTACK2: "right", ATTACK | ATTACK2: "middle"}


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def button_mask(sample):
    for field in ("usercmd_buttonstate_1", "buttons"):
        value = sample.get(field)
        if finite(value):
            return int(value)
    return None


def throw_controls(samples, kind, launch_tick, tick_rate):
    if not samples or launch_tick - samples[-1]["tick"] > 2:
        return None

    # The pin release precedes the projectile spawn. Only inspect the last use of this grenade.
    grenade = []
    for sample in reversed(samples):
        if KINDS.get(sample.get("active_weapon_name")) == kind:
            grenade.append(sample)
        elif grenade:
            break
    release = next((sample for sample in grenade if sample.get("m_bPinPulled") is True), None)
    controls = {}
    fired = grenade[0] if grenade else samples[-1]
    strength = fired.get("m_flThrowStrength")
    if not finite(strength) and release:
        strength = release.get("m_flThrowStrength")
    if finite(strength):
        mode = min(STRENGTH_MODES, key=lambda value: abs(value - strength))
        if abs(mode - strength) <= 0.1:
            controls["mouse"] = STRENGTH_MODES[mode]
    if "mouse" not in controls and release:
        mask = button_mask(release) or 0
        mouse = ATTACK_MODES.get(mask & (ATTACK | ATTACK2))
        if mouse:
            controls["mouse"] = mouse

    release_tick = release["tick"] if release else fired["tick"]
    movement = [sample for sample in samples if sample["tick"] >= release_tick - max(1, tick_rate // 12)]
    masks = [mask for sample in movement if (mask := button_mask(sample)) is not None]
    if masks:
        # A key can be held at release or just after it, before the projectile appears.
        directions = Counter(key for mask in masks for key, bit in DIRECTION_KEYS.items() if mask & bit)
        controls["directions"] = [key for key in DIRECTION_KEYS if directions[key] >= min(2, len(masks))]
    if any(mask & WALK for mask in masks) or any(sample.get("is_walking") is True for sample in movement):
        controls["walk"] = True
    if any(mask & DUCK for mask in masks) or any(
        finite(amount := sample.get("duck_amount")) and amount >= 0.5 for sample in movement
    ):
        controls["crouch"] = True

    aim = samples[-1]
    position = aim
    jumped = any(sample.get("m_bJumpThrow") is True for sample in grenade)
    if aim.get("is_airborne") is True:
        grounded = next((sample for sample in reversed(samples) if sample.get("is_airborne") is False), None)
        if grounded:
            position = grounded
            jumped |= any((button_mask(sample) or 0) & JUMP for sample in samples
                          if sample["tick"] >= grounded["tick"] - 2)
    if jumped:
        controls["jump"] = True

    xyz = [position.get(axis) for axis in ("X", "Y", "Z")]
    angles = [aim.get(axis) for axis in ("pitch", "yaw")]
    result = {"controls": controls} if controls else {}
    if all(finite(value) for value in [*xyz, *angles]):
        result.update(position=[round(value, 6) for value in xyz], angles=[round(value, 6) for value in angles])
    return result or None


def add_throw_inputs(demo, clips, tick_rate):
    # Prefer the normal Python environment and also support a project-local installation on Windows.
    local_parser = Path(__file__).resolve().parent / ".local" / "throw-parser"
    if local_parser.is_dir() and str(local_parser) not in sys.path:
        sys.path.append(str(local_parser))
    from demoparser2 import DemoParser

    lookback = max(1, round(tick_rate * 0.55))
    players = sorted({int(clip["steamId"]) for clip in clips})
    ticks = sorted({tick for clip in clips for tick in range(max(0, clip["launchTick"] - lookback), clip["launchTick"] + 1)})
    parsed = DemoParser(str(demo)).parse_ticks(TICK_FIELDS, players=players, ticks=ticks)
    samples = {(sample["steamid"], sample["tick"]): sample for sample in parsed.to_dict("records")}
    for clip in clips:
        player = int(clip["steamId"])
        launch = clip["launchTick"]
        recent = [samples[player, tick] for tick in range(max(0, launch - lookback), launch + 1)
                  if (player, tick) in samples]
        metadata = throw_controls(recent, clip["kind"], launch, tick_rate)
        if metadata:
            clip["lineup"] = metadata
