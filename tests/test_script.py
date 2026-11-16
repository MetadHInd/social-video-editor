"""Explainer plan: the script format, matching its sentences to the recording, the plan built from
them, cutaways placed on the timeline and the framing of the camera shots (all pure domain logic)."""
from __future__ import annotations

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.application import report
from social_video_editor.domain import script_align as sa
from social_video_editor.domain import pip
from social_video_editor.domain.cutaways import output_time, resolve, sentence_spans, soften, wants_pip
from social_video_editor.domain.explainer import build_plan, face_focus, frame_shots, refocus_faces, subtitle_analyses
from social_video_editor.domain.graphics import TEMPLATES
from social_video_editor.domain.models import PlanError
from social_video_editor.domain.pauses import shorten_plan_pauses
from social_video_editor.domain.script import parse_script, split_sentences, tokens
from social_video_editor.domain.support import propose, sentence_notes
from social_video_editor.domain.timeline import compute_timeline

SCRIPT = """# a comment
How does a message travel? It goes further than expected.
[bars title="Where messages travel" data="Under the sea:95, By satellite:5" unit="%"]
Every time you send one, the phone cuts it into packets.
[camera]
And they travel apart.
"""


def said(*parts, source_gap: float = 0.0) -> list[dict]:
    """Fake transcription: strings are spoken word by word (0.3 s a word, 0.05 s apart), numbers are pauses."""
    out, t = [], source_gap
    for part in parts:
        if isinstance(part, (int, float)):
            t += part
            continue
        for w in part.split():
            out.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2), "prob": 1})
            t += 0.35
    return out


def match(sentences, *recording, **settings):
    return sa.align(sentences, sa.transcript([{"words": list(recording[0])}] if recording else []), {"script": settings})


# ── script format ────────────────────────────────────────────────────────────
def test_tokens_drop_case_accents_and_punctuation():
    assert tokens("¿Cómo VIAJA un mensaje? ¡Así!") == ["como", "viaja", "un", "mensaje", "asi"]
    assert tokens("1.000 km, 2,5 %") == ["1000", "km", "25"]


def test_sentences_keep_their_sign_and_decimals_do_not_split():
    assert split_sentences("Is it true? No. It charges 2.5 times faster…  Really!") == [
        "Is it true?", "No.", "It charges 2.5 times faster…", "Really!"]
    assert split_sentences('He said "stop." Then left') == ['He said "stop."', "Then left"]
    assert split_sentences("...") == []


def test_a_cue_holds_until_the_next_cue_and_camera_is_the_default():
    script = parse_script(SCRIPT, templates=set(TEMPLATES))
    assert [s.text for s in script.sentences] == [
        "How does a message travel?", "It goes further than expected.",
        "Every time you send one, the phone cuts it into packets.", "And they travel apart."]
    assert [s.cue for s in script.sentences] == [None, None, 0, None]
    assert [(c.kind, c.line) for c in script.cues] == [("graphic", 3), ("camera", 5)]
    assert script.cues[0].spec == {"template": "bars", "title": "Where messages travel",
                                   "data": "Under the sea:95, By satellite:5", "unit": "%"}
    assert script.cue_sentences(0) == [2]
    assert [s.line for s in script.sentences] == [2, 2, 4, 6]


def test_cues_in_spanish_and_the_other_kinds():
    text = """Uno.
[barras datos="A:1, B:2" segundos=4]
Dos. Tres.
[foto archivo="fotos/a.jpg"]
Cuatro.
[toma consulta="celular en la mano"]
Cinco.
[camara]
Seis.
"""
    script = parse_script(text, legacy_es.cue, set(TEMPLATES))
    assert [(c.kind, c.seconds) for c in script.cues] == [("graphic", 4.0), ("photo", None), ("broll", None), ("camera", None)]
    assert script.cues[0].spec == {"template": "bars", "data": "A:1, B:2"}
    assert script.cues[1].spec == {"file": "fotos/a.jpg"} and script.cues[2].spec == {"query": "celular en la mano"}
    assert [s.cue for s in script.sentences] == [None, 0, 0, 1, 2, None]


@pytest.mark.parametrize("text, message", [
    ("One.\n[nonsense a=1]\nTwo.", "line 2: unknown cue 'nonsense'"),
    ("One.\n[bars data=\"A:1\"]\n[camera]\nTwo.", "line 2: the cue has no sentence after it"),
    ("One.\n[bars data=\"A:1\"]", "line 2: the cue has no sentence after it"),
    ("One.\n[bars data=\"A:1\" seconds=soon]\nTwo.", "line 2: seconds must be a number"),
    ("One.\n[photo]\nTwo.", "line 2: a photo cue needs file"),
    ("One.\n[bars data]\nTwo.", "line 2: Graphic cue"),
    ("# nothing\n\n", "no sentences"),
])
def test_script_errors_say_the_line(text, message):
    with pytest.raises(PlanError) as e:
        parse_script(text, templates=set(TEMPLATES))
    assert message in str(e.value)


# ── matching the script to the recording ─────────────────────────────────────
def test_exact_match_with_air_before_and_after():
    rec = said(1.0, "How does a message travel?", 1.0, "It goes further than expected.")
    a, b = match(["How does a message travel?", "It goes further than expected."], rec)
    assert a["score"] == b["score"] == 1.0 and a["takes"] == 1
    assert a["start"] == pytest.approx(1.0 - 0.08) and a["end"] == pytest.approx(rec[4]["end"] + 0.12)
    assert b["start"] == pytest.approx(rec[5]["start"] - 0.08)


def test_misheard_words_still_match():
    rec = said("How dose a mesage travl", 1.0, "something else entirely")
    (m,) = match(["How does a message travel?"], rec)
    assert m["score"] == 0.8                               # "dose" is not "does"; the rest look alike
    assert m["start"] == 0.0 and m["end"] == pytest.approx(rec[4]["end"] + 0.12)


def test_a_word_swapped_at_the_edge_is_kept():
    rec = said("before.", 1.0, "Your phone cuts it into packets", 1.0, "after")
    (m,) = match(["The phone cuts it into packets."], rec)
    assert m["start"] == pytest.approx(rec[1]["start"] - 0.08)      # "Your" said instead of "The"
    rec = said("It was true.", "Phone cuts it into packets")
    (m,) = match(["The phone cuts it into packets."], rec)
    assert m["start"] == pytest.approx(rec[3]["start"] - 0.05, abs=0.03)   # never into the previous sentence


def test_a_sentence_said_three_times_uses_the_last_take():
    rec = said("And they travel apart", 1.0, "And they travel apart", 1.0, "And they travel apart.")
    (m,) = match(["And they travel apart."], rec)
    assert m["takes"] == 3 and m["start"] == pytest.approx(rec[8]["start"] - 0.08)
    assert [a[1] for a in m["alternatives"]] == [0.0, pytest.approx(rec[4]["start"] - 0.08)]


def test_a_stumbled_last_take_loses_to_the_good_one_before_it():
    rec = said("Every time you send one the phone cuts it into packets", 1.0,
               "Every time you send one the phone uh", 1.0, "other words here")
    (m,) = match(["Every time you send one, the phone cuts it into packets."], rec)
    assert m["start"] == 0.0 and m["score"] == 1.0 and m["takes"] == 2


def test_take_best_prefers_the_highest_score():
    whole = "And they travel a lot more slowly than you would think today"
    rec = said(whole, 1.0, whole.replace(" lot", ""), 1.0, "And they travel a lot")
    sentence = [whole + "."]
    assert match(sentence, rec, take="best")[0]["start"] == 0.0
    m = match(sentence, rec, take="last")[0]
    assert m["start"] > 4.0 and m["score"] == pytest.approx(11 / 12, abs=0.001)    # close enough to the best: the later one
    assert m["takes"] == 2                                                         # the unfinished third try is no take


