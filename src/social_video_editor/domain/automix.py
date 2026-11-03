"""AutoMix: one continuous, DJ-style mix of a playlist (pure logic, no audio I/O).

The FFmpeg/numpy adapter (adapters/ffmpeg/automix_render.py) measures every song (integrated
loudness, a level envelope, onset rate, low-band accents on the beats) and renders the plan made
here. Per song this module decides:

  - the in-point: the first strong downbeat (a long quiet intro is skipped; an energetic intro is
    kept). The first song keeps everything after its leading silence.
  - the out-point: the end of a musical phrase (a multiple of 8 bars from the first downbeat, 4/4)
    before the record's fade / outro gets quiet. The last song ends where its outro gets quiet
    (plus `end_fade` seconds, faded out).
  - the transition to the next song: when the tempos are within `tempo_tolerance` (also at half /
    double tempo) the outgoing tail is time-stretched to the incoming tempo over the overlap
    (8, 4, 2 or 1 bars, at most `max_overlap` seconds) and the incoming first downbeat lands on an
    outgoing downbeat, so the beats line up; otherwise a short (`short_overlap`) crossfade at a
    phrase end. Both use an equal-power crossfade with a "bass swap": the incoming song starts
    high-passed (cutoff opening from `crossover_hz` to full over the overlap) and the outgoing one
    loses its lows during the second half.
  - a gain bringing every song to the same integrated loudness (`target_lufs`).

`mix_timeline` describes the result for the picture: every song's span in the mix, the song
boundaries (middle of each overlap, on a beat), merged beats / downbeats, tempo and an energy score.

Song dict (built by `song`): {"name", "duration", "tempo", "interval", "beats", "phase",
"lufs", "onset_rate", "profile": {"start", "end", "loud_start", "outro_start", "body_db"}}.
"""
from __future__ import annotations

import math

import numpy as np

AUTOMIX_VERSION = 1
BEATS_PER_BAR = 4
SR = 48000

DEFAULTS = {
    "enabled": True,          # false: songs are joined one after another with a short plain crossfade
    "max_overlap": 12.0,      # seconds: longest beat-matched transition
    "short_overlap": 3.5,     # seconds: transition between songs whose tempos differ a lot
    "tempo_tolerance": 0.08,  # tempos within ±8 % (also at half / double tempo) are beat-matched
    "target_lufs": -16.0,     # every song is brought to this integrated loudness before mixing
    "crossover_hz": 250.0,    # bass swap: the incoming song starts high-passed here
    "gentle_hz": 150.0,       # … and here for a short (tempo-mismatched) transition
    "silence_db": -50.0,      # leading / trailing silence threshold (dBFS of a 0.1 s RMS window)
    "intro_drop_db": 6.0,     # an intro this far below the song's body level is "quiet" (skipped)
    "outro_drop_db": 9.0,     # an outro this far below the body level is the record's fade (cut)
    "end_fade": 3.0,          # the last song fades out over this many seconds where its outro starts
    "min_play": 20.0,         # every song plays at least this long outside the transitions (when possible)
    "phrase_bars": 8,         # out-points on multiples of this many bars
}


def settings(music_cfg: dict | None) -> dict:
    """music.automix merged over DEFAULTS (`automix: false` → enabled False)."""
    raw = (music_cfg or {}).get("automix")
    out = dict(DEFAULTS)
    if isinstance(raw, bool):
        out["enabled"] = raw
    elif isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if v is not None})
    for k in DEFAULTS:
        if k not in ("enabled",):
            out[k] = type(DEFAULTS[k])(out[k])
    out["enabled"] = bool(out["enabled"])
    return out


# ── per-song measurements ────────────────────────────────────────────────────

