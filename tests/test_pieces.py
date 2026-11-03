"""The Composite of pieces: durations, walk, groups, polymorphic behavior and render order."""
from __future__ import annotations

import pytest

from social_video_editor.domain.models import PlanError
from social_video_editor.domain.pieces import (CollageShot, Group, PolaroidShot, Sequence, VideoShot,
                                               make_shot)
from social_video_editor.domain.timeline import compute_timeline


def _plan():
    return {"shots": [
        {"source": 0, "start": 0.0, "end": 1.0},                                         # 0
        {"source": 0, "start": 5.0, "end": 6.0, "speed": 2.0, "group": "star"},           # 1 fast
        {"source": 0, "start": 6.0, "end": 6.6, "speed": 0.5, "group": "star"},           # 2 slow
        {"type": "polaroid", "from_shot": 2, "dur": 0.9, "group": "star"},               # 3
        {"source": 1, "start": 2.0, "end": 3.5, "freeze": 0.5},                          # 4
        {"type": "collage", "from_shots": [0, 1, 4], "beats": 3},                        # 5 (0.6 s per beat)
    ]}


def test_tree_shape_and_walk():
    seq = Sequence.from_plan(_plan())
    kinds = [type(p).__name__ for p in seq.walk()]
    assert kinds == ["Sequence", "VideoShot", "Group", "VideoShot", "VideoShot", "PolaroidShot",
                     "VideoShot", "CollageShot"]
    group = seq.children()[1]
    assert isinstance(group, Group) and group.label == "star" and len(group.children()) == 3
    assert [s.index for s in seq.shots()] == list(range(6))
    assert [s.index for s in group.shots()] == [1, 2, 3]
    assert all(s.is_leaf for s in seq.shots()) and not group.is_leaf
    # a group label repeated after another piece starts a new group
    seq2 = Sequence.from_plan({"shots": [{"source": 0, "start": 0, "end": 1, "group": "a"},
                                         {"source": 0, "start": 1, "end": 2},
                                         {"source": 0, "start": 2, "end": 3, "group": "a"}]})
    assert [type(c).__name__ for c in seq2.children()] == ["Group", "VideoShot", "Group"]


def test_composite_durations():
    seq = Sequence.from_plan(_plan())
    leaf_durs = [s.duration() for s in seq.shots()]
    assert leaf_durs == pytest.approx([1.0, 0.5, 1.2, 0.9, 2.0, 1.8])
    group = seq.children()[1]
    assert group.duration() == pytest.approx(0.5 + 1.2 + 0.9)
    assert seq.duration() == pytest.approx(sum(leaf_durs))
    # the timeline uses the same durations and applies the transition overlaps on top
    tl = compute_timeline(seq, {"output": {"fps": 30}, "transitions": {"duration": 0.2}})
    assert [c.dur for c in tl] == pytest.approx(leaf_durs)
    assert tl.total == pytest.approx(seq.duration())                       # all cuts: no overlap


def test_polymorphic_behavior():
    seq = Sequence.from_plan(_plan())
    v, pol, col = seq.shots()[0], seq.shots()[3], seq.shots()[5]
    assert isinstance(v, VideoShot) and isinstance(pol, PolaroidShot) and isinstance(col, CollageShot)
    assert v.source_window() == (0, 0.0, 1.0, 1.0) and pol.source_window() is None
    assert v.summary() == {"type": "video", "source": 0, "from": "00:00.00", "to": "00:01.00"}
    assert pol.summary() == {"type": "polaroid", "source": None, "from": None, "to": None}
    assert v.describe().startswith("source 0 00:00.00→00:01.00") and col.describe().startswith("collage")
    assert pol.sound_events(2.0, 0.9, -12, {}) == [("shutter", 2.0, -9)]
    pops = col.sound_events(5.0, 1.8, -12, {"collage": {"subdivision": 2}})
    assert [n for n, _, _ in pops] == ["pop"] * 3 and all(5.0 < t < 5.0 + 1.8 for _, t, _ in pops)
    assert v.sound_events(0, 1, -12, {}) == []

    class Recorder:
        def visit_video(self, s): return ("video", s.index)
        def visit_polaroid(self, s): return ("polaroid", s.index)
        def visit_collage(self, s): return ("collage", s.index)

    assert [s.accept(Recorder()) for s in seq.shots()] == [
        ("video", 0), ("video", 1), ("video", 2), ("polaroid", 3), ("video", 4), ("collage", 5)]


def test_dependencies_and_render_order():
    seq = Sequence.from_plan(_plan())
    assert seq.shots()[3].dependencies() == {2}
    assert seq.shots()[5].dependencies() == {0, 1, 4}                     # background_from defaults to the last
    assert seq.children()[1].dependencies() == {2}                         # a composite unions its children
    assert seq.render_order() == [0, 1, 2, 4, 3, 5]                        # video shots first, then drawn ones


def test_drawn_shot_from_another_drawn_shot_is_resolved():
    plan = {"shots": [
        {"source": 0, "start": 0, "end": 1},
        {"type": "polaroid", "from_shot": 2, "beats": 1},                   # depends on the collage after it
        {"type": "collage", "from_shots": [0, 0], "background_from": 0, "beats": 2},
        {"type": "polaroid", "from_shot": 1, "beats": 1},                   # polaroid of a polaroid
    ]}
    order = Sequence.from_plan(plan).render_order()
    assert order == [0, 2, 1, 3]
    assert order.index(2) < order.index(1) < order.index(3)
    assert Sequence.from_plan(plan).render_waves() == [[0], [2], [1], [3]]


def test_render_waves_group_shots_by_dependency_depth():
    seq = Sequence.from_plan(_plan())
    waves = seq.render_waves()
    assert waves == [[0, 1, 2, 4], [3, 5]]                                 # every video shot, then the drawn ones
    assert [i for w in waves for i in w] == seq.render_order()
    for k, wave in enumerate(waves):                                        # a wave only needs earlier waves
        earlier = {i for w in waves[:k] for i in w}
        assert all(seq.shots()[i].dependencies() <= earlier for i in wave)
    assert Sequence.from_plan({"shots": []}).render_waves() == []


@pytest.mark.parametrize("shots,message", [
    ([{"source": 0, "start": 0, "end": 1}, {"type": "polaroid", "from_shot": 7}], "references shot 7"),
    ([{"type": "polaroid", "from_shot": 0}], "Circular reference between shots: 0 → 0"),
    ([{"type": "polaroid", "from_shot": 1}, {"type": "collage", "from_shots": [0]}],
     "Circular reference between shots: 0 → 1 → 0"),
    ([{"type": "polaroid"}], "needs 'from_shot'"),
    ([{"type": "collage", "from_shots": []}], "non-empty 'from_shots'"),
])
def test_bad_references_are_clear_errors(shots, message):
    with pytest.raises(PlanError, match=message):
        Sequence.from_plan({"shots": shots}).render_order()
    with pytest.raises(PlanError, match=message):
        Sequence.from_plan({"shots": shots}).render_waves()


def test_unknown_shot_type():
    with pytest.raises(PlanError, match="Unknown shot type 'gif'"):
        make_shot({"type": "gif"}, 4)