def test_missing_sentence_is_reported_not_invented():
    rec = said("How does a message travel", 1.0, "And they travel apart")
    a, b, c = match(["How does a message travel?", "This was never said by anyone.", "And they travel apart."], rec)
    assert b is None and a and c
    assert match(["Yes."], said("no no no")) == [None]
    assert sa.align(["Anything."], [], {}) == [None]


def test_sentences_said_in_another_order():
    rec = said("And they travel apart", 1.0, "How does a message travel")
    a, b = match(["How does a message travel?", "And they travel apart."], rec)
    assert a["start"] > b["start"]
    assert [s["sentences"] for s in sa.join_shots([a, b])] == [[0], [1]]


def test_a_sentence_inside_a_longer_one_is_not_taken_from_it():
    rec = said("And they travel apart.", 2.0, "And they travel apart again.")
    a, b = match(["And they travel apart.", "And they travel apart again."], rec)
    assert a["start"] == 0.0 and b["start"] > 3.0


def test_the_same_sentence_twice_in_the_script_takes_two_places():
    rec = said("You know what?", 1.0, "It works.", 1.0, "You know what?", 1.0, "It fails.")
    a, _, c, _ = match(["You know what?", "It works.", "You know what?", "It fails."], rec)
    assert a["start"] == 0.0 and c["start"] > a["end"]


def test_a_very_short_sentence_goes_next_to_its_neighbors():
    rec = said("Exactly.", 1.0, "How does a message travel", 0.2, "Exactly.", 1.0, "we said exactly that")
    a, b = match(["How does a message travel?", "Exactly."], rec)
    assert b["start"] == pytest.approx(rec[6]["start"] - 0.08)          # the one right after the question


def test_short_sentences_need_all_their_words():
    assert match(["And they travel."], said("and they were here")) == [None]


def test_takes_never_span_two_sources():
    words = sa.transcript([{"words": said("How does a")}, {"words": said("message travel now")}])
    assert sa.align(["How does a message travel now?"], words, {}) == [None]
    words = sa.transcript([{"words": said("unrelated words")}, {"words": said("How does a message travel")}])
    assert sa.align(["How does a message travel?"], words, {})[0]["source"] == 1


def test_consecutive_sentences_are_joined_into_one_shot():
    rec = said("How does a message travel?", 0.2, "It goes further than expected.", 2.0, "And they travel apart.")
    m = match(["How does a message travel?", "It goes further than expected.", "And they travel apart."], rec)
    shots = sa.join_shots(m)
    assert [s["sentences"] for s in shots] == [[0, 1], [2]]
    assert shots[0]["start"] == m[0]["start"] and shots[0]["end"] == m[1]["end"]
    assert [s["sentences"] for s in sa.join_shots(m, join_gap=0.01)] == [[0], [1], [2]]
    assert [s["sentences"] for s in sa.join_shots([m[0], None, m[2]])] == [[0], [2]]


def test_min_match_setting():
    rec = said("How does the signal travel maybe")
    assert match(["How does a message travel today?"], rec) == [None]
    assert match(["How does a message travel today?"], rec, min_match=0.5)[0]["score"] == 0.5


def test_script_words_are_what_was_written_with_the_times_it_was_said():
    rec = said(1.0, "it starts from hero and then uh it ends")
    (m,) = match(["It starts from zero, and then it ends."], rec)
    words = m["words"]
    assert [w[0] for w in words] == ["It", "starts", "from", "zero,", "and", "then", "it", "ends."]
    assert words[3][1:] == [rec[2]["end"], rec[4]["start"]]             # "zero" takes the place of the misheard word
    assert words[0][1:] == [1.0, 1.3] and words[-1][1:] == [rec[-1]["start"], rec[-1]["end"]]
    # a written word that was not said has no time left for it: it is not shown
    (m,) = match(["It really starts from zero and then it ends."], said("it starts from zero and then it ends"))
    assert [w[0] for w in m["words"]] == ["It", "starts", "from", "zero", "and", "then", "it", "ends."]


def test_subtitles_use_the_script_words_unless_told_otherwise(cfg):
    rec = said(1.0, "it starts from hero and then it ends", 2.0, "more words after")
    plan = _plan(rec, "It starts from zero, and then it ends.\n")
    analyses = [{"words": rec, "duration": 9.0}]
    words = subtitle_analyses(plan, analyses, cfg)[0]["words"]
    assert [w["word"] for w in words] == ["It", "starts", "from", "zero,", "and", "then", "it", "ends.", "more", "words", "after"]
    assert analyses[0]["words"] is rec and rec[3]["word"] == "hero"     # the analysis itself is not changed
    assert subtitle_analyses(plan, analyses, {**cfg, "script": {"subtitles": "speech"}}) is analyses
    assert subtitle_analyses({"shots": []}, analyses, cfg) is analyses


# ── the plan ─────────────────────────────────────────────────────────────────
def _plan(recording, text=SCRIPT):
    script = parse_script(text, templates=set(TEMPLATES))
    words = sa.transcript([{"words": recording}])
    return build_plan(script, sa.align([s.text for s in script.sentences], words, {}), {}, "script.md")


RECORDING = said(0.5, "How does a message travel?", 0.2, "It goes further than expected.", 1.5,
                 "Every time you send one, the phone cuts it into packets.", 1.5, "And they travel apart.")


def test_plan_has_shots_sentences_and_cutaways():
    plan = _plan(RECORDING)
    assert [s["sentences"] for s in plan["shots"]] == [[0, 1], [2], [3]]
    assert all(s["transition"] == "cut" and s["source"] == 0 for s in plan["shots"])
    assert plan["shots"][0]["note"] == "How does a message travel? It goes further than expected."
    assert [s["n"] for s in plan["sentences"]] == [0, 1, 2, 3] and plan["sentences"][2]["score"] == 1.0
    assert plan["cutaways"] == [{"type": "graphic", "template": "bars", "title": "Where messages travel",
                                 "data": "Under the sea:95, By satellite:5", "unit": "%", "at_sentence": 2, "until_sentence": 2}]
    assert plan["script"] == "script.md" and plan["texts"] == []


def test_dissolves_between_sentences_add_half_of_it_on_each_side():
    script = parse_script(SCRIPT, templates=set(TEMPLATES))
    matches = sa.align([s.text for s in script.sentences], sa.transcript([{"words": RECORDING}]), {})
    cut = build_plan(script, matches, {}, "script.md")["shots"]
    soft = build_plan(script, matches, {"script": {"transition": "dissolve"}}, "script.md")["shots"]
    assert [s["transition"] for s in soft] == ["cut", "dissolve", "dissolve"]      # the first shot has nothing before it
    assert soft[0]["start"] == cut[0]["start"] and soft[-1]["end"] == cut[-1]["end"]
    for a, b in zip(cut[1:], soft[1:]):                   # the crossfade is centered on the old cut: no word is dimmed
        assert b["start"] == pytest.approx(a["start"] - 0.1)
    for a, b in zip(cut[:-1], soft[:-1]):
        assert b["end"] == pytest.approx(a["end"] + 0.1)