def level_profile(rms_db, hop: float, silence_db: float = -50.0, intro_drop_db: float = 6.0,
                  outro_drop_db: float = 9.0, smooth: float = 3.0) -> dict:
    """Where the audio starts / ends (silence trimmed), where it gets loud and where the outro gets quiet.

    rms_db: RMS level (dBFS) every `hop` seconds. Returns {"start", "end", "loud_start",
    "outro_start", "body_db"} in seconds (body_db: 75th percentile of the smoothed level).
    """
    db = np.asarray(rms_db, float)
    n = len(db)
    if n == 0:
        return {"start": 0.0, "end": 0.0, "loud_start": 0.0, "outro_start": 0.0, "body_db": -120.0}
    audible = np.flatnonzero(db > silence_db)
    if not len(audible):
        dur = n * hop
        return {"start": 0.0, "end": dur, "loud_start": 0.0, "outro_start": dur, "body_db": float(db.max())}
    i0, i1 = int(audible[0]), int(audible[-1]) + 1
    p = 10 ** (db / 10)

    def smoothed(sec: float) -> np.ndarray:
        kernel = np.ones(max(int(round(sec / hop)), 1))
        return 10 * np.log10(np.convolve(p, kernel, "same") / np.convolve(np.ones(n), kernel, "same") + 1e-12)

    sm = smoothed(smooth)
    body = float(np.percentile(sm[i0:i1], 75))
    loud = np.flatnonzero(smoothed(min(smooth, 1.0))[i0:i1] >= body - intro_drop_db)   # short window: no lead
    loud_start = (i0 + int(loud[0])) * hop if len(loud) else i0 * hop
    kept = np.flatnonzero(sm[i0:i1] >= body - outro_drop_db)
    outro = (i0 + int(kept[-1]) + 1) * hop if len(kept) else i1 * hop
    return {"start": round(i0 * hop, 3), "end": round(i1 * hop, 3), "loud_start": round(loud_start, 3),
            "outro_start": round(min(outro, i1 * hop), 3), "body_db": round(body, 2)}


def downbeat_phase(accents) -> int:
    """Which beat of every four is the downbeat: the phase (0-3) with the strongest mean accent
    (low-band onset strength on the beats). Ties → the earliest phase."""
    a = np.asarray(accents, float)
    if len(a) < BEATS_PER_BAR:
        return 0
    means = [float(a[p::BEATS_PER_BAR].mean()) for p in range(BEATS_PER_BAR)]
    best = max(means)
    return next(p for p, m in enumerate(means) if m >= best - 1e-9 * max(abs(best), 1))


def song(name: str, analysis: dict, features: dict, cfg: dict | None = None) -> dict:
    """A song as the planner needs it, from the beat analysis (domain.music) + the adapter features."""
    s = cfg or DEFAULTS
    beats = [float(b) for b in analysis.get("beats") or []]
    tempo = float(analysis.get("tempo") or 0)
    interval = float(analysis.get("interval") or (60.0 / tempo if tempo else 0.0))
    prof = level_profile(features["rms_db"], float(features["hop"]), s["silence_db"], s["intro_drop_db"],
                         s["outro_drop_db"])
    return {"name": name, "duration": float(features.get("duration") or analysis.get("duration") or prof["end"]),
            "tempo": tempo if len(beats) >= 2 else 0.0, "interval": interval, "beats": beats,
            "phase": downbeat_phase(features.get("accents") or []), "lufs": features.get("lufs"),
            "onset_rate": float(features.get("onset_rate") or 0.0), "profile": prof}


# ── beat grid of a song ──────────────────────────────────────────────────────

def _grid(s: dict, period_beats: int, lo: float, hi: float) -> list[float]:
    """Grid points every `period_beats` beats (anchored on the first downbeat) inside [lo, hi]."""
    if not s["tempo"] or not s["beats"]:
        return []
    T, b0 = s["interval"], s["beats"][0] + s["phase"] * s["interval"]
    step = period_beats * T
    k0 = math.ceil((lo - b0) / step - 1e-9)
    k1 = math.floor((hi - b0) / step + 1e-9)
    return [b0 + k * step for k in range(k0, k1 + 1)]


def downbeats(s: dict, lo: float, hi: float) -> list[float]:
    return _grid(s, BEATS_PER_BAR, lo, hi)


def phrase_ends(s: dict, lo: float, hi: float, bars: int = 8) -> list[float]:
    return _grid(s, BEATS_PER_BAR * bars, lo, hi)


def in_point(s: dict, first: bool = False) -> float:
    """Source second where the song enters the mix.

    First song: right after its leading silence. Otherwise the first strong downbeat: the downbeat
    at (or just before, up to half a bar) the moment the song gets loud — so an energetic intro is
    kept and a long quiet one is skipped.
    """
    p = s["profile"]
    if first:
        return p["start"]
    if not s["tempo"]:
        return p["loud_start"]
    bar = BEATS_PER_BAR * s["interval"]
    cands = downbeats(s, p["start"] - 1e-6, p["loud_start"] + bar / 2)
    if cands:
        return round(cands[-1], 4)
    after = downbeats(s, p["start"], p["start"] + 4 * bar)
    return round(after[0], 4) if after else p["loud_start"]


