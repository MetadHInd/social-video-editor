"""Music math: tempo choice, a regular beat grid, the "drop" and where the song starts.

The audio decoding / onset envelope / beat tracking happens in the librosa adapter; this module only
does math. Music analysis dict:
  {"duration", "tempo" (BPM), "interval" (s per beat), "beats" (grid, s), "drop" (s),
   "confidence" (0-1), "alternatives": [{"bpm", "strength"}, …], "v"}

Tempo disambiguation: beat trackers often lock onto half or double the real tempo (a click track
at 120 BPM read as 60). The candidate periods T/2, T and 2T are compared by the strength of the
onset-envelope autocorrelation at that lag ("pulse clarity"); 70–150 BPM is preferred unless a
candidate outside that range is clearly stronger. Recipe overrides: music.bpm (force) and
music.tempo_factor (0.5 / 2), applied with retime().
"""
from __future__ import annotations

import math

import numpy as np

ANALYSIS_VERSION = 2                 # cached analyses with another version are recomputed
PREFERRED_BPM = (70.0, 150.0)
OUT_OF_RANGE_WEIGHT = 0.65           # a tempo outside the range must be ≈1.5× stronger to win…
LAG_TOLERANCE = 0.06                 # …searched ±6 % around each candidate period


# ── tempo ────────────────────────────────────────────────────────────────────

def onset_autocorrelation(envelope) -> np.ndarray:
    """Unbiased, normalized autocorrelation of an onset envelope (ac[0] = 1)."""
    x = np.asarray(envelope, float)
    x = x - x.mean()
    n = len(x)
    if n < 4 or not np.any(x):
        return np.zeros(max(n, 1))
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (n - np.arange(n))
    return ac / ac[0]


def pulse_strength(ac: np.ndarray, lag: float, tol: float = LAG_TOLERANCE) -> tuple[float, float]:
    """Autocorrelation peak near `lag` frames → (strength, refined lag in frames, sub-frame)."""
    lo = max(int(math.floor(lag * (1 - tol))), 1)
    hi = min(int(math.ceil(lag * (1 + tol))), len(ac) - 2)
    if hi < lo:
        return 0.0, float(lag)
    i = lo + int(np.argmax(ac[lo:hi + 1]))
    a, b, c = ac[i - 1], ac[i], ac[i + 1]
    den = a - 2 * b + c
    d = 0.5 * (a - c) / den if den < 0 else 0.0
    d = float(np.clip(d, -0.5, 0.5))
    return float(b - 0.25 * (a - c) * d), i + d


def tempo_weight(bpm: float, preferred: tuple[float, float] = PREFERRED_BPM) -> float:
    """1 inside the preferred range; outside, OUT_OF_RANGE_WEIGHT halved per octave of distance."""
    lo, hi = preferred
    if lo <= bpm <= hi:
        return 1.0
    octaves = abs(math.log2(bpm / (lo if bpm < lo else hi)))
    return OUT_OF_RANGE_WEIGHT * 2 ** (-octaves)


def choose_tempo(envelope, fps: float, base_period: float,
                 preferred: tuple[float, float] = PREFERRED_BPM) -> dict:
    """Picks among the periods T/2, T and 2T (T = base_period, seconds) by pulse clarity.

    → {"bpm", "period", "confidence", "strength", "alternatives": [{"bpm", "strength"}, …]}
    confidence = 1 − (score of the runner-up / score of the winner).
    """
    ac = onset_autocorrelation(envelope)
    cands = []
    for mult in (0.5, 1.0, 2.0):
        lag = base_period * mult * fps
        if lag < 2 or lag > len(ac) / 2:           # at least two repetitions inside the song
            continue
        s, refined = pulse_strength(ac, lag)
        bpm = 60.0 * fps / refined
        cands.append({"bpm": bpm, "period": refined / fps, "strength": s,
                      "score": max(s, 0.0) * tempo_weight(bpm, preferred)})
    if not cands:
        return {"bpm": 60.0 / base_period, "period": base_period, "confidence": 0.0, "strength": 0.0,
                "alternatives": []}
    cands.sort(key=lambda c: c["score"], reverse=True)
    best = cands[0]
    second = cands[1]["score"] if len(cands) > 1 else 0.0
    confidence = 1 - second / best["score"] if best["score"] > 0 else 0.0
    return {"bpm": best["bpm"], "period": best["period"], "strength": round(best["strength"], 3),
            "confidence": round(float(np.clip(confidence, 0, 1)), 2),
            "alternatives": [{"bpm": round(c["bpm"], 2), "strength": round(c["strength"], 3)} for c in cands[1:]]}


