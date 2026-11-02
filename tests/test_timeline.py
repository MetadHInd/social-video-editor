"""Timeline, alignment of cuts to music beats and texts in absolute time."""
from __future__ import annotations

import copy

import numpy as np
import pytest

from social_video_editor.domain.timeline import (absolute_texts, align_to_beats, compute_timeline, frames,
                                                 snap_cuts_to_beats)

FPS = 30


def _plan():
    return {"shots": [
        {"source": 0, "start": 0.0, "end": 2.0, "transition": "cut"},
        {"source": 0, "start": 5.0, "end": 6.5, "speed": 1.5, "transition": "fade_black"},   # fade_black = 0.4 s fixed
        {"type": "polaroid", "from_shot": 1, "dur": 1.2, "transition": "cut"},
        {"type": "collage", "from_shots": [0, 1], "beats": 3, "transition": "zoom"},    # no dur: 0.6 s per beat
    ]}


def test_frames():
    assert frames(1.0) == 1.0
    assert frames(0.22) == pytest.approx(7 / 30)
    assert frames(0.4) == pytest.approx(12 / 30)


def test_compute_timeline(cfg):
    tl = compute_timeline(_plan(), cfg)
    assert [c.index for c in tl] == [0, 1, 2, 3]
    assert [c.dur for c in tl] == pytest.approx([2.0, 1.0, 1.2, 1.8])
    # the entry transition overlaps the shot with the previous one
    assert [c.overlap for c in tl] == pytest.approx([0.0, 0.4, 0.0, 7 / 30])
    assert [c.out_start for c in tl] == pytest.approx([0.0, 1.6, 2.6, 3.5667], abs=1e-4)
    assert [c.out_end for c in tl] == pytest.approx([2.0, 2.6, 3.8, 5.3667], abs=1e-4)
    assert tl.total == pytest.approx(5.3667, abs=1e-4)
    assert compute_timeline({"shots": []}, cfg).total == 0.0
    assert tl[2].piece.kind == "polaroid" and tl[2].data["from_shot"] == 1     # keeps the shot's fields
    assert tl[2].as_dict()["from_shot"] == 1 and tl[2].as_dict()["dur"] == pytest.approx(1.2)
    assert tl.cut(0) == pytest.approx(1.8) and tl.cut(3) == tl[3].out_end


def test_overlap_limited_to_half_shot(cfg):
    plan = {"shots": [{"source": 0, "start": 0, "end": 2.0},
                      {"source": 0, "start": 3, "end": 3.4, "transition": "fade_black"},   # 0.4 s: overlap ≤ 0.2
                      {"source": 0, "start": 5, "end": 7, "transition": "fade_black"}]}    # previous 0.4 → ≤ 0.2
    tl = compute_timeline(plan, cfg)
    assert [c.overlap for c in tl] == pytest.approx([0.0, 0.2, 0.2])


def test_freeze_adds_duration(cfg):
    plan = {"shots": [{"source": 0, "start": 0, "end": 1.0, "freeze": 0.5,
                       "effects": [{"type": "freeze", "seconds": 0.3}]}]}
    assert compute_timeline(plan, cfg)[0].dur == pytest.approx(1.8)


def test_absolute_texts(cfg):
    plan = _plan()
    tl = compute_timeline(plan, cfg)
    plan["texts"] = [
        {"text": "absolute", "start": 0.5, "end": 100},                 # clipped at the end of the video
        {"text": "shot0", "shot": 0},                                   # until the next one starts
        {"text": "rel", "shot": 1, "start": 0.1, "end": 0.5},           # waits for the transition to end
        {"text": "several", "shot": 1, "until_shot": 2},
        {"text": "empty", "shot": 1, "start": 0.1, "end": 0.3},         # end <= start → dropped
        {"text": "last", "shot": 3, "start": 0.5},
    ]
    res = {t["text"]: (t["start"], t["end"]) for t in absolute_texts(plan, tl)}
    assert set(res) == {"absolute", "shot0", "rel", "several", "last"}
    assert res["absolute"] == pytest.approx((0.5, 5.3667), abs=1e-4)
    assert res["shot0"] == pytest.approx((0.0, 1.6))
    assert res["rel"] == pytest.approx((2.0, 2.1))
    assert res["several"] == pytest.approx((2.0, 3.5667), abs=1e-4)   # until_shot 2, cut when 3 enters
    assert res["last"] == pytest.approx((3.5667 + 0.5, 5.3667), abs=1e-4)
    assert plan["texts"][2]["start"] == 0.1                           # the plan is not modified


def _beats(T, n=80, start=0.0):
    return [start + k * T for k in range(n)]


def _cuts(tl):
    """Middle of every entry transition (= moment of the cut), shots 1..n."""
    return [c.out_start + c.overlap / 2 for c in tl.clips[1:]]