def tempo_match(bpm_out: float, bpm_in: float, tolerance: float) -> dict:
    """Can the two songs be beat-matched? Compares the incoming tempo ×1, ×2 and ×½ with the outgoing.

    → {"matched", "factor" (incoming multiple that matches), "error" (relative), "ratio"}
    ratio = time-stretch of the outgoing tail (output / input length) so its tempo becomes the
    incoming one (× factor); 1.0 when not matched.
    """
    if not bpm_out or not bpm_in:
        return {"matched": False, "factor": 1.0, "error": None, "ratio": 1.0}
    best = min(((abs(bpm_in * k / bpm_out - 1), k) for k in (1.0, 2.0, 0.5)), key=lambda e: e[0])
    err, k = best
    matched = err <= tolerance
    return {"matched": matched, "factor": k, "error": round(err, 4),
            "ratio": bpm_out / (bpm_in * k) if matched else 1.0}


def equal_power(x) -> tuple[np.ndarray, np.ndarray]:
    """Equal-power crossfade gains at positions x ∈ [0, 1] → (outgoing, incoming); out² + in² = 1."""
    x = np.clip(np.asarray(x, float), 0.0, 1.0)
    return np.cos(np.pi / 2 * x), np.sin(np.pi / 2 * x)


def out_point(s: dict, earliest: float, overlap_src: float, bars: int = 8) -> float:
    """Source second where the outgoing song is gone: a phrase end before the outro gets quiet.

    earliest: the overlap may not start before this (in-point + head + minimum play).
    Falls back to bar ends, beats and finally the quiet point itself.
    """
    p = s["profile"]
    limit = min(p["outro_start"], p["end"]) + 0.05
    lo = earliest + overlap_src
    if s["tempo"]:
        for period in (BEATS_PER_BAR * bars, BEATS_PER_BAR, 1):
            c = _grid(s, period, lo, limit)
            if c:
                return round(c[-1], 4)
    return round(max(min(p["outro_start"], p["end"]), lo), 4)


def _transition_shape(out_s: dict, in_s: dict, cfg: dict) -> dict:
    """Overlap length (source and mix seconds), stretch and filter of one transition."""
    if not cfg["enabled"]:
        return {"matched": False, "factor": 1.0, "ratio": 1.0, "bars": 0, "overlap_src": 2.0,
                "overlap_mix": 2.0, "cutoff_hz": 0.0}
    m = tempo_match(out_s["tempo"], in_s["tempo"], cfg["tempo_tolerance"])
    if m["matched"]:
        bar_mix = BEATS_PER_BAR * out_s["interval"] * m["ratio"]
        bars = next((b for b in (8, 4, 2) if b * bar_mix <= cfg["max_overlap"] + 1e-6), 1)
        src = bars * BEATS_PER_BAR * out_s["interval"]
        return {**m, "bars": bars, "overlap_src": src, "overlap_mix": src * m["ratio"],
                "cutoff_hz": cfg["crossover_hz"]}
    if out_s["tempo"]:
        n = max(int(round(cfg["short_overlap"] / out_s["interval"])), 2)
        src = n * out_s["interval"]
    else:
        src = cfg["short_overlap"]
    return {**m, "ratio": 1.0, "bars": 0, "overlap_src": src, "overlap_mix": src, "cutoff_hz": cfg["gentle_hz"]}


def song_gain(s: dict, target: float) -> float:
    lufs = s.get("lufs")
    if lufs is None or not math.isfinite(float(lufs)) or float(lufs) <= -70:
        return 0.0
    return round(float(np.clip(target - float(lufs), -30.0, 18.0)), 2)


