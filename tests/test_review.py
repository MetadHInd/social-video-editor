"""The engine's own review (domain/review.py): what a viewer would notice in the plan or the render,
the remedy the engine applies by itself, and the decisions it leaves for a person."""
from __future__ import annotations

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.domain import review
from social_video_editor.domain.cutaways import READ_SECONDS, Cutaway, resolve, sentence_spans
from social_video_editor.domain.timeline import compute_timeline
from test_director import cfg, plan_for

CFG = {"output": {"fps": 30}, "transitions": {"duration": 0.2}}


def layout(plan, entrance=1.0):
    tl = compute_timeline(plan, CFG)
    placed, _ = resolve(plan, tl, 30, lambda c: entrance)
    minimum = {i: entrance + READ_SECONDS for i in range(len(plan.get("cutaways") or []))}
    return review.Layout(placed, minimum, sentence_spans(plan, tl), tl.total)


def placed(index, kind, spec, start, end, sentences=(1, 1), overlay=False):
    return Cutaway(index, kind, spec, start, end, overlay, sentences)


def fake(cutaways, items, minimum=1.8, total=30.0):
    return {"cutaways": cutaways}, review.Layout(items, {i: minimum for i in range(len(cutaways))}, {}, total)


def codes(found):
    return [x.code for x in found]


def test_what_a_graphic_asks_to_be_read():
    spec = {"template": "number", "value": "20", "unit": "apps", "label": "Open", "sub": "all day", "color": "pink"}
    assert review.reading(spec) == "Open 20 apps all day"
    assert review.read_seconds(spec, 10) == pytest.approx(2.0)
    assert review.reading({"template": "list", "items": ["A", "B"]}) == "A B"
    assert review.settings({"review": {"rounds": 2}})["rounds"] == 2 and review.settings(None)["read_cps"] == 15


def test_short_cutaways_proposed_by_the_engine_go_and_written_ones_are_decisions():
    cuts = [{"type": "graphic", "template": "title", "text": "Hi", "at_sentence": 1, "_auto": "card"},
            {"type": "graphic", "template": "title", "text": "Hi", "at_sentence": 3}]
    plan, lay = fake(cuts, [placed(0, "graphic", cuts[0], 2.0, 2.9), placed(1, "graphic", cuts[1], 8.0, 8.9, (3, 3))])
    found = review.review_plan(plan, lay, {})
    assert codes(found) == ["cutaway_too_short", "cutaway_too_short"]
    assert found[0].fix == {"drop": True} and found[1].fix is None
    assert found[1].options == ["show it until sentence 4", "remove it"]
    done, pending = review.apply(plan, found, set())
    assert [c["text"] for c in plan["cutaways"]] == ["Hi"] and plan["cutaways"][0]["at_sentence"] == 3
    assert len(done) == 1 and "removed cutaway 0" in done[0] and [x.code for x in pending] == ["cutaway_too_short"]


def test_a_cutaway_that_does_not_fit_is_reported():
    cuts = [{"type": "broll", "query": "q", "at_sentence": 9, "_auto": "director"},
            {"type": "photo", "file": "x.jpg", "at_sentence": 9}]
    plan, lay = fake(cuts, [])
    found = review.review_plan(plan, lay, {})
    assert codes(found) == ["cutaway_left_out"] * 2
    assert found[0].fix == {"drop": True} and found[1].options == ["anchor it to another sentence", "remove it"]


def test_a_card_too_long_to_read_is_shortened():
    text = "Your laptop fan spins faster when the room is hot"
    cuts = [{"type": "graphic", "template": "title", "text": text, "icon": "app", "at_sentence": 1, "_auto": "director"},
            {"type": "graphic", "template": "headline", "text": text, "at_sentence": 2}]
    plan, lay = fake(cuts, [placed(0, "graphic", cuts[0], 2.0, 4.0), placed(1, "graphic", cuts[1], 6.0, 8.0, (2, 2))])
    found = review.review_plan(plan, lay, {})
    assert codes(found) == ["text_too_long_to_read"] * 2
    short = found[0].fix["set"]["text"]
    assert short.endswith("…") and text.startswith(short[:-1]) and len(short) <= 2.0 * 15 + 1
    assert found[1].fix is None and found[1].options[0] == "shorten its text"
    review.apply(plan, found, set())
    assert plan["cutaways"][0]["text"] == short and plan["cutaways"][0]["_fixed"] == ["text_too_long_to_read"]
    assert plan["cutaways"][1]["text"] == text                         # written by hand: not touched


