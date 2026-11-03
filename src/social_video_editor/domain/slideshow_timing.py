"""Photo durations fitted to the music (pure, deterministic).

`fit_photos_to_music` splits the length of a mix (see domain.automix.mix_timeline) into one slot
per photo so that:
  - the slots add up to the mix length (the video ends with the music);
  - every photo change lands on a beat, preferably on a bar boundary (downbeat);
  - every song boundary (middle of an AutoMix transition) is a photo change;
  - energetic / faster songs get shorter photos, calm ones longer, inside [min_dur, max_dur].

A slot is the time between two photo changes; a change is the MIDDLE of the visual transition, so
on screen a photo also shows during half of each neighbouring transition (`timeline.fit_to_cuts`
turns the changes into shot durations).
If even max_dur per photo cannot fill the mix, the slots cover only its beginning (ending on a
bar); if min_dur per photo would overflow it, the slots get shorter than min_dur.
With a video fade-out (output.fade_out) the last slot is at least `hold_last` long, so the last photo
is fully on screen (its entry transition over) before the fade to black starts.
"""
from __future__ import annotations

import math

import numpy as np

from .pieces import Sequence
from .timeline import fit_to_cuts
from .transitions import transition_duration

DOWNBEAT_BONUS = 0.15        # cost of a change on a plain beat instead of a bar boundary
OUT_OF_RANGE = 50.0          # cost per (relative) second outside [min_dur, max_dur]


def _targets(songs: list[dict], min_dur: float, max_dur: float) -> list[float]:
    """Preferred photo length per song: energy 0 → near max_dur, 1 → near min_dur (some slack kept)."""
    return [max_dur - (0.15 + 0.7 * float(np.clip(s.get("energy", 0.5), 0, 1))) * (max_dur - min_dur)
            for s in songs]


def _segments(tl: dict) -> list[tuple[float, float, int]]:
    """(start, end, song index) between consecutive boundaries."""
    L = float(tl["duration"])
    edges = [0.0] + [float(b) for b in tl.get("boundaries", []) if 0 < b < L] + [L]
    return [(edges[j], edges[j + 1], min(j, len(tl["songs"]) - 1)) for j in range(len(edges) - 1)]


def _quantized(beats: np.ndarray, a: float, b: float, min_dur: float, max_dur: float) -> tuple[float, float]:
    """Shortest / longest slot that is a whole number of beats inside [min_dur, max_dur] in (a, b)."""
    inside = beats[(beats >= a - 1e-6) & (beats <= b + 1e-6)]
    if len(inside) < 3:
        return min_dur, max_dur
    q = float(np.median(np.diff(inside)))
    lo, hi = math.ceil(min_dur / q - 1e-6) * q, math.floor(max_dur / q + 1e-6) * q
    return (lo, hi) if hi >= lo else (min_dur, max_dur)


def _allocate(lengths: list[float], targets: list[float], n: int, mins: list[float], maxs: list[float]) -> list[int]:
    """Photos per segment: closest to length/target, inside the feasible range, adding up to n."""
    lo = [max(1, math.ceil(L / mx - 1e-9)) for L, mx in zip(lengths, maxs)]
    hi = [max(1, math.floor(L / mn + 1e-9)) for L, mn in zip(lengths, mins)]
    for j in range(len(lengths)):
        if lo[j] > hi[j]:
            lo[j] = hi[j] = max(1, int(round(lengths[j] / targets[j])))
    if sum(lo) > n or sum(hi) < n:            # infeasible: ignore the bounds, keep the proportions
        lo = [1 if L > 0 else 0 for L in lengths]
        hi = [max(n, 1)] * len(lengths)
    k = [int(min(max(round(L / t), a), b)) for L, t, a, b in zip(lengths, targets, lo, hi)]
    while sum(k) > n:      # remove a photo where the mean slot gets closest to its target
        j = min((j for j in range(len(k)) if k[j] > lo[j]), key=lambda j: (lengths[j] / k[j] / targets[j], j))
        k[j] -= 1
    while sum(k) < n:      # add one where the slots are longest relative to the target
        j = max((j for j in range(len(k)) if k[j] < hi[j]), key=lambda j: (lengths[j] / k[j] / targets[j], -j))
        k[j] += 1
    return k


