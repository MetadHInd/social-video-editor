"""Shorten long pauses in speech: in every shot with voice (any recipe), and in whole recordings
(the talk recipe: meetings, classes, screen recordings).

Only pauses of at least `min_pause` seconds are touched; each one is shortened to `keep_pause`
(half kept at the end of the span before it, half at the start of the span after it), so the
speech keeps its natural breathing rhythm. Shorter pauses are left alone.

`overlap` is the crossfade between two spans: each side of a cut gets overlap / 2 more, so the
pause heard after the crossfade is still `keep_pause`.
"""
from __future__ import annotations

import numpy as np

from .check import silences_from_db
from .transitions import transition_duration

DEFAULTS = {
    "enabled": True,
    "transition": "soft_cut",   # how the two sides of a shortened pause are joined
    "min_pause": 0.9,      # seconds: shorter pauses are never cut (breaths between sentences)
    "keep_pause": 0.45,    # seconds of pause left where a long one was cut
    "silence_db": "auto",  # quieter than this (dBFS) counts as a pause; auto = 12 dB under the source median
    "min_span": 0.5,       # a speech span shorter than this is merged across the pause (no tiny shots)
}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **{k: v for k, v in (cfg.get("pauses") or {}).items() if v is not None}}


def speech_spans(silences: list[tuple[float, float]], duration: float, min_pause: float = 0.9,
                 keep_pause: float = 0.45, overlap: float = 0.0,
                 min_span: float = 0.5) -> list[tuple[float, float]]:
    """[(start, end)] of the source to keep, in order, once every long pause is shortened.

    silences: [(start, end)] quiet stretches of the source. A leading / trailing silence keeps
    keep_pause / 2 (the video does not start or end abruptly)."""
    half = keep_pause / 2
    pad = half + overlap / 2
    spans: list[list[float]] = []
    t = 0.0
    for a, b in sorted(silences):
        a, b = max(a, 0.0), min(b, duration)
        if b - a < max(min_pause, keep_pause + overlap) or a < t:
            continue
        if a <= 0.0:                                   # leading silence
            t = max(b - half, 0.0)
            continue
        if b >= duration - 0.05:                       # trailing silence
            spans.append([t, min(a + half, duration)])
            t = duration
            break
        end, nxt = a + pad, b - pad
        if spans and end - t < min_span:               # a tiny span: keep the pause before it instead
            spans[-1][1] = end
        else:
            spans.append([t, end])
        t = nxt
    if t < duration:
        spans.append([t, duration])
    return [(round(a, 3), round(b, 3)) for a, b in spans if b - a > 1e-3]


def threshold_db(p: dict, analysis: dict) -> float:
    """Pause level: pauses.silence_db, or (auto) 12 dB under the source median, at most -25 dBFS."""
    v = p.get("silence_db", "auto")
    if v in (None, "auto"):
        med = float((analysis.get("audio") or {}).get("median_db", -30.0))
        return min(med - 12.0, -25.0)
    return float(v)


def source_pauses(analysis: dict, p: dict) -> list[tuple[float, float]]:
    """Quiet stretches of a source (from its cached audio levels) with no whole transcribed word inside."""
    audio = analysis.get("audio") or {}
    if not audio.get("db"):
        return []
    quiet = silences_from_db(np.asarray(audio["db"], float), float(audio.get("step", 0.1)),
                             threshold_db(p, analysis), float(p["min_pause"]) * 0.9)
    words = analysis.get("words") or []
    # Whisper often stretches the last word of a sentence into the pause after it: only a word lying
    # wholly inside the quiet stretch (a whisper) means it is not a pause
    return [(a, b) for a, b in quiet
            if not any(a + 0.1 <= w["start"] and w["end"] <= b - 0.1 for w in words)]


def _voiced(an: dict, start: float, end: float) -> bool:
    spoken = sum(max(0.0, min(b, end) - max(a, start)) for a, b in (an.get("voice") or {}).get("segments", []))
    return spoken / max(end - start, 1e-3) > 0.3


def shorten_plan_pauses(plan: dict, analyses: list[dict], cfg: dict, whole: bool = False) -> int:
    """Splits every shot with voice at its long pauses (in place); returns how many pauses were shortened.

    Skipped: drawn shots, beat-locked shots ("beats"), sped-up shots, the hook, shots without voice
    (unless `whole`: talk plans keep every source whole). New pieces enter with pauses.transition and
    carry "pause_cut": true (never moved to the beat). Shot indices in texts / polaroids / collages
    are renumbered."""
    p = settings(cfg)
    if not p.get("enabled", True):
        return 0
    tr = str(p["transition"])
    overlap = transition_duration(tr, cfg) if tr != "cut" else 0.0
    shots, first, last, out, cut = plan.get("shots", []), {}, {}, [], 0
    for i, s in enumerate(shots):
        first[i] = len(out)
        src, a0, b0 = s.get("source"), s.get("start"), s.get("end")
        an = analyses[src] if isinstance(src, int) and src < len(analyses) else None
        ok = (an is not None and s.get("type", "video") == "video" and "beats" not in s and a0 is not None
              and b0 is not None and float(s.get("speed", 1) or 1) == 1 and s.get("role") != "hook"
              and (whole or _voiced(an, float(a0), float(b0))))
        spans = []
        if ok:
            a0, b0 = float(a0), float(b0)
            quiet = [(max(a, a0) - a0, min(b, b0) - a0) for a, b in source_pauses(an, p) if b > a0 and a < b0]
            spans = [(a0 + a, a0 + b) for a, b in speech_spans(quiet, b0 - a0, float(p["min_pause"]),
                                                                 float(p["keep_pause"]), overlap,
                                                                 float(p["min_span"]))]
        if len(spans) <= 1:
            if spans and whole:
                s["start"], s["end"] = spans[0]
            out.append(s)
            last[i] = len(out) - 1
            continue
        effects = s.get("effects") or []
        offset = 0.0
        for k, (a, b) in enumerate(spans):
            if k == 0:
                piece = {**s, "start": a, "end": b, "effects": []}
            else:
                piece = {"source": src, "start": a, "end": b, "speed": 1, "role": s.get("role", "story"),
                         "transition": tr, "effects": [], "pause_cut": True}
            length = b - a
            for e in effects:                       # an effect goes to the piece it happens in
                t = float(e.get("t", 0) or 0)
                if offset <= t < offset + length or (k == len(spans) - 1 and t >= offset):
                    piece["effects"].append({**e, "t": round(t - offset, 3)} if "t" in e else e)
            offset += length - overlap             # where the next piece starts (it overlaps this one)
            out.append(piece)
        last[i] = len(out) - 1
        cut += len(spans) - 1
    if cut:
        plan["shots"] = out
        _renumber(plan, first, last)
    return cut


def _renumber(plan: dict, first: dict, last: dict) -> None:
    """Old shot index → new one after splitting: a split shot is referred to by its first piece,
    except where its LAST frame is used (polaroid from_shot, collage background, until_shot)."""
    def f(k):
        return first.get(k, k) if isinstance(k, int) else k

    def lst(k):
        return last.get(k, k) if isinstance(k, int) else k
    for s in plan.get("shots", []):
        if "from_shot" in s:
            s["from_shot"] = lst(s["from_shot"])
        if "from_shots" in s:
            s["from_shots"] = [f(k) for k in s["from_shots"]]
        if "background_from" in s:
            s["background_from"] = lst(s["background_from"])
    for t in plan.get("texts", []):
        if "shot" in t:
            t["shot"] = f(t["shot"])
        if "until_shot" in t:
            t["until_shot"] = lst(t["until_shot"])
