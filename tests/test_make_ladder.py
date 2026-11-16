"""`sve make` goes further: a remedy ladder (another clip → another drawing → removed), faces cut by the
frame, plan texts moved into the safe zone or kept longer, the music lowered under the voice, one version
folder per run, and the same review for every recipe."""
from __future__ import annotations

import json

import pytest

from conftest import requires_ffmpeg
from social_video_editor.domain import review
from social_video_editor.domain.cutaways import Cutaway
from software_encoder import software_encoder  # noqa: F401 (autouse: software x264)
from test_director import plan_for

CFG = {"language": "en", "output": {"width": 1080, "height": 1920, "fps": 30},
       "texts": {"styles": {"cta": {"y": 0.80}}}, "music": {"relative_to_voice": True, "under_voice_db": 10}}


def placed(index, kind, spec, start, end, sentences=(1, 1)):
    return Cutaway(index, kind, spec, start, end, False, sentences)


def rounds(plan, found_again, n, adjust=None):
    """Applies the findings `found_again()` gives, n times (one per round) → what was done each time."""
    tried, out = set(), []
    for _ in range(n):
        done, pending = review.apply(plan, found_again(), tried, adjust)
        out.append((done, [x.code for x in pending]))
    return out


def test_a_frozen_clip_gets_another_clip_then_a_drawing_then_goes():
    plan = plan_for()
    cue = {"type": "broll", "query": "a phone", "file": "footage/stock_1_a.mp4", "at_sentence": 1, "until_sentence": 1,
           "_auto": "director", "fallback": {"template": "title", "text": "Your phone", "icon": "phone"}}
    plan["cutaways"] = [cue]
    lay = review.Layout([placed(0, "broll", cue, 2.0, 6.0)], {0: 1.4}, {}, 30.0)
    check = {"metrics": {"video": {"freeze": [[4.5, 6.0]]}}}

    def found():
        c = plan["cutaways"][0]
        kind = "broll" if c["type"] == "broll" else "graphic"
        if kind == "broll" and not c.get("file"):          # what fetch_broll does: the next candidate
            c["file"] = f"footage/stock_{len(c['_rejected']) + 1}_a.mp4"
        return review.review_render(plan, review.Layout([placed(0, kind, c, 2.0, 6.0)], {0: 1.4}, {}, 30.0),
                                    check, {"review": {"clip_tries": 2}})

    first = review.review_render(plan, lay, check, {})
    assert first[0].fix == {"another_clip": "footage/stock_1_a.mp4"}
    steps = rounds(plan, found, 4)
    assert "gets another clip (the next candidate, not footage/stock_1_a.mp4)" in steps[0][0][0]
    assert "not footage/stock_2_a.mp4" in steps[1][0][0]                # two clips tried (review.clip_tries)
    assert "seconds=2.5" in steps[2][0][0]                              # then it ends where it stops moving
    assert "drawn as a title instead" in steps[3][0][0]                 # then the drawing it carried
    card = plan["cutaways"][0]
    assert card["type"] == "graphic" and card["text"] == "Your phone" and card["_auto"] == "director"
    assert card["at_sentence"] == 1 and "file" not in card and card["_was"] == ["broll"]
    # without the settings (or with review.ladder off) the remedies are those of before
    old = review.review_render({"cutaways": [dict(cue)]}, lay, check)
    assert old[0].fix == {"set": {"seconds": 2.5}} and old[0].more == []
    off = review.review_render({"cutaways": [dict(cue)]}, lay, check, {"review": {"ladder": False}})
    assert off[0].fix == {"set": {"seconds": 2.5}} and off[0].more == []


def test_a_hand_written_clip_gets_another_clip_and_then_is_a_decision():
    cue = {"type": "broll", "query": "a phone", "file": "footage/stock_1_a.mp4", "at_sentence": 1}
    plan = {"cutaways": [cue]}
    lay = review.Layout([placed(0, "broll", cue, 2.0, 6.0)], {0: 3.0}, {}, 30.0)
    (x,) = review.review_render(plan, lay, {"metrics": {"video": {"freeze": [[2.5, 6.0]]}}}, {"review": {"clip_tries": 1}})
    assert x.fix == {"another_clip": "footage/stock_1_a.mp4"} and x.more == []
    review.apply(plan, [x], set())
    plan["cutaways"][0]["file"] = "footage/stock_2_a.mp4"
    (y,) = review.review_render(plan, lay, {"metrics": {"video": {"freeze": [[2.5, 6.0]]}}}, {"review": {"clip_tries": 1}})
    assert y.fix is None and "choose another clip (sve broll --force)" in y.options