def test_soften_fades_full_frame_cutaways_and_chains_the_ones_in_a_row():
    files = [{"file": "a", "start": 1.0, "end": 3.0}, {"file": "b", "start": 3.0, "end": 4.5},
             {"file": "note", "start": 3.2, "end": 4.0, "alpha": True}, {"file": "c", "start": 6.0, "end": 8.0},
             {"file": "d", "start": 0.0, "end": 0.9}]
    soften(files, 0.2, 30, total=8.0)
    a, b, note, c, d = files
    assert (a["fade_in"], a["fade_out"], a["hold"]) == (0.2, 0.0, pytest.approx(0.2333, abs=1e-3))   # b dissolves over it
    assert (b["fade_in"], b["fade_out"]) == (0.2, 0.2) and "hold" not in b
    assert "fade_in" not in note                                         # transparent notes keep their own entrance
    assert (c["fade_in"], c["fade_out"]) == (0.2, 0.0)                   # it ends with the video
    assert d["fade_in"] == 0.0 and d["fade_out"] == pytest.approx(0.2)   # the video starts on it
    assert soften([{"file": "x", "start": 1.0, "end": 1.3}], 0.2, 30)[0]["fade_in"] == pytest.approx(0.1)   # ≤ ⅓ of it
    assert "fade_in" not in soften([{"file": "x", "start": 1.0, "end": 3.0}], 0.0, 30)[0]


def test_plan_marks_missing_sentences_and_fails_when_nothing_was_said():
    plan = _plan(said("How does a message travel?", 1.0, "And they travel apart."))
    assert [s.get("missing", False) for s in plan["sentences"]] == [False, True, True, False]
    assert "2 of 4" in plan["note"]
    with pytest.raises(PlanError, match="None of the script's sentences"):
        _plan(said("completely different words"))


def test_cue_seconds_and_kinds_reach_the_plan():
    text = "One two three.\n[photo file=\"a.jpg\" seconds=2]\nFour five six. Seven eight nine.\n"
    plan = _plan(said("One two three.", 1, "Four five six.", 0.1, "Seven eight nine."), text)
    assert plan["cutaways"] == [{"type": "photo", "file": "a.jpg", "at_sentence": 1, "until_sentence": 2, "seconds": 2.0}]


# ── cutaways on the timeline ─────────────────────────────────────────────────
def _timeline(plan, cfg):
    return compute_timeline(plan, cfg)


def test_cutaway_covers_its_sentence(cfg):
    plan = _plan(RECORDING)
    tl = _timeline(plan, cfg)
    spans = sentence_spans(plan, tl)
    assert spans[0][0] == 0.0 and spans[2][0] == pytest.approx(tl[1].out_start)
    (c,), warnings = resolve(plan, tl, 30)
    assert not warnings and c.kind == "graphic" and not c.overlay and c.sentences == (2, 2)
    assert c.start == pytest.approx(tl[1].out_start, abs=1 / 30) and c.end == pytest.approx(tl[1].out_end, abs=1 / 30)
    assert c.spec == {"template": "bars", "title": "Where messages travel", "data": "Under the sea:95, By satellite:5", "unit": "%"}


def test_cutaways_follow_their_sentence_when_pauses_split_the_shots(cfg):
    """A long pause in the middle of the first sentence is shortened: the shot is split and the shots
    renumbered, and the cutaway still starts with its sentence."""
    def recording(pause):
        return said(0.5, "How does a", pause, "message travel?", 0.2, "It goes further than expected.", 0.3,
                    "Every time you send one, the phone cuts it into packets.", 0.3, "And they travel apart.")
    plan = _plan(recording(0.0))
    assert len(plan["shots"]) == 1
    before = sentence_spans(plan, _timeline(plan, cfg))[2][0]

    rec = recording(3.0)
    plan = _plan(rec)
    assert [s["sentences"] for s in plan["shots"]] == [[0, 1, 2, 3]]
    quiet = (rec[2]["end"] + 0.1, rec[3]["start"] - 0.1)
    n = int(rec[-1]["end"] * 10) + 5
    analysis = {"duration": n / 10, "words": rec, "voice": {"segments": [[0.0, n / 10]], "ratio": 0.9},
                "audio": {"step": 0.1, "median_db": -20.0,
                          "db": [-60.0 if quiet[0] <= i / 10 < quiet[1] else -20.0 for i in range(n)]}}
    assert shorten_plan_pauses(plan, [analysis], cfg) == 1
    assert len(plan["shots"]) == 2 and plan["shots"][1]["pause_cut"] and "sentences" not in plan["shots"][1]
    tl = _timeline(plan, cfg)
    spans = sentence_spans(plan, tl)
    assert spans[2][0] == pytest.approx(before + 0.45, abs=0.2)       # the 3 s pause now lasts about 0.45 s
    (c,), _ = resolve(plan, tl, 30)
    assert c.start == pytest.approx(spans[2][0], abs=1 / 30)
    assert spans[0][1] - spans[0][0] < 3.0                            # the first sentence got shorter, not lost


def _talk(cfg, cutaways, seconds=12.0):
    """One 12 s shot with four 3 s sentences."""
    plan = {"shots": [{"source": 0, "start": 0.0, "end": seconds, "transition": "cut"}],
            "sentences": [{"n": k, "text": f"s{k}", "source": 0, "start": 3.0 * k, "end": 3.0 * k + 3.0} for k in range(4)],
            "cutaways": cutaways}
    return plan, compute_timeline(plan, cfg)


def test_seconds_caps_a_cutaway_and_absolute_times_work(cfg):
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 0, "until_sentence": 1, "seconds": 2.5},
                           {"type": "photo", "file": "a.jpg", "start": 7.0, "end": 8.5},
                           {"type": "photo", "file": "b.jpg", "start": 10.0, "seconds": 1.5}])
    (a, b, c), warnings = resolve(plan, tl, 30)
    assert (a.start, a.end) == (0.0, 2.5) and (b.start, b.end) == (7.0, 8.5) and (c.start, c.end) == (10.0, 11.5)
    assert not warnings and b.label == "a.jpg"


def test_a_cutaway_stays_long_enough_to_be_read(cfg):
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 1, "seconds": 0.5}])
    (c,), _ = resolve(plan, tl, 30, entrance=lambda cut: 1.4)
    assert (c.start, c.end) == (3.0, pytest.approx(3.0 + 1.4 + 0.8, abs=1 / 30))


def test_two_cutaways_never_overlap_and_the_presenter_is_not_flashed_between_them(cfg):
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 0, "until_sentence": 1},
                           {"type": "graphic", "template": "number", "value": "5", "start": 4.0, "end": 9.0}])
    (a, b), warnings = resolve(plan, tl, 30)
    assert a.end == b.start == 4.0 and not warnings                    # the first one is cut short
    plan, tl = _talk(cfg, [{"type": "photo", "file": "a.jpg", "at_sentence": 0, "seconds": 2.6},
                           {"type": "photo", "file": "b.jpg", "at_sentence": 1}])
    (a, b), _ = resolve(plan, tl, 30)
    assert a.end == b.start == 3.0                                     # 0.4 s of presenter would be a flash
    plan, tl = _talk(cfg, [{"type": "photo", "file": "a.jpg", "at_sentence": 0, "seconds": 1.5},
                           {"type": "photo", "file": "b.jpg", "at_sentence": 1}])
    (a, b), _ = resolve(plan, tl, 30)
    assert a.end == 1.5                                                # 1.5 s of presenter is fine


def test_what_does_not_fit_is_left_out_with_a_warning(cfg):
    plan, tl = _talk(cfg, [{"type": "photo", "file": "a.jpg", "start": 5.0, "seconds": 3},
                           {"type": "photo", "file": "b.jpg", "start": 5.3, "end": 9.0},
                           {"type": "photo", "file": "c.jpg", "at_sentence": 9},
                           {"type": "photo", "file": "d.jpg", "start": 40.0}])
    placed, warnings = resolve(plan, tl, 30)
    assert [c.label for c in placed] == ["b.jpg"]
    assert len(warnings) == 3 and "a.jpg" in warnings[2] and "sentence 9 is not in the video" in warnings[0]
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "bars", "data": "A:1", "start": 5.0, "seconds": 1},
                           {"type": "photo", "file": "b.jpg", "start": 6.2, "end": 9.0}])
    placed, warnings = resolve(plan, tl, 30, entrance=lambda cut: 1.0)
    assert len(placed) == 2 and "it needs 1.8 s" in warnings[0]


