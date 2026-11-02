"""Regression tests for the audited bugs."""
from __future__ import annotations

import copy

import pytest

from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.domain import color_math
from social_video_editor.domain.pieces import APPEAR_MARGIN, appearance_times
from social_video_editor.domain.selection import best_window
from social_video_editor.domain.timeline import align_to_beats, compute_timeline


def test_drawn_shot_without_dur_or_beats_aligns(cfg):
    """Was a KeyError: alignment read shot["dur"] of a polaroid/collage that had neither dur nor beats."""
    plan = {"shots": [{"source": 0, "start": 0.0, "end": 1.0, "beats": 2},
                      {"type": "polaroid", "from_shot": 0},                   # natural 1.2 s = 2 beats of 0.6
                      {"type": "collage", "from_shots": [0]}]}
    beats = [k * 0.6 for k in range(40)]
    assert align_to_beats(plan, beats, cfg, [60.0]) == 3
    assert plan["shots"][1]["dur"] == pytest.approx(1.2, abs=1 / 30)
    tl = compute_timeline(plan, cfg)
    assert tl.total == pytest.approx(6 * 0.6, abs=1 / 30)


def test_window_search_uses_the_analysis_audio_step():
    """Was hard-coded to 0.1 s: with levels every 0.25 s the loud moment was read at the wrong time."""
    samples = [{"t": k / 4, "brightness": 0.5, "sharpness": 50.0, "color": 10.0, "motion": 1.0}
               for k in range(41)]
    step = 0.25
    db = [-40.0] * 40
    for i in range(int(6.0 / step), int(7.0 / step) + 1):
        db[i] = -5.0                                            # loud at 6–7 s, nothing else differs
    an = {"duration": 10.0, "samples": samples, "audio": {"step": step, "db": db, "median_db": -40.0}}
    assert best_window(an, [0, 10], 1.0, "action") == (6.0, 7.0)


@pytest.mark.parametrize("rate,buf", [("20M", "40M"), ("7.5M", "15M"), ("16M", "32M"), ("800k", "1600k"),
                                      (20000000, "40000000")])
def test_maxrate_parsing(rate, buf, cfg):
    """'7.5M' crashed with int('7.5')."""
    assert filters.double_rate(rate) == buf
    c = copy.deepcopy(cfg)
    c["export"]["maxrate"] = rate
    opts = filters.video_output_options(c, draft=False)
    assert opts[opts.index("-maxrate") + 1] == str(rate) and opts[opts.index("-bufsize") + 1] == buf


def test_focus_y_inherits_from_config(cfg):
    """focus_x came from the recipe when the shot had none; focus_y always fell back to 0.5."""
    info = {"width": 1080, "height": 2400}                      # taller than 9:16 → vertical crop
    c = copy.deepcopy(cfg)
    c["framing"]["mode"] = "crop"
    c["framing"]["focus_y"] = 0.0
    W, H = 540, 960
    assert filters.prescale(info, W, H, c, {}).endswith(":0:0")            # top of the frame
    c["framing"]["focus_y"] = 1.0
    top = filters.prescale(info, W, H, c, {})
    assert not top.endswith(":0:0")
    assert filters.prescale(info, W, H, c, {"focus_y": 0.0}).endswith(":0:0")   # the shot still wins


def test_creative_look_computed_once(cfg, monkeypatch):
    """_creativo was called twice per correction."""
    calls = []
    real = color_math.creative
    monkeypatch.setattr(color_math, "creative", lambda c: calls.append(1) or real(c))
    stats = {"luma": 0.4, "p02": 0.05, "p98": 0.9, "r": 0.4, "g": 0.4, "b": 0.4, "sat": 0.3}
    chain = filters.correction_chain(stats, cfg["color"])
    assert len(calls) == 1
    assert chain.count("vibrance=") == 1 and chain.count("cas=") == 1
    assert chain.startswith("colorlevels=") and "eq=gamma=" in chain


def test_collage_photos_all_appear_before_the_end():
    """15 photos / 2.0 s on a quarter-beat grid: the last one appeared at 2.05 s (after the end)."""
    shot = {"dur": 2.0, "_beats": [0.3, 0.8, 1.3, 1.8]}
    times = appearance_times(shot, 15, 4)
    assert len(times) == 15 and times == sorted(times)
    assert max(times) <= 2.0 - APPEAR_MARGIN
    for n in range(1, 30):                                      # any number of photos, any duration
        for dur in (0.3, 1.0, 2.0, 5.0):
            assert all(0 <= t <= max(dur - APPEAR_MARGIN, 0) for t in appearance_times(
                {"dur": dur, "_beats": [0.2, 0.7, 1.2]}, n, 4))