def plan_mix(songs: list[dict], cfg: dict | None = None) -> dict:
    """Where every song enters / leaves and how every transition is made (seconds).

    → {"version", "duration", "songs": [{"in", "stretch_from", "out", "ratio", "head", "tail",
       "gain_db", "fade_out", "place"}], "transitions": [{"start", "length", "matched", "factor",
       "ratio", "bars", "cutoff_hz", "boundary", "out_src", "in_src"}], "settings"}
    "in"/"out": source seconds of the song's span; "stretch_from": source second where the stretched
    tail starts (= out − tail source seconds); "head": mix seconds of the song inside the previous
    transition; "tail": mix seconds of the song inside the next one; "place": mix second of "in".
    """
    cfg = settings({"automix": cfg}) if cfg is None or "silence_db" not in cfg else cfg
    if not songs:
        return {"version": AUTOMIX_VERSION, "duration": 0.0, "songs": [], "transitions": [], "settings": cfg}
    n = len(songs)
    ins = [in_point(s, first=(i == 0)) if cfg["enabled"] else s["profile"]["start"] for i, s in enumerate(songs)]
    shapes = [_transition_shape(songs[i], songs[i + 1], cfg) for i in range(n - 1)]
    out_songs, transitions = [], []
    place, head = 0.0, 0.0
    for i, s in enumerate(songs):
        p = s["profile"]
        entry = {"in": ins[i], "place": round(place, 4), "head": round(head, 4),
                 "gain_db": song_gain(s, cfg["target_lufs"])}
        if i < n - 1:
            sh = shapes[i]
            avail = min(p["outro_start"], p["end"]) - ins[i] - head      # the head plays unstretched
            min_play = min(cfg["min_play"], max(avail - sh["overlap_src"], 0) / 2)
            earliest = ins[i] + head + min_play
            if cfg["enabled"]:
                out = out_point(s, earliest, sh["overlap_src"], int(cfg["phrase_bars"]))
            else:
                out = round(max(p["end"], earliest + sh["overlap_src"]), 4)
            src = min(sh["overlap_src"], max(out - ins[i] - head, 0.5))
            if src < sh["overlap_src"] - 1e-6:          # a very short song: smaller overlap
                sh = {**sh, "overlap_src": src, "overlap_mix": src * sh["ratio"]}
            stretch_from = out - sh["overlap_src"]
            start = place + (stretch_from - ins[i])
            tail = sh["overlap_mix"]
            entry.update(out=round(out, 4), stretch_from=round(stretch_from, 4), ratio=sh["ratio"],
                         tail=round(tail, 4), fade_out=0.0)
            transitions.append({"start": round(start, 4), "length": round(tail, 4), "matched": sh["matched"],
                                "factor": sh["factor"], "ratio": round(sh["ratio"], 6), "bars": sh["bars"],
                                "cutoff_hz": sh["cutoff_hz"], "out_src": round(out, 4),
                                "in_src": ins[i + 1]})
            place, head = start, tail
        else:
            end = min(p["end"], p["outro_start"] + cfg["end_fade"]) if cfg["enabled"] else p["end"]
            end = max(end, ins[i] + head + 0.5)
            entry.update(out=round(end, 4), stretch_from=None, ratio=1.0, tail=0.0,
                         fade_out=round(min(cfg["end_fade"], (end - ins[i]) / 3), 3))
            duration = place + (end - ins[i])
        out_songs.append(entry)
    for i, t in enumerate(transitions):            # song boundary: a beat of the incoming song near the middle
        t["boundary"] = _boundary(songs[i + 1], out_songs[i + 1], t)
    return {"version": AUTOMIX_VERSION, "duration": round(duration, 4), "songs": out_songs,
            "transitions": transitions, "settings": cfg}


def _to_mix(entry: dict, t: float) -> float:
    return entry["place"] + (t - entry["in"])


def _boundary(s: dict, entry: dict, tr: dict) -> float:
    mid = tr["start"] + tr["length"] / 2
    if not s["tempo"]:
        return round(mid, 4)
    lo, hi = entry["in"], entry["in"] + tr["length"]
    for grid, reach in ((downbeats(s, lo, hi), tr["length"] / 4), (_grid(s, 1, lo, hi), tr["length"] / 2)):
        if grid:
            best = min((_to_mix(entry, b) for b in grid), key=lambda x: abs(x - mid))
            if abs(best - mid) <= reach + 1e-6:
                return round(best, 4)
    return round(mid, 4)


# ── the mix as seen by the picture ───────────────────────────────────────────