def _slot_cost(d: np.ndarray, target: float, min_dur: float, max_dur: float) -> np.ndarray:
    c = ((d - target) / target) ** 2
    c = c + OUT_OF_RANGE * (np.maximum(min_dur - d, 0) + np.maximum(d - max_dur, 0)) / target
    return c


def _cuts_in(a: float, b: float, k: int, beats: np.ndarray, down: np.ndarray, target: float,
             min_dur: float, max_dur: float, hold_last: float = 0.0) -> list[float]:
    """k−1 changes inside (a, b) on beats (dynamic programming); evenly spaced if there are no beats.
    hold_last > 0: no change in the last hold_last seconds before b."""
    if k <= 1:
        return []
    eps = 1e-6
    cand = beats[(beats > a + eps) & (beats < b - eps)]
    if hold_last > 0:
        cand = cand[cand <= b - hold_last + eps]
    if len(cand) < k - 1:
        if hold_last > 0 and (b - a) / k < hold_last and b - hold_last > a:
            return [a + (b - hold_last - a) * j / (k - 1) for j in range(1, k)]
        return [a + (b - a) * j / k for j in range(1, k)]
    node = np.where(np.isin(np.round(cand, 3), np.round(down, 3)), 0.0, DOWNBEAT_BONUS)
    m = len(cand)
    INF = np.inf
    # f[c] = best cost with the current change at cand[c]; back[j][c] = previous candidate (-1 = a)
    f = _slot_cost(cand - a, target, min_dur, max_dur) + node
    back = [np.full(m, -1)]
    lo_w, hi_w = 0.5 * min_dur, 2.0 * max_dur        # only plausible previous changes
    for _ in range(1, k - 1):
        g = np.full(m, INF)
        bk = np.full(m, -1)
        for c in range(m):
            p0 = int(np.searchsorted(cand, cand[c] - hi_w, "left"))
            p1 = int(np.searchsorted(cand, cand[c] - lo_w, "right"))
            p1 = min(p1, c)
            if p1 <= p0:
                continue
            prev = f[p0:p1] + _slot_cost(cand[c] - cand[p0:p1], target, min_dur, max_dur)
            j = int(np.argmin(prev))
            if np.isfinite(prev[j]):
                g[c], bk[c] = prev[j] + node[c], p0 + j
        f = g
        back.append(bk)
    total = f + _slot_cost(b - cand, target, min_dur, max_dur)
    c = int(np.argmin(total))
    if not np.isfinite(total[c]):
        return [a + (b - a) * j / k for j in range(1, k)]
    out = []
    for bk in reversed(back):
        out.append(float(cand[c]))
        c = int(bk[c])
    return sorted(out)


def _truncate(tl: dict, segs, targets, n: int, max_dur: float, min_dur: float) -> float:
    """Length the photos can fill when n × max_dur is shorter than the mix: ends on a bar (or a boundary)."""
    acc = 0.0
    for a, b, si in segs:
        t = targets[si]
        cap = (b - a) / t
        if acc + cap >= n:
            end = a + (n - acc) * t
            if end - a < min_dur:                       # less than one photo into this song: stop before it
                return a
            down = [x for x in tl.get("downbeats", []) if a + min_dur <= x <= b] or \
                   [x for x in tl.get("beats", []) if a + min_dur <= x <= b]
            return float(min(down, key=lambda x: abs(x - end))) if down else end
        acc += cap
    return float(tl["duration"])


