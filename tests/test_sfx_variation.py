"""Typing clicks: per-keystroke variation (pitch, level, timing), deterministic, overrides kept."""
from __future__ import annotations

import numpy as np
import pytest

from social_video_editor.adapters.ffmpeg.sfx import SR, VARIED, FfmpegSoundEffects

sf = pytest.importorskip("soundfile")


def _hits(track: np.ndarray, times: list[float], n: int) -> list[np.ndarray]:
    return [track[int(t * SR) - 600: int(t * SR) - 600 + n, 0] for t in times]


def test_keystrokes_vary_but_are_deterministic(tmp_path, workspace):
    fx = FfmpegSoundEffects(lambda: workspace / "sfx")
    times = [0.2 + 0.1 * k for k in range(12)]
    events = [("keystroke", t, -12.0) for t in times]
    a = sf.read(fx.mix_track(events, 2.0, tmp_path / "a.wav"), dtype="float32")[0]
    b = sf.read(fx.mix_track(events, 2.0, tmp_path / "b.wav"), dtype="float32")[0]
    assert np.array_equal(a, b)                                    # seeded: same edit, same track

    sample = sf.read(workspace / "sfx" / "keystroke.wav", dtype="float32")[0][:, 0]
    hits = _hits(a, times, len(sample) + 1200)
    peaks = [np.abs(h).max() for h in hits]
    assert max(peaks) / min(peaks) > 1.1                          # level varies (±2.5 dB)…
    assert max(peaks) / min(peaks) < 10 ** (2 * 2.6 / 20)          # …within the range
    onsets = [int(np.argmax(np.abs(h) > 0.2 * np.abs(h).max())) for h in hits]
    jitter = (np.array(onsets) - 600) / SR
    assert np.abs(jitter).max() <= VARIED["keystroke"]["jitter_s"] + 0.002
    assert len(set(onsets)) > 1                                    # timing varies
    # consecutive hits are never the exact same waveform (another pitch variant)
    for h1, h2 in zip(hits, hits[1:]):
        n1, n2 = h1 / np.abs(h1).max(), h2 / np.abs(h2).max()
        assert not np.allclose(n1, n2, atol=1e-3)


def test_other_sounds_unchanged_and_custom_keystroke_file_used(tmp_path, workspace):
    folder = workspace / "sfx"
    folder.mkdir()
    t = np.arange(int(0.05 * SR)) / SR
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype("float32")
    sf.write(folder / "tecla.wav", np.stack([tone, tone], 1), SR)       # legacy Spanish override
    fx = FfmpegSoundEffects(lambda: folder)
    track = sf.read(fx.mix_track([("keystroke", 0.5, 0.0)], 1.0, tmp_path / "k.wav"), dtype="float32")[0][:, 0]
    seg = track[int(0.48 * SR): int(0.6 * SR)]
    f = np.fft.rfftfreq(len(seg), 1 / SR)[np.argmax(np.abs(np.fft.rfft(seg)))]
    assert 1000 * 0.9 < f < 1000 * 1.1                             # the user's sound, pitch ± ≤ 8 %
    pop = sf.read(fx.mix_track([("pop", 0.5, 0.0)], 1.0, tmp_path / "p.wav"), dtype="float32")[0]
    ref = sf.read(folder / "pop.wav", dtype="float32", always_2d=True)[0]
    i = int(0.5 * SR)
    assert np.allclose(pop[i:i + len(ref)], ref[: len(pop) - i], atol=1e-4)   # no variation on other sounds
