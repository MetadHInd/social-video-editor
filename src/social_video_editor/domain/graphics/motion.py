"""Timing math for animated graphics (pure functions of time; no drawing).

The "printed" look animates objects on twos (15 steps per second), lets strokes wobble a little a
few times per second ("boil") and makes impacts shake the color registration instead of blurring.
"""
from __future__ import annotations

import math

ON_TWOS_FPS = 15
LOOP_TICKS = 32               # ambient motion repeats every 32 steps (≈2.1 s): a settled graphic is a loop
BOIL_TICKS = 2                # the stroke wobble changes every 2 steps (7.5 times a second)…
BOIL_PHASES = 4               # …and has 4 states, so it repeats inside the ambient loop
PRINT_SECONDS = 0.27          # a text "prints": its two color ghosts meet in this time
SHAKE_DECAY = 9.0


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def progress(t: float, start: float, dur: float) -> float:
    """0 before `start`, 1 after `start + dur`."""
    return 1.0 if dur <= 0 and t >= start else clamp01((t - start) / dur) if dur > 0 else 0.0


def ease_out(p: float) -> float:
    return 1 - (1 - clamp01(p)) ** 3


def ease_in_out(p: float) -> float:
    p = clamp01(p)
    return p * p * (3 - 2 * p)


def action(p: float, anticipation: float = 0.18, back: float = 0.07, overshoot: float = 1.5) -> float:
    """Action curve: pulls back `back` during the first `anticipation` of the move, then goes past
    the target and settles on it. 0 → 1 (briefly below 0 and above 1)."""
    p = clamp01(p)
    if p <= 0 or p >= 1:
        return p
    if p < anticipation:
        return -back * math.sin(math.pi / 2 * p / anticipation)
    q = (p - anticipation) / (1 - anticipation)
    out = 1 + (overshoot + 1) * (q - 1) ** 3 + overshoot * (q - 1) ** 2      # ease-out-back
    return -back + (1 + back) * out


def stepped(t: float, fps: int = ON_TWOS_FPS) -> float:
    """Time held on a grid of `fps` steps per second (animating "on twos" at 15)."""
    return math.floor(t * fps + 1e-6) / fps


def _noise(n: float) -> float:
    """Deterministic pseudo-random number in -1…1."""
    x = math.sin(n * 12.9898 + 78.233) * 43758.5453
    return (x - math.floor(x)) * 2 - 1


def ticks(t: float) -> int:
    """Whole on-twos steps elapsed at `t`."""
    return math.floor(t * ON_TWOS_FPS + 1e-6)


def loop(t: float, offset: float = 0.0) -> float:
    """Phase 0…1 of the ambient loop at `t` (plus an offset, so things are out of step with each other).
    Anything periodic in this phase repeats seamlessly."""
    return ((ticks(t) % LOOP_TICKS) / LOOP_TICKS + offset) % 1.0


def wave(t: float, offset: float = 0.0, cycles: int = 1) -> float:
    """A smooth 0…1…0 swing, `cycles` times per ambient loop."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * cycles * loop(t, offset))


def noise(n: float) -> float:
    """Deterministic pseudo-random number in 0…1 for an index (decor placement)."""
    return (_noise(n) + 1) / 2


def boil(seed: int, t: float, px: float = 1.2, deg: float = 0.22) -> tuple[float, float, float]:
    """(dx, dy, degrees) of the stroke wobble of shape `seed` at time t."""
    k = (ticks(t) // BOIL_TICKS) % BOIL_PHASES
    base = seed * 7.13 + k * 31.7
    return _noise(base) * px, _noise(base + 1.3) * px, _noise(base + 2.9) * deg


def shake(since: float, energy: float = 1.0) -> float:
    """Energy left of an impact `since` seconds ago (multiplies the registration offset)."""
    return 0.0 if since < 0 else energy * math.exp(-SHAKE_DECAY * since)


def stamp_scale(p: float, start: float = 0.9) -> float:
    """Scale of something stamped on the page: `start` → 1 along the action curve."""
    return start + (1 - start) * action(p)


def lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p