def test_a_lower_third_goes_over_the_picture_and_may_share_the_time(cfg):
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "bars", "data": "A:1", "start": 2.0, "end": 8.0},
                           {"type": "graphic", "template": "lower_third", "text": "Source", "start": 3.0, "end": 6.0}])
    (a, b), warnings = resolve(plan, tl, 30)
    assert not a.overlay and b.overlay and (a.end, b.start, b.end) == (8.0, 3.0, 6.0) and not warnings


@pytest.mark.parametrize("cutaway, message", [
    ({"type": "hologram", "start": 1}, "unknown type 'hologram'"),
    ({"type": "photo", "file": "a.jpg"}, "needs 'at_sentence'"),
    ({"type": "photo", "file": "a.jpg", "start": "soon"}, "'start' must be a number"),
    ({"type": "photo", "file": "a.jpg", "at_sentence": 0, "seconds": 0}, "'seconds' must be a number"),
])
def test_cutaway_errors(cfg, cutaway, message):
    plan, tl = _talk(cfg, [cutaway])
    with pytest.raises(PlanError) as e:
        resolve(plan, tl, 30)
    assert message in str(e.value)


# ── framing the camera shots ─────────────────────────────────────────────────
def _jump_cuts(cfg, cutaways=()):
    plan = {"shots": [{"source": 0, "start": 0.0, "end": 3.0, "transition": "cut"},
                      {"source": 0, "start": 5.0, "end": 8.0, "transition": "cut"},
                      {"source": 0, "start": 8.4, "end": 10.0, "transition": "soft_cut", "pause_cut": True},
                      {"source": 0, "start": 12.0, "end": 15.0, "transition": "cut"},
                      {"source": 1, "start": 0.0, "end": 3.0, "transition": "cut"}],
            "cutaways": list(cutaways)}
    tl = compute_timeline(plan, cfg)
    placed, _ = resolve(plan, tl, 30)
    return plan, tl, placed


INFOS = [{"width": 1080, "height": 1920}, {"width": 1920, "height": 1080}]


def still(cfg: dict, **script) -> dict:
    """The configuration without the slow drift (the tests below look at the punch-in alone)."""
    return {**cfg, "script": {**cfg.get("script", {}), "drift": 0, **script}}


def test_punch_in_alternates_at_visible_jump_cuts(cfg):
    plan, tl, placed = _jump_cuts(cfg)
    counts = frame_shots(plan, tl, placed, {**cfg, "script": {"punch_in": 1.1}}, INFOS, [{}, {}])
    assert [s.get("zoom") for s in plan["shots"]] == [None, 1.1, 1.1, None, None]   # a pause join keeps the zoom
    assert counts == {"punch_ins": 1, "framed": 0}


def test_a_cut_hidden_by_a_cutaway_keeps_the_zoom_and_overlays_hide_nothing(cfg):
    plan, tl, placed = _jump_cuts(cfg, [{"type": "photo", "file": "a.jpg", "start": 2.0, "end": 4.0}])
    frame_shots(plan, tl, placed, still(cfg), INFOS, [{}, {}])
    assert [s.get("zoom") for s in plan["shots"]] == [None, None, None, 1.08, None]
    # a cut exactly where a cutaway begins (or ends) is not a visible jump either
    plan, tl, placed = _jump_cuts(cfg, [{"type": "photo", "file": "a.jpg", "start": 3.0, "end": 5.0}])
    frame_shots(plan, tl, placed, still(cfg), INFOS, [{}, {}])
    assert [s.get("zoom") for s in plan["shots"]] == [None, None, None, 1.08, None]
    plan, tl, placed = _jump_cuts(cfg, [{"type": "photo", "file": "a.jpg", "start": 1.0, "end": 3.0}])
    frame_shots(plan, tl, placed, still(cfg), INFOS, [{}, {}])
    assert [s.get("zoom") for s in plan["shots"]] == [None, None, None, 1.08, None]
    plan, tl, placed = _jump_cuts(cfg, [{"type": "graphic", "template": "lower_third", "text": "x", "start": 2.0, "end": 4.0}])
    frame_shots(plan, tl, placed, still(cfg), INFOS, [{}, {}])
    assert [s.get("zoom") for s in plan["shots"]] == [None, 1.08, 1.08, None, None]


def test_punch_in_off_and_old_zooms_removed(cfg):
    plan, tl, placed = _jump_cuts(cfg)
    plan["shots"][0]["zoom"] = 1.3
    frame_shots(plan, tl, placed, {**cfg, "script": {"punch_in": 0}}, INFOS, [{}, {}])
    assert all("zoom" not in s for s in plan["shots"])


def test_shots_are_framed_on_a_face_seen_often_enough(cfg):
    plan, tl, placed = _jump_cuts(cfg)
    face = {"x": 0.4, "y": 0.3, "size": 0.2, "eyes": 0.28, "ratio": 0.9}
    counts = frame_shots(plan, tl, placed, still(cfg), INFOS, [{"face": face}, {"face": {**face, "ratio": 0.2}}])
    assert counts["framed"] == 4
    assert all((s["focus_x"], s["focus_y"]) == (0.4, 0.3) for s in plan["shots"][:4])   # same shape: zoom about the face
    assert "focus_x" not in plan["shots"][4]


def test_face_focus_centers_the_crop_and_puts_the_eyes_on_the_upper_third():
    face = {"x": 0.6, "y": 0.5, "size": 0.3, "eyes": 0.46}
    # 4:3 source in a 9:16 frame (cropped sideways): the face is centered; the height is whole
    fx, fy = face_focus(face, 1440, 1080, 1080, 1920, mode="crop")
    cover_w = 1440 * 1920 / 1080
    assert fx == pytest.approx((0.6 * cover_w - 540) / (cover_w - 1080), abs=0.001) and fy == 0.5
    # 3:4 source in a square frame (cropped top and bottom): eyes at a third of the height
    fx, fy = face_focus(face, 1080, 1440, 1080, 1080, mode="crop")
    assert fx == 0.6 and fy == pytest.approx((0.46 * 1440 - 360) / (1440 - 1080), abs=0.001)
    # a face at the edge cannot be centered: the crop stops at the border
    assert face_focus({"x": 0.02, "y": 0.5, "size": 0.2}, 1440, 1080, 1080, 1920, mode="crop")[0] == 0.0
    # very different shapes are fitted with blurred borders: the face's place inside the frame
    fx, fy = face_focus(face, 1920, 1080, 1080, 1920)
    assert fx == 0.6 and fy == pytest.approx((1920 - 607.5) / 2 / 1920 + 0.5 * 607.5 / 1920, abs=0.001)
    # eyes estimated from the face box when the detector does not give them
    assert face_focus({"x": 0.5, "y": 0.5, "size": 0.25}, 1080, 1440, 1080, 1080, mode="crop")[1] == pytest.approx(
        ((0.5 - 0.12 * 0.25) * 1440 - 360) / 360, abs=0.001)