def global_period(envelope, fps: float, bpm_range: tuple[float, float] = (60.0, 180.0)) -> float:
    """Strongest autocorrelation period inside a BPM range (when no beat tracker result is usable)."""
    ac = onset_autocorrelation(envelope)
    lo, hi = int(60 * fps / bpm_range[1]), min(int(60 * fps / bpm_range[0]) + 1, len(ac) - 1)
    if hi <= lo:
        return 0.5
    return (lo + int(np.argmax(ac[lo:hi]))) / fps


# ── beat grid ────────────────────────────────────────────────────────────────

def fit_beat_grid(beat_times, duration: float) -> tuple[float, list[float]]:
    """Least-squares fit of the detected beats to a fixed grid → (interval, grid inside the song)."""
    bt = np.asarray(beat_times, float)
    interval = float(np.median(np.diff(bt)))
    k = np.round((bt - bt[0]) / interval)
    T, b0 = np.polyfit(k, bt, 1)
    return float(T), grid_from(float(b0), float(T), duration)


def grid_from(anchor: float, period: float, duration: float) -> list[float]:
    """Every anchor + k·period inside [0, duration]."""
    k0 = int(math.ceil(-anchor / period - 1e-9))
    k1 = int(math.floor((duration - anchor) / period + 1e-9))
    return [float(anchor + k * period) for k in range(k0, k1 + 1)]


def comb_grid(envelope, fps: float, period: float, duration: float, span: float = 0.015) -> tuple[float, list[float]]:
    """Regular grid that best follows the onsets: period (±span) and phase maximizing the envelope
    on the grid, then a least-squares fit to the onset peaks next to each grid beat."""
    env = np.asarray(envelope, float)
    t_env = np.arange(len(env)) / fps
    best = (-np.inf, period, 0.0)
    for P in period * np.linspace(1 - span, 1 + span, 61):
        n = int(duration / P) + 1
        phases = np.arange(0, P, 1 / fps)
        times = phases[:, None] + P * np.arange(n)[None, :]
        vals = np.interp(times, t_env, env, right=0.0).mean(axis=1)
        j = int(np.argmax(vals))
        if vals[j] > best[0]:
            best = (vals[j], float(P), float(phases[j]))
    _, P, phase = best
    grid = np.array(grid_from(phase, P, duration))
    # refine: the strongest onset within ±12 % of a period of each grid beat
    w = max(int(0.12 * P * fps), 1)
    peaks_t, peaks_v = [], []
    for g in grid:
        i = int(round(g * fps))
        lo, hi = max(i - w, 0), min(i + w + 1, len(env))
        if hi <= lo:
            continue
        k = lo + int(np.argmax(env[lo:hi]))
        peaks_t.append(k / fps)
        peaks_v.append(env[k])
    if len(peaks_t) >= 4:
        pt, pv = np.array(peaks_t), np.array(peaks_v)
        strong = pv >= np.percentile(pv, 40)
        if strong.sum() >= 4:
            k = np.round((pt[strong] - phase) / P)
            T, b0 = np.polyfit(k, pt[strong], 1)
            if abs(T / P - 1) < span:
                P, phase = float(T), float(b0)
    return P, grid_from(phase, P, duration)


def find_drop(beat_times, grid: list[float], T: float, rms, rms_times, duration: float) -> float:
    """Where the drums come in / the energy jumps: first beat after a beat-less intro,
    or else the biggest energy jump (8 s after vs 8 s before)."""
    bt = np.asarray(beat_times, float)
    rms, tr = np.asarray(rms, float), np.asarray(rms_times, float)

    def level(a, b):
        m = (tr >= a) & (tr < b)
        return 20 * np.log10(rms[m].mean() + 1e-9) if m.any() else -90

    if len(bt) and bt[0] > 4 * T:
        return float(bt[0])
    jumps = [(level(g, g + 8) - level(g - 8, g), g) for g in grid if 8 <= g <= duration - 8]
    return max(jumps)[1] if jumps else 0.0


