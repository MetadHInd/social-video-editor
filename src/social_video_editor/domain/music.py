"""Music math: a regular beat grid from detected beats, the "drop" and where the song starts.

The audio decoding / beat tracking happens in the librosa adapter; this module only does math.
Music analysis dict: {"duration", "tempo" (BPM), "interval" (s per beat), "beats" (grid, s), "drop" (s)}.
"""
from __future__ import annotations

import numpy as np


def fit_beat_grid(beat_times, duration: float) -> tuple[float, list[float]]:
    """Least-squares fit of the detected beats to a fixed grid → (interval, grid inside the song)."""
    bt = np.asarray(beat_times, float)
    interval = float(np.median(np.diff(bt)))
    k = np.round((bt - bt[0]) / interval)
    T, b0 = np.polyfit(k, bt, 1)
    grid = [float(b0 + j * T) for j in range(int(np.floor(-b0 / T)), int((duration - b0) / T) + 1)]
    return T, [g for g in grid if 0 <= g <= duration]


def find_drop(beat_times, grid: list[float], T: float, rms, rms_times, duration: float) -> float:
    """Where the drums come in / the energy jumps: first beat after a beat-less intro,
    or else the biggest energy jump (8 s after vs 8 s before)."""
    bt = np.asarray(beat_times, float)
    rms, tr = np.asarray(rms, float), np.asarray(rms_times, float)

    def level(a, b):
        m = (tr >= a) & (tr < b)
        return 20 * np.log10(rms[m].mean() + 1e-9) if m.any() else -90

    if bt[0] > 4 * T:
        return float(bt[0])
    jumps = [(level(g, g + 8) - level(g - 8, g), g) for g in grid if 8 <= g <= duration - 8]
    return max(jumps)[1] if jumps else 0.0


def analyze_music(duration: float, beat_times, rms, rms_times) -> dict:
    bt = np.asarray(beat_times, float)
    if len(bt) < 4:
        return {"duration": duration, "tempo": 0, "beats": [], "drop": 0.0}
    T, grid = fit_beat_grid(bt, duration)
    drop = find_drop(bt, grid, T, rms, rms_times, duration)
    return {"duration": duration, "tempo": round(60 / T, 2), "interval": T, "beats": grid, "drop": drop}


def beats_in_video(analysis: dict, start: float, total: float) -> list[float]:
    """Song beats in video time (the song starts playing at second `start` of the song)."""
    return [g - start for g in analysis["beats"] if 0 <= g - start <= total + 1]


def auto_start(analysis: dict, drop_at: float) -> float:
    """Second of the song to start from so that its drop lands at `drop_at` (video seconds)."""
    if not analysis.get("beats"):
        return 0.0
    return max(analysis["drop"] - drop_at, 0.0)   # if the drop comes earlier, the song starts at 0
