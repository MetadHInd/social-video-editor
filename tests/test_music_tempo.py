"""Tempo disambiguation (T/2 · T · 2T), recipe overrides and the analyzer on synthetic click tracks."""
from __future__ import annotations

import numpy as np
import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.domain.music import (choose_tempo, grid_from, retime, tempo_lines, tempo_weight)

SR = 22050


def click_track(bpm: float, dur: float = 20.0, sr: int = SR) -> np.ndarray:
    """A short decaying 1 kHz click on every beat over a quiet low tone."""
    t = np.arange(int(dur * sr)) / sr
    phase = np.mod(t, 60.0 / bpm)
    return (0.8 * np.sin(2 * np.pi * 1000 * t) * np.exp(-phase * 40) + 0.1 * np.sin(2 * np.pi * 110 * t)).astype("float32")


def envelope(bpm: float, dur: float = 20.0, fps: float = 100.0) -> np.ndarray:
    """Synthetic onset envelope: a spike on every beat."""
    env = np.zeros(int(dur * fps))
    for b in np.arange(0, dur, 60.0 / bpm):
        env[int(round(b * fps))] = 1.0
    return env


# ── pure logic ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bpm", [90, 120, 140])
@pytest.mark.parametrize("tracker_factor", [0.5, 1.0, 2.0])
def test_choose_tempo_recovers_the_real_tempo_from_half_or_double(bpm, tracker_factor):
    # the beat tracker says T·factor (e.g. 60 BPM for a 120 BPM track): T/2, T, 2T are compared
    base = 60.0 / bpm / tracker_factor
    c = choose_tempo(envelope(bpm), 100.0, base)
    assert c["bpm"] == pytest.approx(bpm, abs=1.5)
    assert 0 <= c["confidence"] <= 1
    assert c["alternatives"]


def test_choose_tempo_keeps_a_slow_tempo_when_nothing_else_is_strong():
    c = choose_tempo(envelope(60), 100.0, 1.0)
    assert c["bpm"] == pytest.approx(60, abs=1)          # 120 has no onsets, 30 is further out of range


def test_tempo_weight_prefers_70_to_150():
    assert tempo_weight(100) == tempo_weight(70) == tempo_weight(150) == 1.0
    assert tempo_weight(60) < 1.0 and tempo_weight(30) < tempo_weight(60)
    assert tempo_weight(300) < tempo_weight(170) < 1.0


def test_grid_from_covers_the_song():
    g = grid_from(0.25, 0.5, 2.0)
    assert g == pytest.approx([0.25, 0.75, 1.25, 1.75])
    assert grid_from(1.1, 0.5, 2.0)[0] == pytest.approx(0.1)


def test_retime_factor_and_forced_bpm():
    am = {"duration": 10.0, "tempo": 60.0, "interval": 1.0, "beats": grid_from(0.2, 1.0, 10.0), "drop": 4.2}
    assert retime(am) is am
    double = retime(am, factor=2)
    assert double["tempo"] == 120 and double["interval"] == pytest.approx(0.5)
    assert 4.2 in [round(b, 3) for b in double["beats"]] and 4.7 in [round(b, 3) for b in double["beats"]]
    half = retime(am, factor=0.5)
    assert half["tempo"] == 30 and 4.2 in [round(b, 3) for b in half["beats"]]      # keeps the drop's parity
    forced = retime(am, bpm=100)
    assert forced["tempo"] == 100 and forced["forced"] and forced["detected_tempo"] == 60
    assert np.allclose(np.diff(forced["beats"]), 0.6)
    assert retime(am, bpm=100, factor=2)["tempo"] == 200
    assert "forced" in tempo_lines(forced)[0]


def test_legacy_spanish_tempo_keys():
    cfg = legacy_es.config({"musica": {"bpm": 120, "factor_tempo": 0.5}})
    assert cfg["music"] == {"bpm": 120, "tempo_factor": 0.5}


def test_recipe_defaults_have_the_tempo_keys(cfg):
    assert cfg["music"]["bpm"] is None and cfg["music"]["tempo_factor"] is None


# ── librosa adapter on audio ─────────────────────────────────────────────────

@pytest.mark.parametrize("bpm", [60, 90, 120, 140])
def test_analyzer_click_tracks(tmp_path, bpm):
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    from social_video_editor.adapters.librosa.audio_analyzer import LibrosaAudioAnalyzer

    wav = tmp_path / f"clicks{bpm}.wav"
    sf.write(wav, click_track(bpm), SR)
    am = LibrosaAudioAnalyzer().music(wav)
    assert am["tempo"] == pytest.approx(bpm, abs=0.5)
    period = 60.0 / bpm
    beats = np.asarray(am["beats"])
    assert np.allclose(np.diff(beats), period, atol=0.002)
    # on the clicks (clicks at k·period; the onset envelope may lag by a frame or two)
    off = np.mod(beats - period / 2, period) - period / 2
    assert np.abs(off).max() < 0.03
    assert am["alternatives"] and 0 <= am["confidence"] <= 1
    lines = tempo_lines(am)
    assert f"{am['tempo']} BPM" in lines[0] and "alternatives" in lines[1]
