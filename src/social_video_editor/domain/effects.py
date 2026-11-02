"""Shot effect catalog (camera moves applied to a video shot).

Effects you can put in a shot's "effects" list:
  {"type": "zoom_punch", "t": 0.0, "scale": 1.12}   sudden zoom-in (emphasis, "reveal")
  {"type": "push_in", "amount": 0.06}               slow zoom-in during the whole shot
  {"type": "pull_out", "amount": 0.06}              slow zoom-out
  {"type": "shake", "t": 0.0, "dur": 0.3}           short shake (impact)
  {"type": "flash", "t": 0.0}                       brief white flash
  {"type": "freeze", "seconds": 0.6}                freezes the last frame (a shot can also say "freeze": s)
  {"type": "beat_pulse", "amount": 0.04}            small zoom on every music beat inside the shot
The FFmpeg expressions that draw them are built in adapters/ffmpeg/filters.py.
"""
from __future__ import annotations

EFFECT_TYPES = ("zoom_punch", "push_in", "pull_out", "shake", "flash", "freeze", "beat_pulse")


def effects_of(shot: dict, kind: str) -> list[dict]:
    return [e for e in shot.get("effects", []) if e.get("type") == kind]


def freeze_seconds(shot: dict) -> float:
    """Extra seconds the shot lasts because its last frame is frozen."""
    s = float(shot.get("freeze", 0) or 0)
    for e in effects_of(shot, "freeze"):
        s += float(e.get("seconds", 0.6))
    return s