def test_another_frame_shape_frames_the_faces_again_and_keeps_a_focus_set_by_hand():
    face = {"x": 0.6, "y": 0.4, "size": 0.3, "eyes": 0.36, "ratio": 0.9}
    infos, analyses = [{"width": 1440, "height": 1080}, {"width": 1440, "height": 1080}], [{"face": face}, {}]
    fx, fy = face_focus(face, 1440, 1080, 1080, 1920)
    shots = [{"source": 0, "focus_x": fx, "focus_y": fy},            # as the planner framed it for 9:16
             {"source": 0, "focus_x": 0.2, "focus_y": 0.2},          # set by hand
             {"source": 1, "focus_x": fx, "focus_y": fy},            # no face known for that source
             {"type": "graphic", "template": "title"}]
    assert refocus_faces(shots, infos, analyses, (1080, 1920), (1920, 1080)) == 1
    assert (shots[0]["focus_x"], shots[0]["focus_y"]) == face_focus(face, 1440, 1080, 1920, 1080) != (fx, fy)
    assert shots[1] == {"source": 0, "focus_x": 0.2, "focus_y": 0.2} and shots[2]["focus_x"] == fx


# ── Spanish names and the report ─────────────────────────────────────────────
def test_spanish_settings_and_plan_keys():
    assert legacy_es.config({"explicativo": True, "guion": {"archivo": "guion.md", "toma": "ultima", "acercamiento": 1.1,
                                                            "coincidencia_min": 0.7, "union_max": 0.4, "subtitulos": "voz"},
                             "encuadre": {"cara": True}}) == {
        "explainer": True, "script": {"file": "guion.md", "take": "last", "punch_in": 1.1, "min_match": 0.7, "join_gap": 0.4,
                                      "subtitles": "speech"},
        "framing": {"face": True}}
    plan = legacy_es.plan({"guion": "guion.md", "frases": [{"n": 0}],
                           "tomas_encima": [{"tipo": "grafico", "plantilla": "barras", "datos": "A:1", "en_frase": 1,
                                             "hasta_frase": 2, "segundos": 3},
                                            {"tipo": "foto", "archivo": "a.jpg", "ini": 1, "fin": 2}]})
    assert plan == {"script": "guion.md", "sentences": [{"n": 0}],
                    "cutaways": [{"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 1,
                                  "until_sentence": 2, "seconds": 3},
                                 {"type": "photo", "file": "a.jpg", "start": 1, "end": 2}]}
    assert legacy_es.cue({"template": "camara"}) == {"template": "camera"}


def test_report_lists_the_script_and_the_cutaways(cfg):
    plan = _plan(said("How does a message travel?", 1.0, "Every time you send one, the phone cuts it into packets.",
                      1.0, "Every time you send one, the phone cuts it into packets."))
    sc = report._script(plan)
    assert (sc["sentences"], sc["found"]) == (4, 2) and [m["n"] for m in sc["missing"]] == [1, 3]
    assert sc["repeated"] == [{"n": 2, "takes": 2, "start": plan["sentences"][2]["start"], "score": 1.0}]
    lines = report._script_lines(sc)
    assert lines[0] == "SCRIPT script.md · 4 sentences · 2 found"
    assert "⚠ sentence 1 not found in the recording: It goes further than expected." in lines[1]
    assert any("sentence 2: 2 takes, used the one at 00:0" in line for line in lines)
    tl = compute_timeline(plan, cfg)
    placed, warnings = resolve(plan, tl, 30)
    cu = {"count": 1, "warnings": ["cutaway 3 (x): left out"],
          "placed": [{"what": c.label, "start": c.start, "end": c.end, "overlay": c.overlay,
                      "sentences": list(c.sentences)} for c in placed]}
    lines = report._cutaway_lines(cu)
    assert lines[0].startswith("CUTAWAYS 1 · 1 placed") and "bars " in lines[1] and "(sentence 2)" in lines[1]
    assert "⚠ cutaway 3" in lines[2]
    assert report._script_lines(None) == [] and report._cutaway_lines(None) == []


# ── the presenter's face ─────────────────────────────────────────────────────
class _Video:
    def __init__(self, face):
        self.result, self.calls = face, []

    def face(self, video, download=False):
        self.calls.append((video, download))
        return self.result


class _Svc:
    def __init__(self, face):
        self.video = _Video(face)


@pytest.mark.parametrize("setting, calls", [(False, []), (None, []), ("auto", [("proxy.mp4", False)]),
                                            (True, [("proxy.mp4", True)])])
def test_face_is_looked_for_only_when_framing_face_asks(setting, calls):
    from social_video_editor.application.analyze import _add_face

    svc, data = _Svc({"ratio": 0.9, "x": 0.5, "y": 0.3, "size": 0.2}), {}
    changed = _add_face(svc, {"path": "clip.mov", "proxy": "proxy.mp4"}, data, {"framing": {"face": setting}})
    assert svc.video.calls == calls and changed is bool(calls) and ("face" in data) is bool(calls)


def test_no_face_detector_leaves_the_analysis_alone():
    from social_video_editor.application.analyze import _add_face

    data = {}
    assert _add_face(_Svc(None), {"path": "clip.mov", "proxy": "proxy.mp4"}, data, {"framing": {"face": "auto"}}) is False
    assert data == {}
    assert _add_face(_Svc({"ratio": 1}), {"path": "photo.jpg", "proxy": None}, data, {"framing": {"face": True}}) is False


def test_doctor_reports_face_framing():
    from social_video_editor.adapters.system.doctor import face_check

    status, name, detail = face_check()
    assert name == "face framing" and status in ("ok", "optional") and detail


# ── one camera that reads like several ───────────────────────────────────────
def test_long_runs_are_reframed_between_sentences_without_losing_anything():
    rec = said("One two three four five six.", 0.2, "Seven eight nine ten eleven twelve.", 0.2,
               "Thirteen fourteen fifteen sixteen seventeen eighteen.", 0.2, "Nineteen twenty.")
    sentences = ["One two three four five six.", "Seven eight nine ten eleven twelve.",
                 "Thirteen fourteen fifteen sixteen seventeen eighteen.", "Nineteen twenty."]
    m = match(sentences, rec)
    assert [x["sentences"] for x in sa.join_shots(m)] == [[0, 1, 2, 3]]
    a, b = sa.join_shots(m, reframe_every=3.0)                       # each sentence lasts about 2 s
    assert (a["sentences"], b["sentences"]) == ([0, 1], [2, 3]) and b["continues"] is True
    assert a["end"] == b["start"]                                    # the picture and the sound go straight on
    assert m[1]["end"] - 0.01 <= a["end"] <= m[2]["start"] + 0.01    # cut in the quiet between the two sentences
    # a tail too short to register stays in its shot
    assert [x["sentences"] for x in sa.join_shots([None, None, m[2], m[3]], reframe_every=1.0)] == [[2, 3]]
    assert [x["sentences"] for x in sa.join_shots(m[:2], reframe_every=1.9)] == [[0], [1]]
    assert [x["sentences"] for x in sa.join_shots([m[0], m[3]], reframe_every=1.0)] == [[0], [1]]   # not said in a row


def test_reframed_shots_alternate_the_punch_in(cfg):
    rec = said("One two three four five six.", 0.2, "Seven eight nine ten eleven twelve.", 0.2,
               "Thirteen fourteen fifteen sixteen seventeen eighteen.")
    script = parse_script("One two three four five six. Seven eight nine ten eleven twelve.\n"
                          "Thirteen fourteen fifteen sixteen seventeen eighteen.\n")
    words = sa.transcript([{"words": rec}])
    plan = build_plan(script, sa.align([x.text for x in script.sentences], words, {}), {"script": {"reframe_every": 1.5}})
    assert [x["sentences"] for x in plan["shots"]] == [[0], [1], [2]] and plan["shots"][1]["continues"] is True
    tl = compute_timeline(plan, cfg)
    frame_shots(plan, tl, [], still(cfg), INFOS, [{}, {}])
    assert [x.get("zoom") for x in plan["shots"]] == [None, 1.08, None]