def energy_scores(songs: list[dict], gains: list[float] | None = None) -> list[float]:
    """0-1 per song (relative inside the playlist): onset rate (½), tempo folded into 70-140 BPM (¼)
    and body RMS after the loudness gain (¼). A single song (or equal songs) → 0.5."""
    def fold(bpm: float) -> float:
        if bpm <= 0:
            return 0.0
        while bpm < 70:
            bpm *= 2
        while bpm > 140:
            bpm /= 2
        return bpm

    gains = gains or [0.0] * len(songs)
    feats = np.array([[s["onset_rate"], fold(s["tempo"]), s["profile"]["body_db"] + g]
                      for s, g in zip(songs, gains)], float).reshape(len(songs), 3)
    if not len(songs):
        return []
    span = feats.max(axis=0) - feats.min(axis=0)
    norm = np.where(span > 1e-9, (feats - feats.min(axis=0)) / np.where(span > 1e-9, span, 1), 0.5)
    e = norm @ np.array([0.5, 0.25, 0.25])
    return [round(float(x), 3) for x in e]


def mix_timeline(plan: dict, songs: list[dict]) -> dict:
    """{"duration", "songs": [{"index", "name", "start", "end", "region": [a, b], "tempo", "energy",
    "gain_db"}], "boundaries": [s], "overlaps": [[a, b]], "beats": [s], "downbeats": [s]} — mix seconds.

    Beats come from each song's grid inside its own region (the incoming song's grid from the start
    of the overlap on, where the outgoing tail has been stretched to it)."""
    L = float(plan["duration"])
    entries, trs = plan["songs"], plan["transitions"]
    bounds = [t["boundary"] for t in trs]
    energies = energy_scores(songs, [e["gain_db"] for e in entries])
    out_songs, beats, downs = [], [], []
    for i, (s, e) in enumerate(zip(songs, entries)):
        r0 = 0.0 if i == 0 else trs[i - 1]["start"]
        r1 = trs[i]["start"] if i < len(trs) else L
        lo, hi = e["in"] + (r0 - e["place"]), e["in"] + (r1 - e["place"])
        beats += [_to_mix(e, b) for b in _grid(s, 1, lo, hi) if r0 - 1e-6 <= _to_mix(e, b) < r1 - 1e-6]
        downs += [_to_mix(e, b) for b in downbeats(s, lo, hi) if r0 - 1e-6 <= _to_mix(e, b) < r1 - 1e-6]
        end = trs[i]["start"] + trs[i]["length"] if i < len(trs) else L
        out_songs.append({"index": i, "name": s["name"], "start": round(e["place"], 3), "end": round(end, 3),
                          "region": [round(0.0 if i == 0 else bounds[i - 1], 3), round(bounds[i] if i < len(bounds) else L, 3)],
                          "tempo": s["tempo"], "energy": energies[i], "gain_db": e["gain_db"]})
    keep = lambda xs: sorted({round(x, 4) for x in xs if 0 <= x <= L + 1e-6})
    return {"duration": round(L, 4), "songs": out_songs, "boundaries": [round(b, 4) for b in bounds],
            "overlaps": [[t["start"], round(t["start"] + t["length"], 4)] for t in trs],
            "beats": keep(beats), "downbeats": keep(downs)}


def single_timeline(analysis: dict, start: float, name: str = "music", phase: int = 0) -> dict:
    """The mix timeline of ONE song played from `start` (seconds of the song) to its end."""
    beats = [float(b) for b in analysis.get("beats") or []]
    L = max(float(analysis.get("duration", 0)) - start, 0.0)
    tempo = float(analysis.get("tempo") or 0)
    grid = [b - start for b in beats if 0 <= b - start <= L]
    down = [b - start for b in beats[phase::BEATS_PER_BAR] if 0 <= b - start <= L]
    return {"duration": round(L, 4), "songs": [{"index": 0, "name": name, "start": 0.0, "end": round(L, 3),
                                                 "region": [0.0, round(L, 3)], "tempo": tempo, "energy": 0.5,
                                                 "gain_db": 0.0}],
            "boundaries": [], "overlaps": [], "beats": grid, "downbeats": down}


def splices(plan: dict) -> list[dict]:
    """Transition windows for `sve check`: [{"t" (boundary), "start", "end", "matched"}] (mix seconds)."""
    return [{"t": t["boundary"], "start": t["start"], "end": round(t["start"] + t["length"], 4),
             "matched": t["matched"]} for t in plan["transitions"]]