def analyze_music(duration: float, beat_times, rms, rms_times, onset_env=None, onset_fps: float | None = None) -> dict:
    """Tempo (disambiguated when the onset envelope is given), beat grid and drop."""
    bt = np.asarray(beat_times, float)
    have_env = onset_env is not None and onset_fps and len(onset_env) > 8
    if len(bt) < 4 and not have_env:
        return {"duration": duration, "tempo": 0, "beats": [], "drop": 0.0, "v": ANALYSIS_VERSION}
    if not have_env:
        T, grid = fit_beat_grid(bt, duration)
        drop = find_drop(bt, grid, T, rms, rms_times, duration)
        return {"duration": duration, "tempo": round(60 / T, 2), "interval": T, "beats": grid, "drop": drop,
                "confidence": None, "alternatives": [], "v": ANALYSIS_VERSION}
    base = fit_beat_grid(bt, duration)[0] if len(bt) >= 4 else global_period(onset_env, onset_fps)
    choice = choose_tempo(onset_env, onset_fps, base)
    T, grid = comb_grid(onset_env, onset_fps, choice["period"], duration)
    drop = find_drop(bt, grid, T, rms, rms_times, duration)
    return {"duration": duration, "tempo": round(60 / T, 2), "interval": T, "beats": grid, "drop": drop,
            "confidence": choice["confidence"], "alternatives": choice["alternatives"], "v": ANALYSIS_VERSION}


def retime(analysis: dict, bpm: float | None = None, factor: float | None = None) -> dict:
    """Recipe overrides: music.bpm forces the tempo, music.tempo_factor multiplies it (0.5 / 2).

    The new grid keeps the drop (or the first beat) as its anchor, so halving keeps the beats of the
    drop's parity and doubling adds the off-beats. Returns a new dict; unchanged if no override.
    """
    if not bpm and not factor:
        return analysis
    if not analysis.get("beats") and not bpm:
        return analysis
    tempo = float(bpm or analysis["tempo"]) * float(factor or 1.0)
    if tempo <= 0:
        raise ValueError("music.bpm × music.tempo_factor must be positive")
    period = 60.0 / tempo
    beats = analysis.get("beats") or [0.0]
    drop = float(analysis.get("drop") or 0.0)
    anchor = min(beats, key=lambda b: abs(b - drop)) if analysis.get("beats") else drop
    out = dict(analysis)
    out.update(tempo=round(tempo, 2), interval=period,
               beats=grid_from(anchor, period, float(analysis["duration"])),
               detected_tempo=analysis.get("tempo"), forced=True)
    return out


def beats_in_video(analysis: dict, start: float, total: float) -> list[float]:
    """Song beats in video time (the song starts playing at second `start` of the song)."""
    return [g - start for g in analysis["beats"] if 0 <= g - start <= total + 1]


def auto_start(analysis: dict, drop_at: float) -> float:
    """Second of the song to start from so that its drop lands at `drop_at` (video seconds)."""
    if not analysis.get("beats"):
        return 0.0
    return max(analysis["drop"] - drop_at, 0.0)   # if the drop comes earlier, the song starts at 0


def tempo_lines(analysis: dict) -> list[str]:
    """Human summary of the tempo choice (sve music, render log)."""
    if not analysis.get("tempo"):
        return ["Tempo: no beat found"]
    conf = analysis.get("confidence")
    head = f"Tempo: {analysis['tempo']} BPM" + (f" (confidence {conf:.2f})" if conf is not None else "")
    if analysis.get("forced"):
        head += f" — forced by the recipe (detected {analysis.get('detected_tempo')} BPM)"
    lines = [head]
    alts = analysis.get("alternatives") or []
    if alts:
        lines.append("  alternatives: " + ", ".join(f"{a['bpm']:g} BPM (strength {a['strength']:.2f})" for a in alts))
        lines.append("  to change it: settings → music → bpm: N, or tempo_factor: 0.5 / 2")
    return lines