def test_cuts_rotate_three_framings(cfg):
    plan = {"shots": [{"source": 0, "start": 10.0 * k, "end": 10.0 * k + 3, "transition": "cut"} for k in range(6)], "cutaways": []}
    tl = compute_timeline(plan, cfg)
    frame_shots(plan, tl, [], still(cfg, punch_in=1.1, close_up=1.2), INFOS, [{}, {}])
    assert [x.get("zoom") for x in plan["shots"]] == [None, 1.1, None, 1.2, None, 1.1]      # wide, punch-in, wide, close-up…
    frame_shots(plan, tl, [], still(cfg, punch_in=1.1, close_up=0), INFOS, [{}, {}])
    assert [x.get("zoom") for x in plan["shots"]] == [None, 1.1, None, 1.1, None, 1.1]
    frame_shots(plan, tl, [], still(cfg, punch_in=0, close_up=1.2), INFOS, [{}, {}])
    assert all("zoom" not in x for x in plan["shots"])


def test_every_camera_shot_drifts_slowly_and_stays_continuous_over_pause_joins(cfg):
    plan, tl, placed = _jump_cuts(cfg)
    frame_shots(plan, tl, placed, {**cfg, "script": {"punch_in": 1.1, "drift": 0.04}}, INFOS, [{}, {}])
    a, b, c, d, e = plan["shots"]
    assert a["effects"] == [{"type": "push_in", "amount": pytest.approx(0.04 * 3 / 5), "auto": True}] and "zoom" not in a
    # shots 1 and 2 are one run (a shortened pause joins them): punched in, so it pulls out, without a jump
    assert [x["effects"][0]["type"] for x in (b, c)] == ["pull_out", "pull_out"]
    total = tl[1].dur + tl[2].dur
    amount = 0.04 * min(total / 5, 1)
    assert b["zoom"] + b["effects"][0]["amount"] == pytest.approx(1.1 + amount, abs=1e-3)       # starts at its widest
    assert b["zoom"] == pytest.approx(c["zoom"] + c["effects"][0]["amount"], abs=1e-3)          # the join does not jump
    assert c["zoom"] == pytest.approx(1.1, abs=1e-3)                                             # and ends on the punch-in
    assert d["effects"][0]["type"] == "push_in" and e["effects"][0]["type"] == "push_in"
    # planning again replaces the planner's own effects and keeps the ones written by hand
    a["effects"].append({"type": "shake", "t": 1.0})
    frame_shots(plan, tl, placed, {**cfg, "script": {"punch_in": 1.1, "drift": 0}}, INFOS, [{}, {}])
    assert a["effects"] == [{"type": "shake", "t": 1.0}] and b["effects"] == [] and b["zoom"] == 1.1


# ── notes proposed around the presenter ──────────────────────────────────────
def _words(text: str, start: float = 0.0, step: float = 0.4) -> list[list]:
    return [[w, round(start + i * step, 2), round(start + i * step + 0.3, 2)] for i, w in enumerate(text.split())]


def test_a_sentence_offers_figures_and_things_with_an_illustration():
    notes = [n for n in sentence_notes(_words("Si cierras 20 apps al día pierdes batería y memoria."), "es")
             if n["kind"] != "emphasis"]
    assert [(n["label"], n["icon"], n["kind"]) for n in notes] == [
        ("20 apps", None, "figure"), ("Batería", "battery", "icon"), ("Memoria", "ram", "icon")]
    assert notes[0]["t"] == 0.8                                           # when "20" is said
    assert [n["label"] for n in sentence_notes(_words("Your phone takes 240 ms, 5% more."), "en")] == ["Phone", "240 ms", "5%"]
    assert sentence_notes(_words("Tu y yo, el y la."), "es") == []             # function words never name anything
    assert [n["label"] for n in sentence_notes(_words("La app y otra app."), "es")] == ["App"]   # an illustration once


def test_nouns_fill_the_room_between_figures_and_illustrations():
    notes = sentence_notes(_words("La memoria es como una mesa, ahí esperan tus apps."), "es")
    assert [(n["label"], n["kind"]) for n in notes] == [("Memoria", "icon"), ("Mesa", "word"), ("Esperan", "emphasis"),
                                                         ("Apps", "icon")]
    # "las" before a verb is a pronoun, not an article; "tú" is not "tu"
    notes = sentence_notes(_words("Pero si las cierras se van a la bodega."), "es")
    assert [(n["label"], n["kind"]) for n in notes] == [("Cierras", "emphasis"), ("Bodega", "word")]
    assert sentence_notes(_words("Tú usas eso y no las tocas."), "es") == []
    assert [n["label"] for n in sentence_notes(_words("Open the window and your settings."), "en")] == ["Window", "Settings"]
    # at most three per sentence: figures and illustrations take their place first, words what is left
    plan = _spoken("Aquí empieza todo lo demás de hoy.", "La mesa guarda tu celular y la memoria vieja de la casa.")
    (overlay,) = propose(plan, "es")
    assert overlay["items"] == ["Mesa", "Celular", "Memoria"] and len(overlay["item_times"]) == 3     # no place for "casa"
    # a word needs room on both sides: "mesa" 0.4 s before "celular" is left out, "casa" comes in
    plan = _spoken("Aquí empieza todo lo demás de hoy.", "Tu mesa celular y la memoria vieja de la casa.", "Y sigue así.")
    assert propose(plan, "es")[0]["items"] == ["Celular", "Memoria", "Casa"]


def test_an_overlay_holds_six_notes_and_then_starts_over():
    plan = _spoken("Aquí empieza todo lo demás de hoy.", *["La mesa y la silla y la casa nueva."] * 1,
                   "El perro y el gato y el pato feo.", "Un libro y un lápiz y un papel azul.", gap=0.6)
    first, second = propose(plan, "es")
    assert len(first["items"]) == 6 and second["items"][0] not in first["items"]
    assert first["until_sentence"] <= second["at_sentence"]


def test_long_words_are_emphasis_and_an_empty_sentence_settles_for_a_shorter_one():
    notes = sentence_notes(_words("No están abiertas, están dormidas."), "es")
    assert [(n["label"], n["kind"], n["t"]) for n in notes] == [("Abiertas", "emphasis", 0.8), ("Dormidas", "emphasis", 1.6)]
    assert sentence_notes(_words("Hay que traerlas luego."), "es") == []                   # a verb with its pronoun; a short word
    (note,) = sentence_notes(_words("Hay que traerlas luego."), "es", key_word=True)
    assert note["label"] == "Luego"
    assert [n["label"] for n in sentence_notes(_words("Hay que traerlas rápido."), "es")] == ["Rápido"]   # six letters: a note
    assert sentence_notes(_words("Eso es todo por hoy."), "es", key_word=True) == []
    assert sentence_notes(_words("Really amazing."), "en", key_word=True) == []            # adverbs and -ing forms read badly alone


def _spoken(*sentences, gap: float = 0.3, covered=()) -> dict:
    plan, t = {"sentences": [], "cutaways": [{"type": "photo", "file": "x.jpg", "at_sentence": k} for k in covered]}, 0.0
    for n, text in enumerate(sentences):
        words = _words(text, t)
        plan["sentences"].append({"n": n, "text": text, "source": 0, "start": t, "end": words[-1][2], "words": words}
                                 if text else {"n": n, "text": "?", "missing": True})
        t = (words[-1][2] if text else t) + gap
    return plan


