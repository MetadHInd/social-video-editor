"""Formats: shorts cut from an explainer project (script marks, proposals, the short's plan), subtitles
drawn in the narration box, sources with no picture (a voice memo), files a graphic names and font
roles. Pure logic here; the renders are in tests/test_formats_render.py."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.application.cutaways import project_files
from social_video_editor.domain import caption_subtitles as cs
from social_video_editor.domain import shorts
from social_video_editor.domain.backdrop import Backdrop, has_picture
from social_video_editor.domain.explainer import build_plan
from social_video_editor.domain.graphics import TEMPLATES, RecordingCanvas, Stage, build_theme
from social_video_editor.domain.models import PlanError
from social_video_editor.domain.script import parse_script, short_name

SCRIPT = """Why do phones get slow? Nobody tells you.
[short memory]
How much memory does an app use?
[bars data="Before:15, Today:40" unit="%"]
About forty percent today.
It stays asleep in memory.
[/short]
[camera]
That is all for today.
"""


# ── script marks ─────────────────────────────────────────────────────────────
def test_short_marks_wrap_sentences_and_change_nothing_else():
    marked = parse_script(SCRIPT, templates=set(TEMPLATES))
    plain = parse_script("\n".join(x for x in SCRIPT.splitlines() if "short" not in x), templates=set(TEMPLATES))
    assert [(m.name, m.first, m.last) for m in marked.shorts] == [("memory", 2, 4)]
    assert [(s.text, s.cue) for s in marked.sentences] == [(s.text, s.cue) for s in plain.sentences]
    assert [c.spec for c in marked.cues] == [c.spec for c in plain.cues] and plain.shorts == []


def test_spanish_marks_names_and_a_short_left_open():
    script = parse_script("Hola.\n[corto nombre=\"Memoria RAM\"]\n¿Qué pasa?\nNada.\n[/corto]\n"
                          "Fin.\n[corto Último]\nAdiós.")
    assert [(m.name, m.first, m.last) for m in script.shorts] == [("memoria-ram", 1, 2), ("ultimo", 4, 4)]
    assert short_name("  Memoria  RAM! ") == "memoria-ram"


@pytest.mark.parametrize("text, message", [
    ("[/short]\nHello.", r"without a \[short name\]"),
    ("[short a]\nHello.\n[short b]\nBye.", "starts before the one of line 1 ends"),
    ("[short]\nHello.", "needs a name"),
    ("[short a]\n[/short]\nHello.", "has no sentences"),
    ("[short a]\nHi.\n[/short]\n[short A]\nBye.", "already a short called 'a'"),
])
def test_bad_short_marks_say_what_is_wrong(text, message):
    with pytest.raises(PlanError, match=message):
        parse_script(text)


def matches(n: int, length: float = 4.0, gap: float = 0.5) -> list[dict]:
    out, t = [], 1.0
    for _ in range(n):
        out.append({"source": 0, "start": round(t, 3), "end": round(t + length, 3), "score": 1.0, "takes": 1})
        t += length + gap
    return out


def test_the_plan_records_the_shorts_and_is_otherwise_the_same():
    marked = parse_script(SCRIPT, templates=set(TEMPLATES))
    plain = parse_script(SCRIPT.replace("[short memory]\n", "").replace("[/short]\n", ""), templates=set(TEMPLATES))
    a, b = build_plan(marked, matches(6), {}), build_plan(plain, matches(6), {})
    assert a.pop("shorts") == [{"name": "memory", "from_sentence": 2, "to_sentence": 4}]
    assert a == b and "shorts" not in b


# ── proposals ────────────────────────────────────────────────────────────────
def sentences(texts: list[str], seconds: float = 6.0) -> list[dict]:
    out, t = [], 0.0
    for n, text in enumerate(texts):
        out.append({"n": n, "text": text, "source": 0, "start": t, "end": t + seconds})
        t += seconds + 0.4
    return out


TALK = ["Hello and welcome.", "Today is a long day.", "Why are phones slow?", "Apps stay in memory.",
        "They wait there.", "It costs 2 seconds.", "So close fewer apps.", "Did you like it?", "Bye now.", "Really bye."]


def test_a_proposal_starts_at_a_question_and_says_why():
    found = shorts.propose(sentences(TALK), [{"type": "graphic", "template": "bars", "at_sentence": 5}],
                           {"script": {"shorts": "auto"}})
    best = found[0]
    assert best["_auto"] == "shorts" and best["name"] == "auto-1" and best["from_sentence"] == 2
    assert 20 <= best["seconds"] <= 60
    assert "starts with a question" in best["why"] and "has a bars graphic" in best["why"]
    assert all(TALK[p["from_sentence"]].endswith("?") for p in found)


def test_proposals_do_not_overlap_written_shorts_nor_each_other():
    taken = [{"name": "mine", "from_sentence": 2, "to_sentence": 6}]
    found = shorts.propose(sentences(TALK), [], {"script": {"shorts": "auto", "short_min": 10}}, taken)
    spans = [(p["from_sentence"], p["to_sentence"]) for p in found] + [(2, 6)]
    assert all(a[1] < b[0] or b[1] < a[0] for i, a in enumerate(spans) for b in spans[i + 1:])
    assert [p["from_sentence"] for p in found] == [7]


def test_no_opener_or_too_short_no_proposal():
    assert shorts.propose(sentences(["Plain words."] * 10), [], {"script": {"shorts": "auto"}}) == []
    assert shorts.propose(sentences(TALK, seconds=1.0), [], {"script": {"shorts": "auto"}}) == []
    assert not shorts.auto_on({}) and shorts.auto_on({"script": {"shorts": "auto"}})


def test_score_leaning_start_counts_against():
    s = sentences(["And why is that?", "Because.", "Fine.", "Why is that?", "Because.", "Fine."])
    leaning, _ = shorts.score(s, [], 0, 1)
    clean, _ = shorts.score(s, [], 3, 4)
    assert leaning == pytest.approx(clean - 1)


# ── the short's plan ─────────────────────────────────────────────────────────
def joined_plan() -> dict:
    sents = [{"n": 0, "text": "A.", "source": 0, "start": 1.0, "end": 3.0},
             {"n": 1, "text": "B?", "source": 0, "start": 2.95, "end": 6.0},
             {"n": 2, "text": "C.", "source": 0, "start": 6.4, "end": 9.0},
             {"n": 3, "text": "D.", "source": 0, "start": 9.3, "end": 12.0},
             {"n": 4, "text": "E.", "missing": True}]
    return {"note": "x", "script": "script.md", "sentences": sents, "texts": [{"text": "hi", "start": 0, "end": 1}],
            "shots": [{"source": 0, "start": 0.9, "end": 6.2, "sentences": [0, 1], "transition": "cut"},
                      {"source": 0, "start": 6.3, "end": 12.1, "sentences": [2, 3], "transition": "dissolve",
                       "continues": True}],
            "cutaways": [{"type": "graphic", "template": "bars", "at_sentence": 0, "until_sentence": 2,
                          "from_word": [0, 1.5], "item_times": [[0, 1.5]]},
                         {"type": "photo", "file": "a.png", "at_sentence": 3, "until_sentence": 3},
                         {"type": "graphic", "template": "lower_third", "text": "x", "start": 2.0, "end": 4.0}],
            "shorts": [{"name": "mid", "from_sentence": 1, "to_sentence": 2}]}


def test_short_plan_trims_shared_shots_halfway_and_clamps_cutaways():
    plan = joined_plan()
    out = shorts.short_plan(plan, shorts.find(plan, "MID"))
    assert [(s["start"], s["end"]) for s in out["shots"]] == [(2.975, 6.2), (6.3, 9.15)]
    assert out["shots"][0]["transition"] == "cut" and "continues" not in out["shots"][0]
    assert out["cutaways"] == [{"type": "graphic", "template": "bars", "at_sentence": 1, "until_sentence": 2}]
    assert out["texts"] == [] and "shorts" not in out and out["short"]["name"] == "mid"
    assert plan["shots"][0]["start"] == 0.9                       # the long plan is untouched


def test_unknown_or_empty_short_is_a_clear_error():
    plan = joined_plan()
    with pytest.raises(PlanError, match="no short 'nope'. Shorts: mid"):
        shorts.find(plan, "nope")
    with pytest.raises(PlanError, match="none of its sentences"):
        shorts.short_plan(plan, {"name": "gone", "from_sentence": 4, "to_sentence": 4})
    assert shorts.lines(plan) == ["  mid: sentences 1–2, ≈6 s"]


# ── subtitles in the narration box ───────────────────────────────────────────
GROUPS = [{"words": [{"word": "how", "start": 0.0, "end": 0.3}, {"word": "does", "start": 0.35, "end": 0.6},
                     {"word": "it", "start": 0.65, "end": 0.9}], "start": 0.0, "end": 1.0},
          {"words": [{"word": "travel?", "start": 1.2, "end": 1.6}], "start": 1.2, "end": 1.9}]


@pytest.mark.parametrize("W, H", [(1080, 1920), (1920, 1080)])
def test_caption_track_keeps_the_timing_and_stays_in_the_safe_zone(W, H):
    cfg = {"subtitles": {"y": 0.95, "size": 82, "uppercase": True}, "check": {}}
    spec = cs.track_spec(GROUPS, cfg, W, H, 2.0)
    track = cs.SubtitleTrack(spec, Stage(W, H, build_theme()))
    left, top, right, bottom = cs.safe_band(cfg, W, H)
    for x, y, w, h in track.boxes:                   # y 0.95 asked: kept above the bottom of the safe zone
        assert x >= left - 1 and x + w <= right + 1 and y >= top and y + h <= bottom + 0.5
    assert track.state(0.5) == (0, 1, 1.0) and track.state(1.1) is None and track.state(1.25)[0:2] == (1, 0)
    assert track.state(0.36)[2] < 1.0                # the band under "does" is still growing
    assert track.frame_state(0.5)[0] == track.frame_state(0.6)[0]      # same word, same drawing
    boxes = track.text_boxes()
    assert [b["text"] for b in boxes] == ["HOW DOES IT", "TRAVEL?"] and boxes[1]["start"] == 1.2
    cv = RecordingCanvas(W, H)
    track.draw(cv, 0.5)
    assert any(c[0] == "text" for c in cv.calls)


def test_caption_style_is_off_by_default_and_has_a_spanish_name():
    assert cs.style({}) == "pillow" and cs.style({"subtitles": {"style": "caption"}}) == "caption"
    assert cs.style({"subtitles": {"style": "odd"}}) == "pillow"
    assert legacy_es.config({"subtitulos": {"estilo": "caja"}})["subtitles"]["style"] == "caption"
    assert legacy_es.config({"guion": {"cortos": "auto"}})["script"]["shorts"] == "auto"


def test_landscape_pillow_subtitles_are_raised_into_the_safe_zone():
    from PIL import Image

    from social_video_editor.adapters.pillow.text_layer import subtitle_y

    im = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    im.paste((255, 255, 255, 255), (10, 10, 390, 90))
    sub = {"y": 0.95}
    assert subtitle_y(im, sub, {}, 1080, 1920, True) == pytest.approx(0.95 * 1920)        # vertical: as set
    y = subtitle_y(im, sub, {}, 1920, 1080, True)
    assert y + 40 * 1.1 <= 0.90 * 1080 and y < 0.95 * 1080
    assert subtitle_y(im, {"y": 0.835}, {}, 1920, 1080, True) == pytest.approx(0.835 * 1080)   # fits: unchanged
    assert subtitle_y(im, {"y": 0.5}, {}, 1920, 1080, False) == pytest.approx(540)


# ── a source with no picture ─────────────────────────────────────────────────
def test_a_voice_memo_has_no_picture_and_gets_the_backdrop():
    assert not has_picture({"path": "memo.m4a", "has_audio": True, "duration": 30})
    assert has_picture({"width": 1080, "height": 1920, "has_audio": True}) and has_picture({"image": True})
    assert has_picture({"path": "x.mp4", "has_audio": False})          # nothing known: treated as a video
    scene = Backdrop({"dur": 2.0}, Stage(1080, 1920, build_theme()))
    cv = RecordingCanvas(1080, 1920)
    scene.paint(cv, 0.5)
    assert cv.calls and scene.sounds() == [] and not scene.overlay      # the scenery, nothing on it


# ── files a graphic names ────────────────────────────────────────────────────
def test_graphic_files_resolve_in_the_project_folder(tmp_path):
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "settings.png").write_bytes(b"x")
    svc = SimpleNamespace(projects=SimpleNamespace(exists=lambda p: Path(p).exists()))
    spec = {"template": "screen", "image": "shots/settings.png"}
    assert project_files(svc, tmp_path, spec)["image"] == str(tmp_path / "shots" / "settings.png")
    assert spec["image"] == "shots/settings.png"                          # not changed in place
    for kept in ("@sample", "/abs/elsewhere.png", "missing.png", ""):
        assert project_files(svc, tmp_path, {"image": kept})["image"] == kept
    assert project_files(svc, None, spec) is spec


# ── font roles ───────────────────────────────────────────────────────────────
class FakeSkia:
    """Just enough of skia for FontBook: typefaces are the names of the files / families found."""

    def __init__(self, families: set[str]):
        families_ = families

        class Typeface:
            @staticmethod
            def MakeFromFile(path):
                return f"file:{Path(path).stem}"

            def __new__(cls, family, style=None):
                return f"family:{family}"

        class FontMgr:
            @staticmethod
            def RefDefault():
                return SimpleNamespace(matchFamilyStyle=lambda name, style: f"family:{name}" if name in families_ else None)

        self.Typeface, self.FontMgr = Typeface, FontMgr
        self.FontStyle = SimpleNamespace(Normal=lambda: None, Bold=lambda: None)


def test_a_font_role_may_be_a_list_and_mono_never_falls_back_to_a_proportional_font(monkeypatch):
    from social_video_editor.adapters.skia import renderer

    files = {"DejaVuSansMono": Path("/fonts/DejaVuSansMono.ttf"), "Brand": Path("/fonts/Brand.ttf")}
    monkeypatch.setattr(renderer, "font_file_exact", lambda name: files.get(name))
    monkeypatch.setattr(renderer, "find_font_file", lambda name: (Path("/fonts/ArialBlack.ttf"), 0))
    book = renderer.FontBook(FakeSkia(set()), {"label": ["Missing", "Brand"], "mono": "Menlo", "display": "Gone"})
    assert book.face("label") == "file:Brand"
    assert book.face("mono") == "file:DejaVuSansMono"                    # Menlo missing (Linux): a monospace one
    assert book.face("display") == "file:ArialBlack"                     # any other role: the bold fallback
    assert book.candidates("mono")[:2] == ["Menlo", "Menlo"] and "Courier New" in book.candidates("mono")
    windows = renderer.FontBook(FakeSkia({"Consolas"}), {"mono": ["Menlo"]})
    monkeypatch.setattr(renderer, "font_file_exact", lambda name: None)
    assert windows.face("mono") == "family:Consolas"


# ── subtitles across a transition ────────────────────────────────────────────
SAID = [("How", 1.00), ("does", 1.30), ("it", 1.60), ("travel?", 1.90),            # sentence 0
        ("And", 6.00), ("they", 6.30), ("travel", 6.60), ("apart.", 6.90)]          # sentence 1, another take


def _groups(plan: dict, words: list[tuple[str, float]], transition: str = "dissolve") -> list[str]:
    from social_video_editor.domain.text_layout import subtitle_groups
    from social_video_editor.domain.timeline import compute_timeline

    cfg = {"output": {"fps": 30}, "transitions": {"duration": 0.2},
           "subtitles": {"max_words": 3, "max_chars": 18}}
    analyses = [{"words": [{"word": w, "start": t, "end": t + 0.25} for w, t in words]}]
    tl = compute_timeline(plan, cfg)
    return [w["word"] for g in subtitle_groups(analyses, tl, cfg) for w in g["words"]]


def test_a_sentence_that_starts_under_a_dissolve_keeps_its_first_word():
    # as build_plan writes it: each side of the 0.2 s dissolve gets 0.1 s more, sentences keep 0.08 s of air
    plan = {"shots": [{"source": 0, "start": 0.82, "end": 2.37, "transition": "cut"},
                      {"source": 0, "start": 5.82, "end": 7.37, "transition": "dissolve"}], "texts": []}
    assert _groups(plan, SAID) == [w for w, _ in SAID]           # every word, in order, once
    plan["shots"][1]["transition"] = "cut"
    assert _groups(plan, SAID) == [w for w, _ in SAID]


def test_one_take_crossfaded_into_itself_writes_each_word_once():
    # a continuous take split in two shots that share 0.2 s of the recording (a dissolve inside one take)
    words = [("one", 1.0), ("two", 1.4), ("three", 1.8), ("four", 2.05), ("five", 2.4), ("six", 2.8)]
    plan = {"shots": [{"source": 0, "start": 0.9, "end": 2.2, "transition": "cut"},
                      {"source": 0, "start": 2.0, "end": 3.2, "transition": "dissolve"}], "texts": []}
    assert _groups(plan, words) == [w for w, _ in words]
