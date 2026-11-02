"""Picks the best moments of the footage and builds a first edit plan.

Every instant gets a score (motion, loudness, sound peaks, voice, keywords, sharpness, color).
The best-scoring windows are taken without cutting phrases or crossing scene changes, until
the target duration is filled. The resulting plan (plan.json) is meant to be edited.

Analysis dict of a source (English keys):
  {"id", "duration",
   "samples": [{"t", "brightness", "sharpness", "motion", "color"}, …],
   "scenes": [[start, end], …],
   "audio": {"step", "db": […], "peaks": […], "median_db"} | None,
   "words": [{"word", "start", "end", "prob"}, …],
   "voice": {"segments": [[a, b], …], "ratio"}}
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

import numpy as np


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[^a-zñ0-9 ]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def _audio_index(t: np.ndarray, audio: dict) -> np.ndarray:
    """Index of the audio level for each sample time (audio levels are every `step` seconds)."""
    return np.clip((t / audio["step"]).astype(int), 0, len(audio["db"]) - 1)


def _score_series(an: dict, cfg: dict, refs: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """Score per sample (4/s) for one source."""
    sel = cfg["selection"]
    w = sel["weights"]
    m = an["samples"]
    t = np.array([x["t"] for x in m])
    mot = np.clip(np.array([x["motion"] for x in m]) / refs["motion"], 0, 1.5)
    mot_s = np.where(mot > 1.0, 1.0 - (mot - 1.0), mot)               # too much motion = blurry pan
    sharp = np.log1p([x["sharpness"] for x in m])
    sharp_n = np.clip((sharp - refs["sharp_lo"]) / max(refs["sharp_hi"] - refs["sharp_lo"], 1e-3), 0, 1)
    col = np.clip(np.array([x["color"] for x in m]) / refs["color"], 0, 1.2)
    light = np.array([x["brightness"] for x in m])

    aud = np.zeros_like(t)
    peaks = np.zeros_like(t)
    if an.get("audio"):
        db = np.array(an["audio"]["db"])
        aud = np.clip((db[_audio_index(t, an["audio"])] - an["audio"]["median_db"]) / 18, 0, 1)
        for p in an["audio"]["peaks"]:
            peaks[(t >= p - 0.25) & (t <= p + 0.6)] = 1
    voice = np.zeros_like(t)
    for a, b in an["voice"]["segments"]:
        voice[(t >= a) & (t <= b)] = 1
    kw = np.zeros_like(t)
    keys = {_norm_text(k) for k in sel.get("keywords", [])}
    hits = []
    for p in an.get("words", []):
        if _norm_text(p["word"]) in keys:
            kw[(t >= p["start"] - 0.6) & (t <= p["end"] + 0.6)] = 1
            hits.append(p["word"])

    s = (w["motion"] * mot_s + w["audio"] * aud + w["peaks"] * peaks + w["voice"] * voice
         + w["keywords"] * kw + w["sharpness"] * sharp_n + w["color"] * col)
    bad = (light < sel["min_brightness"]) | (sharp < refs["sharp_blurry"])
    s = np.where(bad, s - 5, s)
    return t, s, {"peaks": peaks, "voice": voice, "keyword_hits": hits}


def _references(analyses: list[dict], cfg: dict) -> dict:
    allm = [x for a in analyses for x in a["samples"]]
    sharp = np.log1p([x["sharpness"] for x in allm])
    return {
        "motion": float(np.percentile([x["motion"] for x in allm], 90) or 1),
        "sharp_lo": float(np.percentile(sharp, 10)), "sharp_hi": float(np.percentile(sharp, 90)),
        "sharp_blurry": float(np.percentile(sharp, cfg["selection"]["blur_percentile"])),
        "color": float(np.percentile([x["color"] for x in allm], 90) or 1),
    }


def _scene_cuts(an: dict) -> list[float]:
    return sorted(a for a, _ in an.get("scenes", [])[1:])


def _fit_to_phrase(start: float, end: float, an: dict, phrase_max: float) -> tuple[float, float]:
    """If the window cuts a phrase, extends it so no word is cut."""
    for a, b in an["voice"]["segments"]:
        if a < end and b > start:                       # overlaps a voice segment
            ns, ne = min(start, a - 0.08), max(end, b + 0.12)
            if ne - ns <= phrase_max:
                start, end = ns, ne
            else:                                       # phrase too long: cut in a pause between words
                words = [p for p in an["words"] if start - 0.5 <= p["start"] <= end + 0.5]
                if words:
                    start = min(start, words[0]["start"] - 0.08) if words[0]["start"] < start else start
                    ends = [p["end"] for p in words if p["end"] <= start + phrase_max]
                    end = max(ends) + 0.1 if ends else end
    return max(start, 0), min(end, an["duration"])


def voice_segments(words: list[dict], gap: float = 0.7) -> list[list[float]]:
    """Speech segments: consecutive words separated by less than `gap` seconds."""
    segs: list[list[float]] = []
    for w in words:
        if segs and w["start"] - segs[-1][1] <= gap:
            segs[-1][1] = w["end"]
        else:
            segs.append([w["start"], w["end"]])
    return segs


def has_voice(an: dict, start: float, end: float) -> bool:
    """True if more than 30 % of [start, end] is speech."""
    spoken = sum(max(0, min(b, end) - max(a, start)) for a, b in an["voice"]["segments"])
    return spoken / max(end - start, 1e-3) > 0.3


def propose(analyses: list[dict], cfg: dict) -> dict:
    """Candidate windows of every source and the chosen ones (story order)."""
    r, d = cfg["pacing"], cfg["duration"]
    refs = _references(analyses, cfg)
    candidates = []
    for si, an in enumerate(analyses):
        t, s, extra = _score_series(an, cfg, refs)
        if len(t) < 4:
            continue
        step = t[1] - t[0]
        length = max(int(round(r["shot_max"] / step)), 2)
        smooth = np.convolve(s, np.ones(length) / length, mode="same")
        cuts = _scene_cuts(an)
        used = np.zeros(len(t), bool)
        for i in np.argsort(-smooth):
            if used[i] or smooth[i] < 0:
                continue
            start, end = t[i] - r["shot_max"] / 2, t[i] + r["shot_max"] / 2
            for c in cuts:                              # never cross a scene change
                if start < c < t[i]:
                    start = c + 0.05
                if t[i] < c < end:
                    end = c - 0.05
            start, end = _fit_to_phrase(start, end, an, r["phrase_max"])
            if end - start < r["shot_min"]:
                continue
            m = (t >= start - r["min_gap"]) & (t <= end + r["min_gap"])
            if used[m].any():
                continue
            used[m] = True
            seg = (t >= start) & (t <= end)
            candidates.append({
                "source": si, "start": round(float(start), 2), "end": round(float(end), 2),
                "score": round(float(s[seg].mean()), 3),
                "peak": bool(extra["peaks"][seg].any()), "voice": bool(extra["voice"][seg].mean() > 0.3),
            })
            if len(candidates) > 400:
                break

    # Choose: first the best of each source (if asked), then the best overall
    candidates.sort(key=lambda c: -c["score"])
    target = d["target"] - (cfg["hook"]["duration"] if cfg["hook"]["strategy"] == "teaser" else 0)
    chosen, total = [], 0.0

    def dur(c):
        v = 1.0 if c["voice"] else r["speed_without_voice"]
        return (c["end"] - c["start"]) / v

    if cfg["selection"].get("use_all_sources", True):
        for si in range(len(analyses)):
            best = next((c for c in candidates if c["source"] == si and c["score"] > 0), None)
            if best:
                chosen.append(best)
                total += dur(best)
    n_parts = int(cfg["selection"].get("coverage", 0) or 0)
    if n_parts:   # coverage: the best moment of each part of the footage (not all from the same minute)
        full_length = sum(a["duration"] for a in analyses)
        bounds, acc = [], 0.0
        for si, a in enumerate(analyses):
            bounds.append((si, acc, acc + a["duration"]))
            acc += a["duration"]

        def pos(c):
            return next(b0 for si, b0, _ in bounds if si == c["source"]) + c["start"]

        for k in range(n_parts):
            a0, a1 = full_length * k / n_parts, full_length * (k + 1) / n_parts
            best = next((c for c in candidates if a0 <= pos(c) < a1 and c not in chosen
                         and c["score"] > 0), None)
            if best and total < target:
                chosen.append(best)
                total += dur(best)
    for c in candidates:
        if total >= target:
            break
        if c in chosen or c["score"] <= 0:
            continue
        if total + dur(c) > d["max"]:
            continue
        chosen.append(c)
        total += dur(c)
    chosen.sort(key=lambda c: (c["source"], c["start"]))      # story order
    return {"shots": chosen, "candidates": candidates[:80], "candidates_all": candidates, "total": total}


def build_plan(analyses: list[dict], cfg: dict, report: Callable[[str], None] | None = None) -> dict:
    """Complete edit plan: shots + hook + transitions + effects."""
    prop = propose(analyses, cfg)
    if report:
        report(f"Moments: {len(prop['candidates_all'])} candidates → {len(prop['shots'])} shots "
               f"(~{prop['total']:.1f} s)")
    shots = []
    for c in prop["shots"]:
        an = analyses[c["source"]]
        said = " ".join(p["word"] for p in an.get("words", []) if c["start"] <= p["start"] < c["end"])
        shots.append({
            "source": c["source"], "start": c["start"], "end": c["end"],
            "speed": 1.0 if c["voice"] else cfg["pacing"]["speed_without_voice"],
            "role": "story", "transition": "cut", "effects": [],
            "score": c["score"], "note": said[:120],
        })
    if cfg["effects"]["zoom_on_peaks"]:   # zoom only on part of the shots with a peak (the best ones)
        with_peak = sorted((k for k, c in enumerate(prop["shots"]) if c["peak"]),
                           key=lambda k: -prop["shots"][k]["score"])
        quota = int(round(len(shots) * float(cfg["effects"].get("zoom_ratio", 0.3))))
        for k in with_peak[:quota]:
            shots[k]["effects"].append({"type": "zoom_punch"})

    h = cfg["hook"]
    if shots and h["strategy"] == "teaser":
        best = max(shots, key=lambda x: x["score"])
        best["role"] = "reveal"
        center = (best["start"] + best["end"]) / 2
        hook = {**best, "start": round(max(center - h["duration"] / 2, best["start"]), 2),
                "role": "hook", "speed": 1.0, "effects": [{"type": "zoom_punch"}]}
        hook["end"] = round(min(hook["start"] + h["duration"], best["end"]), 2)
        shots.insert(0, hook)

    rot = cfg["transitions"]["rotation"] or ["cut"]
    for i, s in enumerate(shots):
        if i == 0:
            s["transition"] = "cut"
        elif i == 1 and h["strategy"] == "teaser":
            s["transition"] = h.get("transition", "flash")
        else:
            s["transition"] = rot[(i - 1) % len(rot)]
        if s["role"] == "reveal" and cfg["effects"].get("flash_on_reveal"):
            s["transition"] = "flash"

    texts = []
    if h.get("text"):
        texts.append({"text": h["text"], "start": 0.0, "end": max(h["duration"] + 0.8, 2.2), "style": "hook"})
    return {
        "note": "Editable plan. Shot times = seconds inside each source; text times = seconds of the "
                "final video. Render again with: sve render <project>",
        "shots": shots,
        "texts": texts,
        "extra_candidates": [c for c in prop["candidates"] if c not in prop["shots"]][:30],
    }


def best_start(t: np.ndarray, s: np.ndarray, a: float, b: float, dur: float) -> float:
    """Start of the steadiest high-scoring window of `dur` seconds inside [a, b].

    Each window is valued as mean(score) − 0.5·std(score) over the samples it contains.
    """
    best, best_start_ = -1e9, a
    for t0 in t:
        if t0 < a:
            continue
        if t0 + dur > b:
            break
        sel = (t >= t0) & (t <= t0 + dur)
        v = s[sel].mean() - 0.5 * s[sel].std()
        if v > best:
            best, best_start_ = v, t0
    return float(best_start_)


def best_window(an: dict, rng: list[float], dur: float, criterion: str = "product",
                visual: dict | None = None) -> tuple[float, float]:
    """Inside [a, b] of a source, the most suitable window of `dur` seconds.

    product = sharp, colorful and still image (showing an object)
    action  = motion and sound (opening, tearing, unpacking)
    visual  = optional {"t": [...], "score": [...]}: a score per instant from a vision model
              (e.g. CLIP product score or similarity to a text); when given it replaces the
              heuristic score (sharpness is still added, so blurry frames lose).
    """
    a, b = max(0.0, rng[0]), min(an["duration"], rng[1])
    if b - a <= dur:
        return round(a, 2), round(b, 2)
    if visual is not None and len(visual.get("t", [])) >= 3:
        vt, vs = np.asarray(visual["t"], float), np.asarray(visual["score"], float)
        inside = (vt >= a) & (vt <= b)
        if inside.sum() >= 3:
            t, s = vt[inside], vs[inside]
            s = (s - s.mean()) / max(s.std(), 1e-6)
            m = an.get("samples", [])
            if len(m) >= 2:
                sharp = np.interp(t, [x["t"] for x in m], np.log1p([x["sharpness"] for x in m]))
                s = s + 0.5 * (sharp - sharp.mean()) / max(sharp.std(), 1e-6)
            start = best_start(t, s, a, b, dur)
            return round(start, 2), round(start + dur, 2)
    m = [x for x in an["samples"] if a <= x["t"] <= b]
    if len(m) < 3:
        return round(a, 2), round(a + dur, 2)
    t = np.array([x["t"] for x in m])
    sharp = np.log1p([x["sharpness"] for x in m])
    sharp = (sharp - sharp.min()) / max(np.ptp(sharp), 1e-6)
    col = np.array([x["color"] for x in m])
    col = (col - col.min()) / max(np.ptp(col), 1e-6)
    mot = np.array([x["motion"] for x in m])
    mot = (mot - mot.min()) / max(np.ptp(mot), 1e-6)
    if criterion == "action":
        s = mot + 0.5 * sharp
        if an.get("audio"):
            db = np.array(an["audio"]["db"])
            s = s + np.clip((db[_audio_index(t, an["audio"])] - an["audio"]["median_db"]) / 18, 0, 1)
    else:
        s = 1.2 * sharp + 0.8 * col - 1.0 * mot
    start = best_start(t, s, a, b, dur)
    return round(start, 2), round(start + dur, 2)


def search_range(search: dict, an: dict) -> list[float]:
    """The [a, b] range of a "search" (key "range", or "in"; the whole source when missing)."""
    rng = search.get("range", search.get("in"))
    return [float(rng[0]), float(rng[1])] if rng else [0.0, float(an["duration"])]


def resolve_ranges(plan: dict, analyses: list[dict],
                   visual: Callable[[int, dict], dict | None] | None = None) -> dict:
    """Fills start/end of the shots that ask to "search" (recomputed on every render).

    search = {"range": [a, b], "dur": 1.8, "criterion": "product" | "action"}
           | {"text": "phone case", "range": [a, b]}       (best match of a description; needs vision)
    `visual(source, search)` may return a vision score series for that search (see best_window).
    """
    for s in plan["shots"]:
        b = s.get("search")
        if b:
            an = analyses[s["source"]]
            criterion = b.get("criterion", "product")
            series = visual(s["source"], b) if visual and (b.get("text") or criterion == "product") else None
            s["start"], s["end"] = best_window(an, search_range(b, an), float(b.get("dur", 1.8)), criterion,
                                               series)
    return plan