def test_notes_are_grouped_in_overlays_and_timed_by_their_words():
    plan = _spoken("Hoy vamos a mirar algo distinto.", "Tu celular guarda las apps en la memoria.",
                   "Y eso gasta 2 minutos de batería.", gap=0.6)
    (overlay,) = propose(plan, "es")           # "batería", the video's last word, would have no time on screen
    assert overlay["template"] == "callouts" and overlay["_auto"] == "support"
    assert overlay["items"] == ["Distinto", "Celular", "Apps", "Memoria", "2 minutos"]   # up to three per sentence
    assert overlay["icons"] == ["-", "phone", "app", "ram", "-"]
    assert (overlay["at_sentence"], overlay["until_sentence"]) == (0, 2)
    assert overlay["item_times"][1] == [0, plan["sentences"][1]["words"][1][1]]   # when "celular" is said
    assert plan["cutaways"] == []                                                # the plan itself is not touched


def test_notes_leave_alone_what_already_has_a_cutaway_the_opening_and_missing_sentences():
    plan = _spoken("La batería dura más.", "Tu celular guarda las apps en la memoria.", "",
                   "El procesador descansa y el disco también.", covered=(1,))
    (only,) = propose(plan, "es")                    # nothing over the face in the first second ("batería" at 0.4 s)
    assert only["items"] == ["Procesador", "Disco"] and (only["at_sentence"], only["until_sentence"]) == (3, 3)
    assert propose({"sentences": [], "cutaways": []}) == []
    # a note is not started right before a full-frame cutaway takes the screen
    plan = _spoken("Para abrir este video algo sencillo.", "Todo vive en la memoria.", "La foto.", covered=(2,))
    assert [o["items"] for o in propose(plan, "es")] == [["Video", "Sencillo"]]
    plan = _spoken("Para abrir este video algo sencillo.", "Todo vive en la memoria.", "Y sigue aquí.")
    assert [o["items"] for o in propose(plan, "es")] == [["Video", "Sencillo", "Memoria"]]


def test_item_times_follow_the_words_through_shortened_pauses(cfg):
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "callouts", "items": ["A", "B", "C"],
                            "item_times": [[0, 3.5], [0, 5.0], [0, 8.9], [0, 30.0]], "at_sentence": 1, "until_sentence": 2}])
    (c,), _ = resolve(plan, tl, 30)
    assert c.overlay and c.auto is None
    assert c.spec["times"] == [0.5, 2.0, 5.5, 5.5]       # at least half a second on screen; one that is not in the video: with the last
    assert output_time(tl, 0, 4.0) == 4.0 and output_time(tl, 1, 4.0) is None
    # a moment cut out of the video lands where the next piece starts
    plan["shots"] = [{"source": 0, "start": 0.0, "end": 3.0, "transition": "cut"}, {"source": 0, "start": 5.0, "end": 12.0, "transition": "cut"}]
    tl = compute_timeline(plan, cfg)
    assert output_time(tl, 0, 4.0) == 3.0 and output_time(tl, 0, 6.0) == 4.0


def test_sentence_by_sentence_bullets_from_a_cue():
    text = "Intro words here.\n[bullets items=\"First; Second\"]\nFour five six. Seven eight nine.\n"
    plan = _plan(said("Intro words here.", 1, "Four five six.", 0.1, "Seven eight nine."), text)
    (cut,) = plan["cutaways"]
    assert cut["item_times"] == [[0, plan["sentences"][1]["start"]], [0, plan["sentences"][2]["start"]]]
    text = text.replace('items=\"First; Second\"', 'items=\"First; Second; Third\"')     # not one per sentence: its own rhythm
    assert "item_times" not in _plan(said("Intro words here.", 1, "Four five six.", 0.1, "Seven eight nine."), text)["cutaways"][0]


def test_an_overlay_does_not_grow_onto_a_full_frame_cutaway(cfg):
    plan, tl = _talk(cfg, [{"type": "graphic", "template": "callouts", "items": "A", "at_sentence": 0, "seconds": 1},
                           {"type": "photo", "file": "a.jpg", "start": 1.5, "end": 6.0}])
    placed, _ = resolve(plan, tl, 30, entrance=lambda c: 2.0)
    notes = next(c for c in placed if c.overlay)
    assert notes.end == 1.5


# ── the presenter's window (picture-in-picture) ──────────────────────────────
def test_window_goes_over_long_full_frame_cutaways_only(cfg):
    cuts = [{"type": "graphic", "template": "bars", "data": "A:1", "start": 0.0, "end": 4.0},
            {"type": "photo", "file": "a.jpg", "start": 5.0, "end": 6.5},
            {"type": "graphic", "template": "lower_third", "text": "x", "start": 7.0, "end": 11.0},
            {"type": "photo", "file": "b.jpg", "start": 8.0, "end": 9.5, "pip": True},
            {"type": "graphic", "template": "bars", "data": "A:1", "start": 9.5, "end": 12.0, "pip": "no"}]
    plan, tl = _talk(cfg, cuts)
    on = {"enabled": True, "min_seconds": 2.5}
    assert [c.pip for c in sorted(resolve(plan, tl, 30, pip=on)[0], key=lambda c: c.index)] == [True, False, False, True, False]
    assert [c.pip for c in sorted(resolve(plan, tl, 30)[0], key=lambda c: c.index)] == [False, False, False, True, False]
    assert wants_pip({"pip": "si"}, None) and not wants_pip({"pip": False}, on) and not wants_pip({}, {"enabled": False})


def test_a_cut_under_a_window_is_still_seen(cfg):
    photo = {"type": "photo", "file": "a.jpg", "start": 2.0, "end": 6.0}
    plan, tl, _ = _jump_cuts(cfg, [photo])
    placed, _ = resolve(plan, tl, 30, pip={"enabled": True, "min_seconds": 2.5})
    frame_shots(plan, tl, placed, still(cfg), INFOS, [{}, {}])
    assert [s.get("zoom") for s in plan["shots"]] == [None, 1.08, 1.08, None, None]


def test_window_layout_leaves_room_for_the_graphic():
    cfg = {"pip": {"enabled": True}}
    v = pip.layout(1080, 1920, cfg)
    x, y, w, h = v["rect"]
    assert (w, h) == (346, 422) and x + w <= 0.88 * 1080 and y + h <= 0.77 * 1920          # inside the safe zone, bottom right
    assert v["safe"][:3] == (0.06, 0.08, 0.88) and v["safe"][3] == pytest.approx((y - 18 - 32.4) / 1920, abs=1e-3)
    assert v["frame"] == (round(x / 1080, 5), round(y / 1920, 5), round(w / 1080, 5), round(h / 1920, 5))
    top = pip.layout(1080, 1920, {"pip": {"corner": "top_left", "size": 0.3}})
    assert top["rect"][0] >= 0.06 * 1080 and top["safe"][1] > 0.25 and top["safe"][3] == 0.77
    assert pip.layout(1080, 1920, {"pip": {"size": 0.5}})["rect"][2] == 540
    wide = pip.layout(1920, 1080, cfg)
    assert wide["safe"][2] < 0.76 and wide["safe"][1::2] == (0.10, 0.82)                    # landscape: beside the window
    assert pip.layout(1920, 1080, {"pip": {"corner": "bottom_left"}})["safe"][0] > 0.24
    with pytest.raises(SystemExit, match="pip.corner"):
        pip.layout(1080, 1920, {"pip": {"corner": "middle"}})


def test_window_shows_head_and_shoulders_around_the_face():
    face = {"x": 0.4, "y": 0.3, "eyes": 0.28, "size": 0.12}
    x, y, w, h = pip.crop(1080, 1920, 388 / 474, face)
    assert h == pytest.approx(0.12 * 2.8 * 1920, abs=2) and w / h == pytest.approx(388 / 474, abs=0.01)
    assert x + w / 2 == pytest.approx(0.4 * 1080, abs=2) and (0.28 * 1920 - y) / h == pytest.approx(0.36, abs=0.01)
    x, y, w, h = pip.crop(1080, 1920, 388 / 474, {"x": 0.02, "y": 0.05, "eyes": 0.04, "size": 0.2})
    assert (x, y) == (0, 0)                                                                 # never outside the picture
    x, y, w, h = pip.crop(1080, 1920, 388 / 474, None)
    assert x + w / 2 == pytest.approx(540, abs=2) and y > 0 and h == pytest.approx(0.52 * 1920, abs=2)
    x, y, w, h = pip.crop(640, 360, 2.0, {"x": 0.5, "y": 0.5, "size": 0.9})                 # a window wider than the picture allows
    assert w <= 640 and h <= 360