@pytest.mark.parametrize("spec,chars,expected", [
    ({"template": "list", "items": "One thing; Two things; Three things; Four things"}, 22,
     {"items": "One thing; Two things"}),
    ({"template": "number", "value": "20", "label": "Apps", "sub": "open all day long"}, 5, {"sub": ""}),
    ({"template": "number", "value": "20", "label": "Apps"}, 3, {"label": ""}),
    ({"template": "title", "text": "Two words"}, 3, None),             # cannot say less than two words
    ({"template": "bars", "data": "A:1"}, 1, None),
])
def test_shorten(spec, chars, expected):
    assert review.shorten(spec, chars) == expected


def test_a_graphic_held_too_long_lets_the_presenter_back():
    cuts = [{"type": "graphic", "template": "bars", "data": "A:1, B:2", "at_sentence": 2},
            {"type": "graphic", "template": "list", "items": "A; B", "at_sentence": 4, "until_sentence": 6}]
    plan, lay = fake(cuts, [placed(0, "graphic", cuts[0], 3.0, 9.5, (2, 2)), placed(1, "graphic", cuts[1], 12.0, 21.0, (4, 6))],
                     minimum=3.5)
    found = review.review_plan(plan, lay, {"review": {"graphic_max": 6}})
    assert codes(found) == ["cutaway_too_long"] * 2
    assert found[0].fix == {"set": {"seconds": 5.0}}                  # 6.5 s → 5 s, then 1.5 s of face
    assert found[1].fix is None                                       # several sentences on purpose: a decision
    assert review.review_plan(plan, lay, {"review": {"graphic_max": 0}}) == []


def test_unmatched_sentences_are_decisions():
    plan = {"sentences": [{"n": 0, "text": "Hello."}, {"n": 1, "text": "Not said at all.", "missing": True}]}
    (x,) = review.review_plan(plan, review.Layout([], {}, {}, 10.0), {})
    assert x.code == "sentence_unmatched" and "Not said at all." in x.message and x.fix is None
    assert x.options[0] == "record it again and run sve make"


def test_the_presenter_alone_too_long_gets_footage_from_the_director():
    plan = plan_for()
    plan["cutaways"] = []
    lay = layout(plan)
    (x,) = review.camera_stretches(plan, lay, cfg())
    assert x.code == "camera_too_long" and x.t == 0.0 and x.fix["add"]
    assert all(c["_auto"] == "director" for c in x.fix["add"])
    done, pending = review.apply(plan, [x], set())
    assert done and not pending and len(plan["cutaways"]) == len(x.fix["add"])
    # the director placed what it could: the stretches left are within its limits
    assert review.camera_stretches(plan, layout(plan), cfg()) == []
    # without the director the engine does not judge the camera time
    assert review.camera_stretches(plan, lay, {}) == []


def test_a_remedy_is_tried_once_then_it_is_a_decision():
    cuts = [{"type": "graphic", "template": "title", "text": "Hi", "at_sentence": 1, "_auto": "card"}]
    plan, lay = fake(cuts, [placed(0, "graphic", cuts[0], 2.0, 2.9)])
    tried = set()
    first = review.review_plan(plan, lay, {})
    review.apply({"cutaways": list(cuts)}, first, tried)
    again = review.review_plan(plan, lay, {})
    done, pending = review.apply(plan, again, tried)
    assert done == [] and codes(pending) == ["cutaway_too_short"] and len(plan["cutaways"]) == 1


