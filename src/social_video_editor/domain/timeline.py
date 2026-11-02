"""Where every shot lands in the final video, beat alignment and absolute text times.

Plan text format (times of the final video unless anchored to a shot):
  {"text": "…", "start": 0, "end": 2, "style": "hook"}
  {"text": "…", "shot": 3, "start": 0.1, "end": 1.5, "style": "label"}   # relative to shot 3
  {"text": "…", "description": "…", "shot": 3, "until_shot": 4, "style": "card"}  # spans shots 3 and 4
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from .pieces import Sequence, Shot
from .timebase import frames
from .transitions import entry_overlap, transition_duration


@dataclass
class Clip:
    """A shot placed on the output timeline."""

    index: int
    piece: Shot
    dur: float          # seconds on screen (whole frames)
    overlap: float      # seconds shared with the previous shot (entry transition)
    out_start: float
    out_end: float

    @property
    def data(self) -> dict:
        return self.piece.data

    def as_dict(self) -> dict:
        """The shot's data plus its placement (what the render filters read)."""
        return {**self.piece.data, "index": self.index, "dur": self.dur, "overlap": self.overlap,
                "out_start": self.out_start, "out_end": self.out_end}


class Timeline:
    def __init__(self, sequence: Sequence, clips: list[Clip]):
        self.sequence, self.clips = sequence, clips

    def __iter__(self) -> Iterator[Clip]:
        return iter(self.clips)

    def __len__(self) -> int:
        return len(self.clips)

    def __getitem__(self, i) -> Clip:
        return self.clips[i]

    @property
    def total(self) -> float:
        return self.clips[-1].out_end if self.clips else 0.0

    def cut(self, i: int) -> float:
        """Moment of the cut at the end of clip i (middle of the next entry transition)."""
        nxt = self.clips[i + 1].overlap / 2 if i + 1 < len(self.clips) else 0.0
        return self.clips[i].out_end - nxt


def _sequence(plan_or_sequence) -> Sequence:
    return plan_or_sequence if isinstance(plan_or_sequence, Sequence) else Sequence.from_plan(plan_or_sequence)


def compute_timeline(plan_or_sequence, cfg: dict) -> Timeline:
    """Places every shot: duration in whole frames, overlap of its entry transition."""
    seq = _sequence(plan_or_sequence)
    fps = cfg["output"]["fps"]
    clips: list[Clip] = []
    t = 0.0
    for i, piece in enumerate(seq.shots()):
        dur = piece.duration(fps)
        d = entry_overlap(piece.transition, dur, clips[-1].dur if clips else None, cfg, fps)
        start = t - d
        clips.append(Clip(i, piece, dur, d, round(start, 4), round(start + dur, 4)))
        t = start + dur
    return Timeline(seq, clips)


def absolute_texts(plan: dict, tl: Timeline) -> list[dict]:
    """Converts shot-anchored texts to final-video times (the plan is not modified)."""
    res = []
    total = tl.total
    for tx in plan.get("texts", []):
        tx = dict(tx)
        if "shot" in tx:
            k = int(tx["shot"])
            base = tl[k]
            end_rel = tx.get("end", base.dur)
            # appears when the entry transition is over and leaves before the next transition
            start = max(base.out_start + tx.get("start", 0), base.out_start + base.overlap)
            end = base.out_start + end_rel
            k_end = int(tx.get("until_shot", k))          # a text can span several consecutive shots
            if k_end != k:
                end = tl[k_end].out_end
            if k_end + 1 < len(tl):
                end = min(end, tl[k_end + 1].out_start)
            tx["start"], tx["end"] = start, end
        tx["end"] = min(tx["end"], total)
        if tx["end"] > tx["start"]:
            res.append(tx)
    return res


def snap_cuts_to_beats(plan: dict, beats: list[float], cfg: dict, source_durations: list[float],
                       window: float = 0.3) -> int:
    """Moves every cut to the nearest music beat by lengthening/shortening the previous shot."""
    if not beats:
        return 0
    b = sorted(beats)
    if cfg["music"].get("half_beats", True) and len(b) > 1:   # also on the off-beat (between beats)
        b = sorted(b + [(x + y) / 2 for x, y in zip(b, b[1:])])
    b = np.array(b)
    moved = 0
    shots = Sequence.from_plan(plan).shots()
    for i in range(1, len(shots)):
        tl = compute_timeline(plan, cfg)
        cut = tl[i].out_start + tl[i].overlap / 2
        j = int(np.argmin(np.abs(b - cut)))
        delta = float(b[j] - cut)
        if abs(delta) < 1 / 60 or abs(delta) > window:
            continue
        if shots[i - 1].shift_end(delta, cfg, source_durations):
            moved += 1
    return moved


def align_to_beats(plan: dict, beats: list[float], cfg: dict, source_durations: list[float]) -> int:
    """Makes every cut fall EXACTLY on a music beat.

    "continuous": true → the shot starts where the previous one ended (fast→slow ramps).
    Each shot lasts "beats": N beats (if not given, the number of beats closest to its natural length).
    The middle of every transition sits on the beat. Video shots grow/shrink from their start
    (or slide back if the source is too short); drawn shots change their "dur".
    Each shot stores "_beats" (beats inside the shot, seconds relative to its start) for beat_pulse.
    """
    if len(beats) < 2:
        return 0
    g = np.array(sorted(beats))
    T = float(np.median(np.diff(g)))
    fps = cfg["output"]["fps"]
    shots = Sequence.from_plan(plan).shots()
    k = int(np.searchsorted(g, -1e-6))      # beat where the video starts (first one >= 0)
    cuts = []
    for s in shots:
        if "beats" in s.data:
            n = int(s.data["beats"])
        else:
            n = max(1, int(round(s.base_duration() / T)))
        k += n
        cuts.append(float(g[k]) if k < len(g) else float(g[-1] + (k - len(g) + 1) * T))
    overlaps = [0.0] + [frames(transition_duration(s.transition, cfg), fps) for s in shots[1:]]
    prev_end = 0.0
    for i, s in enumerate(shots):
        out_start = prev_end - overlaps[i]
        end_out = cuts[i] + (overlaps[i + 1] / 2 if i + 1 < len(shots) else 0)
        dur = frames(end_out - out_start, fps)
        s.fit_to_duration(dur, shots[i - 1] if i else None, source_durations)
        s.data["_beats"] = [round(float(b - out_start), 3) for b in g
                            if out_start + 0.05 < b < out_start + dur - 0.05]
        prev_end = out_start + dur
    return len(shots)