def test_face_position_in_the_frame_follows_the_framing():
    face = {"x": 0.6, "y": 0.4, "eyes": 0.36, "size": 0.2, "ratio": 0.9}
    same = pip.face_in_frame(face, 1080, 1920, 1080, 1920, (0.5, 0.5))
    assert same == {"x": 0.6, "y": 0.4, "eyes": 0.36, "size": 0.2}
    # a landscape source cropped to a vertical frame around focus_x: the face moves with the crop
    cropped = pip.face_in_frame(face, 1440, 1080, 1080, 1920, (1.0, 0.5), mode="crop")
    cover = 1440 * 1920 / 1080
    assert cropped["x"] == pytest.approx((0.6 * cover - (cover - 1080)) / 1080) and cropped["y"] == pytest.approx(0.4)
    fitted = pip.face_in_frame(face, 1920, 1080, 1080, 1920, (0.5, 0.5))                    # blurred borders: fitted inside
    assert fitted["x"] == pytest.approx(0.6) and fitted["size"] == pytest.approx(0.2 * 607.5 / 1920)
    assert pip.face_in_frame({**face, "ratio": 0.2}, 1080, 1920, 1080, 1920, (0.5, 0.5)) is None
    assert pip.face_in_frame(None, 1080, 1920, 1080, 1920, (0.5, 0.5)) is None


def test_spanish_names_of_the_new_options():
    assert legacy_es.config({"ventana": {"activo": True, "esquina": "abajo_izquierda", "tam": 0.3, "segundos_min": 2},
                             "guion": {"reencuadre_cada": 4, "deriva": 0.02, "apoyos": False, "primer_plano": 1.2}}) == {
        "pip": {"enabled": True, "corner": "bottom_left", "size": 0.3, "min_seconds": 2},
        "script": {"reframe_every": 4, "drift": 0.02, "support": False, "close_up": 1.2}}
    assert legacy_es.cue({"template": "apuntes", "elementos": "A; B", "tiempos": "0, 1", "enlazar": "si", "ventana": "no"}) == {
        "template": "callouts", "items": "A; B", "times": "0, 1", "link": "si", "pip": "no"}
    assert legacy_es.cue({"template": "vinetas"}) == {"template": "bullets"}


def test_check_does_not_take_a_reframing_cut_for_a_cut_of_the_sound(cfg):
    from social_video_editor.application.check import Checker

    summary = {"resolution": "540x960", "shot_details": [
        {"n": 1, "in_video": "00:00.00–00:05.00", "cut_sec": 5.0}, {"n": 2, "in_video": "00:05.00–00:09.00", "cut_sec": 9.0},
        {"n": 3, "in_video": "00:09.00–00:12.00", "cut_sec": 12.0}]}
    plan = {"shots": [{"source": 0, "start": 0.0, "end": 5.0}, {"source": 0, "start": 5.0, "end": 9.0, "continues": True},
                      {"source": 0, "start": 20.0, "end": 23.0}]}
    checker = Checker(None, {"config": cfg, "summary": summary, "plan": plan}, {})
    assert checker.cuts == [5.0, 9.0] and checker.sound_cuts() == [9.0]
    plan["shots"][1]["start"] = 6.0                     # edited by hand: the sound no longer goes straight on
    assert checker.sound_cuts() == [5.0, 9.0]


def test_a_sentence_never_ends_inside_a_stretch_of_speech():
    # the transcription split the first word ("Pre" + "paras") and heard "of loading screens" as "of loading sc reens":
    # the words said right against the edges are kept, so no word is chopped in half
    rec = [{"word": w, "start": a, "end": b, "prob": 1} for w, a, b in [
        ("Pre", 1.20, 1.68), ("paras", 1.68, 1.88), ("la", 1.88, 2.12), ("cena", 2.12, 2.42), ("para", 2.42, 2.80),
        ("los", 2.80, 3.06), ("invitados.", 3.06, 3.58),
        ("Two", 4.40, 4.60), ("hours", 4.60, 4.90), ("of", 4.90, 5.30), ("loading", 5.30, 5.90),
        ("sc", 5.90, 6.10), ("reens,", 6.10, 6.40),
        ("Luego", 7.00, 7.40), ("descansas.", 7.40, 7.80)]]
    first, second, third = sa.align(["Preparas la cena para los invitados.", "Two hours of loading screens.",
                                      "Luego descansas."], sa.transcript([{"words": rec}]), {})
    assert first["start"] < 1.20 and first["end"] == pytest.approx(3.58 + 0.12)
    assert second["end"] == pytest.approx(6.40 + 0.12)                   # "sc reens" is not cut off
    assert third["start"] == pytest.approx(7.00 - 0.08)                  # after a pause nothing is borrowed
    # a word that belongs to another sentence is never taken
    a, b = sa.align(["Tus amigos para cenar", "llegan ahora."],
                    sa.transcript([{"words": [{"word": w, "start": t, "end": t + 0.3, "prob": 1} for w, t in
                                              [("Tus", 0.0), ("amigos", 0.3), ("para", 0.6), ("cenar", 0.9),
                                               ("llegan", 1.2), ("ahora.", 1.5)]]}]), {})
    assert a["end"] <= b["start"] + 1e-9


def test_notes_on_the_camera_part_of_a_sentence_that_ends_in_footage():
    plan = _spoken("El procesador es como una cocina enorme donde trabajan muchos cocineros ocupados.", "Fin del video ahora.")
    s0 = plan["sentences"][0]
    cut_at = s0["words"][9][1]                                          # footage from "muchos"
    plan["cutaways"] = [{"type": "broll", "query": "q", "at_sentence": 0, "from_word": [0, cut_at], "_auto": "director"}]
    (group,) = [c for c in propose(plan, "es") if c["at_sentence"] == 0]
    assert group["until_sentence"] == 0 and group["items"]
    assert all(t < cut_at - 0.5 + 1e-6 for _, t in group["item_times"])   # only what is said before the footage
    covered = {**plan, "cutaways": [{**plan["cutaways"][0], "from_word": None}]}
    assert all(c["at_sentence"] != 0 for c in propose(covered, "es"))    # covered whole: nothing


def test_notes_around_the_presenter_end_when_a_full_frame_cutaway_comes_in():
    plan = _spoken("Uno dos tres cuatro cinco seis siete ocho nueve diez.", "Once doce trece catorce.")
    s0, s1 = plan["sentences"]
    plan["shots"] = [{"source": 0, "start": s0["start"], "end": s1["end"]}]
    tl = compute_timeline(plan, {"output": {"fps": 30}, "transitions": {"duration": 0.2}})
    plan["cutaways"] = [{"type": "graphic", "template": "callouts", "items": "A; B", "at_sentence": 0, "until_sentence": 1},
                        {"type": "photo", "file": "x.jpg", "at_sentence": 1}]
    placed, _ = resolve(plan, tl, 30)
    notes = next(c for c in placed if c.overlay)
    photo = next(c for c in placed if not c.overlay)
    assert notes.end == pytest.approx(photo.start, abs=1 / 30) and notes.start == pytest.approx(s0["start"], abs=0.05)