def test_what_the_check_found_on_screen():
    cuts = [{"type": "broll", "query": "q", "file": "footage/a.mp4", "at_sentence": 1, "_auto": "director"},
            {"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 3}]
    items = [placed(0, "broll", {"query": "q"}, 2.0, 6.0), placed(1, "graphic", {"template": "bars"}, 9.0, 13.0, (3, 3))]
    plan, lay = fake(cuts, items, minimum=1.4)
    check = {"errors": ["black frames 0.00–0.20 s"],
             "metrics": {"video": {"freeze": [[4.5, 6.0], [10.0, 11.5], [20.0, 22.0]]},
                         "text_boxes": {"issues": [{"text": "Some caption", "start": 15.0, "end": 17.0,
                                                    "issues": ["below the safe zone"]}]}}}
    found = review.review_render(plan, lay, check)
    assert codes(found) == ["frozen_picture", "frozen_picture", "text_outside_safe_zone", "frozen_picture", "render_error"]
    assert found[0].fix == {"set": {"seconds": 2.5}}                  # the clip ends where it stops moving
    assert found[1].fix is None and "bars" in found[1].message
    assert "the presenter" in found[3].message and found[3].fix is None
    early = review.review_render(plan, lay, {"metrics": {"video": {"freeze": [[2.5, 6.0]]}}})
    assert early[0].fix == {"drop": True}                             # stops at once: an engine clip goes


def test_decision_lines_and_spanish_keys():
    lines = review.decision_lines([{"code": "x", "message": "Something", "options": ["a", "b"]}])
    assert lines == ["DECISIONS (1): the engine could not settle these", "  1. Something", "     → a · b"]
    assert review.decision_lines([]) == []
    cfg_es = legacy_es.config({"revision": {"vueltas": 2, "lectura_cps": 12, "grafico_max": 5,
                                                      "holgura_camara": 1}})
    assert cfg_es["review"] == {"rounds": 2, "read_cps": 12, "graphic_max": 5, "camera_slack": 1}


def test_a_short_engine_cutaway_starts_a_word_earlier_instead_of_going():
    plan = plan_for()
    s0 = plan["sentences"][0]
    late = next(w for w in s0["words"] if s0["end"] - w[1] < 1.6)        # 1.5 s before the sentence ends
    plan["cutaways"] = [{"type": "graphic", "template": "title", "text": "Your phone", "at_sentence": 0,
                         "from_word": [s0["source"], late[1]], "_auto": "director"},
                        {"type": "graphic", "template": "bars", "data": "A:1, B:2", "at_sentence": 1}]   # right after it
    lay = layout(plan, entrance=1.4)                                    # needs 2.2 s
    (x,) = [i for i in review.review_plan(plan, lay, {}) if i.code == "cutaway_too_short"]
    source, t = x.fix["set"]["from_word"]
    assert source == s0["source"] and t < late[1] and t in [w[1] for w in s0["words"]]     # at a word said earlier
    review.apply(plan, [x], set())
    assert [i.code for i in review.review_plan(plan, layout(plan, entrance=1.4), {})] == []
    # at the very start of the video there is no room: the face is seen first
    plan["cutaways"][0]["from_word"] = [s0["source"], s0["words"][4][1]]
    lay = layout(plan, entrance=6.0)
    (x,) = [i for i in review.review_plan(plan, lay, {}) if i.code == "cutaway_too_short"]
    assert x.fix == {"drop": True}


def test_words_the_voice_says_are_read_along():
    spec = {"template": "list", "items": "Baja el brillo; Cierra el wifi"}
    alone = review.read_seconds(spec, 15)
    along = review.read_seconds(spec, 15, review.heard(
        {"sentences": [{"n": 2, "words": [[w, 0, 0] for w in "baja el brillo y cierra el wifi".split()]}]},
        (2, 2)))
    assert alone == pytest.approx(len(review.reading(spec)) / 15)
    assert along == pytest.approx(alone * review.HEARD_SHARE, abs=0.05)
    assert review.heard({}, None) == set()


def test_what_was_removed_is_not_proposed_again_and_tried_remedies_offer_options():
    proposal = {"type": "broll", "query": "q", "at_sentence": 0, "_auto": "director"}
    tried = {f"short:{review._where(proposal)}"}
    x = review.Issue("camera_too_long", "alone", 0.0, None, {"add": [dict(proposal)]}, key="camera:0")
    plan = {"cutaways": []}
    done, pending = review.apply(plan, [x], tried)
    assert done == [] and plan["cutaways"] == [] and [p.code for p in pending] == ["camera_too_long"]
    y = review.Issue("cutaway_too_short", "short", 1.0, 0, {"drop": True}, key="k")
    _, pending = review.apply({"cutaways": [proposal]}, [y], {"k"})
    assert pending[0].options == ["leave it", "remove it"]