@pytest.fixture
def cfg_beats(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["transitions"]["duration"] = 0.2          # 6 frames: the middle of the transition falls on a frame
    return cfg


def _plan_beats():
    return {"shots": [
        {"source": 0, "start": 1.0, "end": 2.0, "beats": 3},
        {"source": 0, "start": 10.0, "end": 11.0, "beats": 2, "speed": 1.5, "transition": "flash"},
        {"source": 0, "start": 50.0, "end": 51.0, "beats": 2, "speed": 0.5, "continuous": True,
         "transition": "cut"},
        {"type": "polaroid", "from_shot": 2, "beats": 1, "transition": "cut"},
        {"source": 1, "start": 3.0, "end": 4.0, "transition": "zoom"},           # no "beats": natural length
        {"type": "collage", "from_shots": [0, 1], "beats": 4, "transition": "fade_black"},
    ]}


def test_align_to_beats_every_cut_on_a_beat(cfg_beats):
    T = 0.5                                              # 120 BPM: every beat falls on an exact frame
    g = _beats(T)
    plan = _plan_beats()
    n = align_to_beats(plan, g, cfg_beats, [60.0, 60.0])
    assert n == 6
    tl = compute_timeline(plan, cfg_beats)
    beats_per_shot = [3, 2, 2, 1, 2, 4]                  # shot 4 (1 s natural) = 2 beats
    expected = list(np.cumsum(beats_per_shot) * T)
    for c, e in zip(_cuts(tl), expected[:-1]):
        assert c == pytest.approx(e, abs=2e-4)
    assert tl.total == pytest.approx(expected[-1], abs=2e-4)   # the video ends on a beat
    # continuous: shot 2 starts exactly where shot 1 ended (ramps)
    assert plan["shots"][2]["start"] == plan["shots"][1]["end"]
    # drawn shots change their "dur"
    # (every shot extends half a transition into the next one and half into the previous one)
    assert plan["shots"][3]["dur"] == pytest.approx(T + 0.2 / 2, abs=1e-4)      # leaves with "zoom" (0.2 s)
    assert plan["shots"][5]["dur"] == pytest.approx(4 * T + 0.4 / 2, abs=1e-4)  # enters with "fade_black" (0.4 s)
    # every shot keeps its inner beats (relative to its start)
    tl0 = tl[0]
    assert plan["shots"][0]["_beats"] == pytest.approx([T, 2 * T, 3 * T], abs=1e-3)   # incl. the one of the cut
    for s, c in zip(plan["shots"], tl):
        for b in s["_beats"]:
            assert 0.05 < b < c.dur - 0.05
    assert tl0.out_start == 0.0


def test_align_to_beats_real_tempo_half_frame(cfg_beats):
    T = 0.6977                                           # tempo that does not match the frames
    g = _beats(T, start=0.0)
    plan = _plan_beats()
    align_to_beats(plan, g, cfg_beats, [60.0, 60.0])
    tl = compute_timeline(plan, cfg_beats)
    expected = np.cumsum([3, 2, 2, 1, 1, 4]) * T          # shot 4: 1 s natural ≈ 1 beat
    for c, e in zip(_cuts(tl), expected[:-1]):
        assert abs(c - e) <= 1 / (2 * FPS) + 1e-3          # at most half a frame of rounding


def test_align_to_beats_short_source_slides_the_shot(cfg_beats):
    plan = {"shots": [{"source": 0, "start": 9.0, "end": 9.5, "beats": 4},
                      {"source": 0, "start": 0.0, "end": 1.0, "beats": 2}]}
    align_to_beats(plan, _beats(0.5), cfg_beats, [10.0])
    s0 = plan["shots"][0]
    assert s0["end"] == pytest.approx(9.98)                # never past the end of the source
    assert s0["end"] - s0["start"] == pytest.approx(2.0, abs=1e-3)


def test_align_to_beats_without_beats(cfg_beats):
    plan = _plan_beats()
    before = copy.deepcopy(plan)
    assert align_to_beats(plan, [1.0], cfg_beats, [60.0, 60.0]) == 0
    assert plan == before


def test_snap_cuts_to_beats(cfg):
    plan = {"shots": [{"source": 0, "start": 0.0, "end": 1.9},
                      {"source": 0, "start": 5.0, "end": 7.0},
                      {"type": "polaroid", "from_shot": 1, "dur": 1.0}]}
    moved = snap_cuts_to_beats(plan, [0.0, 2.0, 4.1, 6.0], cfg, [60.0])
    assert moved == 2
    tl = compute_timeline(plan, cfg)
    assert _cuts(tl)[0] == pytest.approx(2.0, abs=1 / 60)
    assert plan["shots"][0]["end"] == pytest.approx(2.0)
    assert snap_cuts_to_beats(plan, [], cfg, [60.0]) == 0