def test_a_clip_that_matches_its_search_poorly_is_replaced_when_asked():
    cue = {"type": "broll", "query": "a phone", "file": "footage/stock_1_a.mp4", "at_sentence": 1,
           "_auto": "director", "_match": 0.271}
    plan = {"cutaways": [cue]}
    lay = review.Layout([placed(0, "broll", cue, 2.0, 6.0)], {0: 1.4}, {}, 30.0)
    assert review.review_plan(plan, lay, {}) == []                           # review.clip_match is off by default
    (x,) = review.review_plan(plan, lay, {"review": {"clip_match": 0.29}})
    assert x.code == "broll_low_relevance" and x.fix == {"another_clip": "footage/stock_1_a.mp4"}
    assert x.more == [{"drop": True}]                                        # no sentence to draw: then removed


def test_a_card_too_long_is_shortened_then_drawn_another_way_then_removed():
    plan = plan_for()
    s1 = plan["sentences"][2]                                                # something doing something: a scene too
    text = "Your laptop fan spins faster when the room is hot and nobody notices"
    cue = {"type": "graphic", "template": "title", "text": text, "at_sentence": s1["n"], "until_sentence": s1["n"],
           "_auto": "director"}
    plan["cutaways"] = [cue]
    lay = review.Layout([placed(0, "graphic", cue, 2.0, 3.2, (s1["n"], s1["n"]))], {0: 1.0}, {}, 30.0)
    (x,) = review.review_plan(plan, lay, {"language": "en"})
    assert "set" in x.fix and [next(iter(m)) for m in x.more] == ["card", "drop"]
    assert x.more[0]["card"]["template"] != "title"                          # another kind of drawing
    tried = set()
    review.apply(plan, [x], tried)
    review.apply(plan, [x], tried)
    assert plan["cutaways"][0]["template"] == x.more[0]["card"]["template"] and plan["cutaways"][0]["_was"] == ["title"]
    done, _ = review.apply(plan, [x], tried)
    assert plan["cutaways"] == [] and "removed" in done[0]


def test_a_face_cut_by_the_frame_is_framed_again_then_plain():
    cfg = {"output": {"width": 1080, "height": 1920}, "framing": {"mode": "crop", "zoom_margin": 1.6}}
    info = {"width": 1920, "height": 1080}
    face = {"x": 0.5, "y": 0.12, "size": 0.2, "eyes": 0.1, "ratio": 0.9}          # high in the picture
    shot = {"source": 0, "start": 1.0, "end": 4.0, "zoom": 1.5, "focus_x": 0.5, "focus_y": 0.5,
            "effects": [{"type": "push_in", "amount": 0.04, "auto": True}]}
    plan = {"shots": [shot]}
    (x,) = review.faces_cut(plan, [info], [{"face": face}], cfg)
    assert x.code == "face_out_of_frame" and x.fix["shot"] == 0 and "focus_y" in x.fix["set"]
    assert x.more == [{"shot": 0, "plain": True}]
    tried = set()
    review.apply(plan, [x], tried)
    again = review.faces_cut(plan, [info], [{"face": face}], cfg)
    if again:                                                              # still cut at that zoom: plain framing
        review.apply(plan, again, tried)
        assert "zoom" not in shot and shot["effects"] == [] and shot["_plain"]
    assert review.faces_cut(plan, [info], [{"face": face}], cfg) == []
    assert review.faces_cut(plan, [info], [{"face": face}], {**cfg, "review": {"faces": False}}) == []
    centered = {"shots": [{"source": 0, "start": 0, "end": 2, "zoom": 1.2}]}
    assert review.faces_cut(centered, [info], [{"face": {**face, "y": 0.45, "eyes": 0.42}}], cfg) == []


def test_plan_texts_move_into_the_safe_zone_and_stay_long_enough_to_read():
    plan = {"shots": [{"source": 0, "start": 0, "end": 1}] * 3,
            "texts": [{"text": "Follow for more", "start": 5.0, "end": 8.0, "style": "cta"},
                      {"text": "A rather long card that nobody can read in a blink", "shot": 0}]}
    lay = review.Layout([], {}, {}, 10.0, texts=[(0, 5.0, 8.0), (1, 0.1, 0.9)])
    check = {"metrics": {"text_boxes": {"issues": [
        {"text": "FOLLOW FOR MORE", "start": 5.0, "end": 8.0, "issues": ["bottom 0.85·H > 0.78"]},
        {"text": "Subtitle", "kind": "subtitle", "start": 1.0, "end": 2.0, "issues": ["bottom 0.9·H > 0.78"]}]},
        "music_vs_voice": {"speech_lu": 3.5, "worst_t": 4.0}, "thresholds": {"music_min_under_voice_lu": 6}},
        "errors": ["integrated loudness -16.2 LUFS, target -14.0 ± 1.0 LU"]}
    found = review.review_render(plan, lay, check, CFG)
    assert [x.code for x in found] == ["text_outside_safe_zone", "music_covers_voice", "text_outside_safe_zone",
                                       "loudness_off"]
    assert found[2].fix == {"text": 0, "set": {"y": 0.72}} and found[0].fix is None      # a subtitle: a decision
    assert found[1].fix == {"settings": {"music": {"under_voice_db": 13.5}}}
    adjust: dict = {}
    done, pending = review.apply(plan, found, set(), adjust)
    assert plan["texts"][0]["y"] == 0.72 and adjust == {"music": {"under_voice_db": 13.5}}
    assert [x.code for x in pending] == ["text_outside_safe_zone", "loudness_off"]
    _, pending = review.apply(plan, found[1:2], set())                      # no place for settings: a decision
    assert [x.code for x in pending] == ["music_covers_voice"]
    (short,) = review.texts_too_short(plan, lay, {})
    assert short.fix == {"text": 1, "set": {"until_shot": 1}}
    assert review.texts_too_short(plan, lay, {"review": {"ladder": False}}) == []


