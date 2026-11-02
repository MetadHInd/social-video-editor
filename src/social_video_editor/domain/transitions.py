"""Transition catalog (how a shot ENTERS).

Each transition has a default duration, says whether that duration is fixed (ignores
`transitions.duration` from the recipe) and which sound effect accompanies it.
How it is drawn (FFmpeg xfade name) lives in the FFmpeg adapter.
To add a new one: add it here and in adapters/ffmpeg/filters.py:XFADE.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import UserError
from .timebase import DEFAULT_FPS, frames


@dataclass(frozen=True)
class Transition:
    name: str
    duration: float          # default duration in seconds (0 = hard cut)
    fixed: bool = False      # True = always its own duration
    sound: str | None = None  # sound effect played with it

    @property
    def is_cut(self) -> bool:
        return self.duration <= 0


TRANSITIONS: dict[str, Transition] = {t.name: t for t in (
    Transition("cut", 0.0),
    Transition("fade", 0.35, fixed=True),
    Transition("fade_black", 0.4, fixed=True),
    Transition("flash", 0.18, sound="flash"),
    Transition("zoom", 0.25, sound="whoosh"),
    Transition("whip", 0.22, sound="whoosh"),
    Transition("slide_up", 0.22, sound="whoosh"),
    Transition("slide_left", 0.22, sound="whoosh"),
    Transition("circle", 0.35, fixed=True, sound="whoosh"),
    Transition("pixel", 0.3, fixed=True),
    Transition("blur", 0.3, fixed=True),
    Transition("radial", 0.3, fixed=True, sound="whoosh"),
    Transition("curtain", 0.3, fixed=True, sound="whoosh"),
)}


def get_transition(name: str) -> Transition:
    if name not in TRANSITIONS:
        raise UserError(f"Unknown transition '{name}'. Options: {', '.join(TRANSITIONS)}")
    return TRANSITIONS[name]


def entry_overlap(name: str, dur: float, prev_dur: float | None, cfg: dict, fps: int = DEFAULT_FPS) -> float:
    """Seconds a shot entering with transition `name` overlaps the previous shot (whole frames).

    At most half of either shot; 0 for the first shot (prev_dur None) and for hard cuts.
    """
    if prev_dur is None:
        return 0.0
    return frames(min(transition_duration(name, cfg), dur / 2, prev_dur / 2), fps)


def transition_duration(name: str, cfg: dict) -> float:
    t = get_transition(name)
    if t.is_cut:
        return 0.0
    if t.fixed:
        return t.duration
    return float(cfg.get("transitions", {}).get("duration", t.duration))
