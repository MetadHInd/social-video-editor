"""Joining with transitions, collage appearance times and best-window selection."""
from __future__ import annotations

import pytest

from social_video_editor.adapters.ffmpeg.filters import XFADE, join, xfade_name
from social_video_editor.domain.pieces import appearance_times
from social_video_editor.domain.selection import best_window, resolve_ranges
from social_video_editor.domain.transitions import TRANSITIONS, get_transition, transition_duration


def test_join_counts_xfade_and_concat():
    segs = [{"dur": 2.0, "overlap": 0.0, "transition": "cut"},
            {"dur": 1.0, "overlap": 0.0, "transition": "cut"},
            {"dur": 1.5, "overlap": 0.35, "transition": "fade"},
            {"dur": 1.0, "overlap": 0.2, "transition": "zoom"},
            {"dur": 1.0, "overlap": 0.0, "transition": "whip"}]      # xfade without overlap → concat
    flt, v, a = join(segs, 30)
    assert (v, a) == ("[v4]", "[a4]")
    assert flt.count("xfade=") == 2
    assert flt.count("acrossfade=") == 2
    assert flt.count("concat=n=2") == 2
    assert "xfade=transition=fade:duration=0.3500:offset=2.6500" in flt
    assert "xfade=transition=zoomin:duration=0.2000:offset=3.9500" in flt
    for i in range(5):
        assert f"[{i}:v]setpts=PTS-STARTPTS,fps=30,settb=AVTB" in flt


def test_join_single_segment():
    flt, v, a = join([{"dur": 1.0, "overlap": 0.0, "transition": "cut"}], 30)
    assert (v, a) == ("[sv0]", "[sa0]")
    assert "xfade" not in flt and "concat" not in flt


def test_transition_duration(cfg):
    assert transition_duration("cut", cfg) == 0.0
    assert transition_duration("fade", cfg) == 0.35          # own fixed duration
    assert transition_duration("zoom", cfg) == cfg["transitions"]["duration"] == 0.22
    with pytest.raises(SystemExit, match="Unknown transition"):
        get_transition("does_not_exist")
    # every domain transition has a drawing in the FFmpeg adapter, and vice versa
    assert set(XFADE) == set(TRANSITIONS)
    assert all((xfade_name(n) is None) == t.is_cut for n, t in TRANSITIONS.items())


def test_appearance_times_subdivision_4():
    s = {"dur": 2.0, "_beats": [0.3, 0.8, 1.3, 1.8]}
    assert appearance_times(s, 6, 4) == pytest.approx([0.3, 0.425, 0.55, 0.675, 0.8, 0.925])
    # includes the beat at the start if it fits before the first one
    s = {"dur": 2.0, "_beats": [0.6, 1.1, 1.6]}
    assert appearance_times(s, 3, 4) == pytest.approx([0.1, 0.225, 0.35])
    # the shot can set its own subdivision
    assert appearance_times({**s, "subdivision": 1}, 3, 4) == pytest.approx([0.1, 0.6, 1.1])
    assert appearance_times(s, 3, 2) == pytest.approx([0.1, 0.35, 0.6])


def test_appearance_times_without_music():
    s = {"dur": 2.0}
    assert appearance_times(s, 4, 4) == pytest.approx([0.1, 0.35, 0.6, 0.85])   # first 50 %
    # not enough beats for all the photos → even spread
    s = {"dur": 1.0, "_beats": [0.3, 0.8]}
    assert appearance_times(s, 20, 4) == pytest.approx([0.1 + k * 0.5 / 20 for k in range(20)])


def _analysis(dur=10.0, per_sec=4, step=0.1):
    samples = []
    for k in range(int(dur * per_sec) + 1):
        t = k / per_sec
        product = 6.0 <= t <= 7.5                     # sharp, colorful and still object
        action = 3.0 <= t <= 4.0                      # lots of motion
        samples.append({"t": t, "brightness": 0.5, "sharpness": 500.0 if product else 50.0,
                        "color": 40.0 if product else 10.0, "motion": 9.0 if action else 1.0})
    db = [-40.0] * int(dur / step)
    for i in range(int(round(3.0 / step)), int(round(4.0 / step)) + 1):
        db[i] = -10.0                                 # loud sound at 3–4 s
    return {"duration": dur, "samples": samples,
            "audio": {"step": step, "db": db, "median_db": -40.0, "peaks": []}}


def test_best_window_product_and_action():
    an = _analysis()
    assert best_window(an, [2, 9], 1.5, "product") == (6.0, 7.5)
    assert best_window(an, [0, 9], 1.0, "action") == (3.0, 4.0)


def test_best_window_edges():
    an = _analysis()
    assert best_window(an, [-1, 3], 5) == (0.0, 3.0)          # range shorter than the window
    assert best_window(an, [8, 30], 1.0) in {(round(8 + k * 0.25, 2), round(9 + k * 0.25, 2)) for k in range(5)}
    few = {"duration": 10.0, "samples": [{"t": 0, "sharpness": 1, "color": 1, "motion": 0}]}
    assert best_window(few, [2, 8], 1.5) == (2.0, 3.5)        # < 3 samples: from the start


def test_resolve_ranges():
    plan = {"shots": [{"source": 0, "search": {"range": [2, 9], "dur": 1.5}},
                      {"source": 0, "start": 1, "end": 2}]}
    resolve_ranges(plan, [_analysis()])
    assert (plan["shots"][0]["start"], plan["shots"][0]["end"]) == (6.0, 7.5)
    assert plan["shots"][1] == {"source": 0, "start": 1, "end": 2}