# ── the whole command ────────────────────────────────────────────────────────
@pytest.mark.integration
@requires_ffmpeg
def test_make_fixes_what_the_render_shows_and_keeps_one_folder(workspace, monkeypatch, capsys):
    from social_video_editor import cli
    from test_cutaways_render import explainer_project

    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, "How does a message travel?\nEvery time you send one, it splits again.\n"
                                     "And they travel apart.\n", {"check": {"safe_bottom": 0.78}})
    cli.main(["plan", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    plan["texts"] = [{"text": "Follow", "start": 1.0, "end": 4.0, "style": "cta", "y": 0.95}]
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    capsys.readouterr()
    cli.main(["make", "demo"])
    out = capsys.readouterr().out
    assert "text_outside_safe_zone: text 0 «Follow» y=" in out and "2 render(s)" in out
    folders = sorted(p.name for p in d.glob("v*") if p.is_dir())
    assert folders == ["v1Borrador_2"]                                   # the first attempt is gone
    saved = json.loads((d / "review.json").read_text(encoding="utf-8"))
    assert saved["version"] == "v1Borrador_2" and saved["renders"] == 2
    assert json.loads((d / "plan.json").read_text(encoding="utf-8"))["texts"][0]["y"] < 0.9


@pytest.mark.integration
@requires_ffmpeg
def test_another_clip_is_the_next_candidate_of_the_same_cue(workspace, monkeypatch):
    from social_video_editor import cli
    from test_footage import FakeSource, fake_services, stock_clip
    from test_cutaways_render import explainer_project

    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, 'How does a message travel?\n[broll query="a phone"]\nAnd they travel apart.\n',
                          {"broll": {"sources": ["stock"], "credit": False}})
    stock = FakeSource("stock", "stock", [stock_clip(id="1", title="one"), stock_clip(id="2", title="two")], "0x1EC81E")
    fake_services(monkeypatch, {"stock": stock})
    cli.main(["plan", "demo"])
    cli.main(["broll", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    (cue,) = [c for c in plan["cutaways"] if c["type"] == "broll"]
    assert cue["file"] == "footage/stock_1_one.mp4"
    review.apply(plan, [review.Issue("frozen_picture", "stops", 1.0, plan["cutaways"].index(cue),
                                     {"another_clip": cue["file"]}, key="freeze:x")], set())
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    cli.main(["broll", "demo"])
    (cue,) = [c for c in json.loads((d / "plan.json").read_text(encoding="utf-8"))["cutaways"] if c["type"] == "broll"]
    assert cue["file"] == "footage/stock_2_two.mp4" and cue["_rejected"] == ["footage/stock_1_one.mp4"]


@pytest.mark.integration
@requires_ffmpeg
def test_make_reviews_a_talk_too(workspace, monkeypatch, capsys):
    import yaml

    from social_video_editor import cli
    from test_cutaways_render import explainer_project

    monkeypatch.chdir(workspace.parent)
    d = explainer_project(workspace, "How does a message travel?\n")
    data = yaml.safe_load((d / "project.yaml").read_text(encoding="utf-8"))
    data["recipe"] = "talk"
    (d / "project.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    cli.main(["plan", "demo"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    plan["texts"] = [{"text": "Welcome", "start": 0.5, "end": 3.0, "style": "simple", "y": 0.97}]
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    capsys.readouterr()
    cli.main(["make", "demo"])
    out = capsys.readouterr().out
    assert "text_outside_safe_zone: text 0 «Welcome» y=" in out
    assert len([p for p in d.glob("v*") if p.is_dir()]) == 1
    assert json.loads((d / "review.json").read_text(encoding="utf-8"))["renders"] == 2