def fit_photos_to_music(n_photos: int, mix_timeline: dict, min_dur: float = 3.5, max_dur: float = 5.5,
                        transition: float = 1.0, hold_last: float = 0.0) -> list[float]:
    """Slot length (seconds between photo changes) of each photo; they add up to the mix length.

    mix_timeline: {"duration", "songs": [{"energy", …}], "boundaries", "beats", "downbeats"}.
    transition: length of the visual transition (a slot is never shorter than 1.5 × it).
    hold_last: the last slot is at least this long (the video's fade-out + half a transition).
    """
    n = int(n_photos)
    if n <= 0:
        return []
    min_dur = max(float(min_dur), 1.5 * float(transition))
    max_dur = max(float(max_dur), min_dur)
    L = float(mix_timeline["duration"])
    if L <= 0:
        return [0.0] * n
    beats = np.array(sorted(float(b) for b in mix_timeline.get("beats", [])))
    down = np.array(sorted(float(b) for b in mix_timeline.get("downbeats", [])))
    songs = mix_timeline.get("songs") or [{"energy": 0.5}]
    targets = _targets(songs, min_dur, max_dur)
    segs = _segments(mix_timeline)
    if n * max_dur < L - 1e-6:                  # not enough photos: the video covers the start of the mix
        end = _truncate(mix_timeline, segs, targets, n, max_dur, min_dur)
        segs = [(a, min(b, end), si) for a, b, si in segs if a < end - 1e-6]
        L = end
    if len(segs) > n:                            # fewer photos than songs: drop the latest boundaries
        segs = segs[:n - 1] + [(segs[n - 1][0], L, segs[n - 1][2])]
    bounds = [_quantized(beats, a, b, min_dur, max_dur) for a, b, _ in segs]
    counts = _allocate([b - a for a, b, _ in segs], [targets[si] for *_, si in segs], n,
                       [q[0] for q in bounds], [q[1] for q in bounds])
    cuts = []
    for j, ((a, b, si), k) in enumerate(zip(segs, counts)):
        hold = float(hold_last) if j == len(segs) - 1 else 0.0
        cuts += _cuts_in(a, b, k, beats, down, targets[si], min_dur, max_dur, hold) + [b]
    edges = [0.0] + cuts
    out = [round(edges[j + 1] - edges[j], 4) for j in range(n)]
    out[-1] = round(L - sum(out[:-1]), 4)        # exact total despite rounding
    return out


def change_times(durations: list[float]) -> list[float]:
    """Absolute photo-change times (end of every slot)."""
    return [float(x) for x in np.cumsum(durations)]


def fit_plan_to_music(plan: dict, mix_timeline: dict, cfg: dict, source_durations: list[float]) -> dict | None:
    """With photo.fit_to_music, fits the photo shots of `plan` (in place) to the mix.

    Only plans made of photo shots are fitted. Returns None when not applicable, else
    {"photos", "length", "min", "max"} or {"skipped": reason}.
    """
    photo = cfg.get("photo") or {}
    shots = plan.get("shots") or []
    if not photo.get("fit_to_music") or not any(s.get("type") == "photo" for s in shots):
        return None
    if any(s.get("type", "video") != "photo" for s in shots):
        return {"skipped": "photo.fit_to_music only fits plans made of photo shots"}
    pieces = Sequence.from_plan(plan).shots()
    trans = max([transition_duration(p.transition, cfg) for p in pieces[1:]] or [0.0])
    fade_out = max(float((cfg.get("output") or {}).get("fade_out") or 0), 0.0)
    hold = fade_out + trans / 2 if fade_out > 0 else 0.0      # the last photo holds through the fade-out
    slots = fit_photos_to_music(len(pieces), mix_timeline, float(photo.get("min_duration", 3.5)),
                                float(photo.get("max_duration", 5.5)), trans, hold)
    fit_to_cuts(plan, change_times(slots), cfg, source_durations, mix_timeline.get("beats"))
    return {"photos": len(slots), "length": round(sum(slots), 3), "min": min(slots), "max": max(slots)}
