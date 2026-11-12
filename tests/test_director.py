"""The director (domain/director.py): footage where the presenter would stay alone on camera too long,
from the word being said, with an illustrated card as its fallback; and cutaways that start at a word."""
from __future__ import annotations

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.domain import director
from social_video_editor.domain.cutaways import resolve
from social_video_editor.domain.explainer import build_plan
from social_video_editor.domain.graphics import TEMPLATES
from social_video_editor.domain.script import parse_script
from social_video_editor.domain.script_align import align, transcript
from social_video_editor.domain.timeline import compute_timeline
from test_script import said

SCRIPT = """Close your apps to save the battery and make the phone faster, but you make it work harder.
[bars data="Before:1, After:2"]
It goes further than expected.
[camera]
Apps are asleep in memory, they wait on a table for you all day long.
That is why they come back at once and you never notice a thing.
Do you close them?
"""
RECORDING = said(0.3, "Close your apps to save the battery and make the phone faster, but you make it work harder.", 0.4,
                 "It goes further than expected.", 0.4,
                 "Apps are asleep in memory, they wait on a table for you all day long.", 0.3,
                 "That is why they come back at once and you never notice a thing.", 0.3, "Do you close them?")
OVERLAY = {name for name, t in TEMPLATES.items() if getattr(t, "overlay", False)}


def plan_for(text=SCRIPT, recording=RECORDING):
    script = parse_script(text, templates=set(TEMPLATES))
    return build_plan(script, align([s.text for s in script.sentences], transcript([{"words": recording}]), {}), {}, "script.md")


def cfg(**script):
    return {"script": {"auto_broll": True, **script}}


def test_the_face_opens_then_footage_cuts_in_at_a_named_thing():
    plan = plan_for()
    cuts = director.propose(plan, cfg(), OVERLAY)
    first = cuts[0]
    s0 = plan["sentences"][0]
    assert first["at_sentence"] == 0 and first["_auto"] == "director" and first["type"] == "broll"
    at = first["from_word"][1]
    assert at - s0["start"] >= 2.5 - 1e-6                               # the opening: the face first
    assert first["query"] in director.QUERIES["phone"]                  # "phone" is said right after the limit
    assert first["query_lang"] == "en" and first["said"] == s0["text"] and "phone" in first["query_local"]
    fallback = first["fallback"]                                        # drawn by the engine when no clip is good
    assert fallback["template"] == "scene" and fallback["text"] == director.phrase([(w, t) for w, t, _ in s0["words"]], at)
    # sentence 1 is under the bars: nothing there; the long camera stretch afterwards gets footage too
    assert all(c["at_sentence"] != 1 for c in cuts)
    assert any(c["at_sentence"] in (2, 3) for c in cuts)
    assert all(c["at_sentence"] != 4 for c in cuts)                     # the closing question stays on camera


def test_limits_and_a_short_sentence():
    plan = plan_for()
    assert director.propose(plan, cfg(max_camera=60, opening=60), OVERLAY) == []
    assert director.propose(plan, {"script": {}}, OVERLAY) == [] or director.settings({})["auto_broll"] is False
    many = director.propose(plan, cfg(max_camera=1.6, opening=1.6, auto_min=1.0), OVERLAY)
    usual = director.propose(plan, cfg(), OVERLAY)
    assert len(many) >= len(usual) and many[0]["from_word"][1] < usual[0]["from_word"][1]   # at most one per sentence, sooner
    for c in many:                                                     # never shorter than auto_min, the face first
        s = plan["sentences"][c["at_sentence"]]
        assert s["end"] - c["from_word"][1] >= 1.0 - 1e-6 and c["from_word"][1] - s["start"] >= 0.3 - 1e-6


def test_queries_come_from_the_sentence_and_change():
    plan = plan_for()
    cuts = director.propose(plan, cfg(max_camera=2.0, opening=2.0, auto_min=1.0), OVERLAY)
    memory = [c for c in cuts if c["at_sentence"] == 2]
    assert memory and memory[0]["query"] in director.QUERIES["app"] + director.QUERIES["ram"]
    queries = [c["query"] for c in cuts if c["type"] == "broll"]
    assert all(a != b for a, b in zip(queries, queries[1:]))           # the same picture does not come back right away
    assert director.topic(plan["sentences"]) in director.QUERIES


@pytest.mark.parametrize("text, cut, title", [
    ("Close your apps to save the battery and make it faster, but you make it work harder.", 11, "You make it work harder"),
    ("Apps are asleep in memory, they wait on a table.", 1, "Apps are asleep in memory"),
    ("La memoria es como una mesa, ahí esperan tus apps.", 5, "La memoria es como una mesa"),
    ("Entonces, ¿por qué se guardan tantas apps abiertas?", 0, "¿Por qué se guardan tantas apps…"),
    ("Cierras tus apps para ahorrar batería y que vaya rápido, pero lo pones a trabajar más.", 9,
     "Ahorrar batería y que vaya rápido"),
    ("Te leo.", 0, "Te leo"),
])
def test_the_card_says_the_clause(text, cut, title):
    words = [(w, float(i)) for i, w in enumerate(text.split())]
    assert director.phrase(words, float(cut)) == title


def test_card_for_a_sentence_and_what_covers_the_presenter():
    s = {"n": 0, "start": 0.0, "words": [["The", 0.0, 0.2], ["battery", 0.3, 0.5], ["drains", 0.6, 0.8], ["fast.", 0.9, 1.0]]}
    assert director.card(s) == {"template": "scene", "action": "drain", "text": "The battery drains fast"}
    assert director.card(s, last="scene") == {"template": "title", "text": "The battery drains fast", "icon": "battery"}
    assert director.card({"words": [["Hello", 0, 1], ["there.", 1, 2]]}, main="laptop")["icon"] == "laptop"
    plan = {"cutaways": [{"type": "graphic", "template": "lower_third", "text": "x", "at_sentence": 1},
                         {"type": "graphic", "template": "bars", "data": "A:1", "at_sentence": 2, "until_sentence": 3},
                         {"type": "broll", "query": "q", "at_sentence": 5}]}
    assert director.covered(plan, OVERLAY) == {2, 3, 5}


def test_a_cutaway_from_a_word_starts_when_it_is_said():
    plan = plan_for()
    tl = compute_timeline(plan, {"output": {"fps": 30}, "transitions": {"duration": 0.2}})
    s2 = plan["sentences"][2]
    word_t = s2["words"][4][1]
    plan["cutaways"] = [{"type": "photo", "file": "x.jpg", "at_sentence": 2, "until_sentence": 2,
                         "from_word": [s2["source"], word_t]}]
    (c,), warnings = resolve(plan, tl, 30)
    plain = dict(plan["cutaways"][0])
    plain.pop("from_word")
    (whole,), _ = resolve({**plan, "cutaways": [plain]}, tl, 30)
    assert not warnings and c.start == pytest.approx(whole.start + (word_t - s2["start"]), abs=1 / 30)
    assert c.end == whole.end and "from_word" not in c.spec


def test_spanish_names():
    assert legacy_es.config({"guion": {"tomas_auto": True, "apertura": 2, "camara_max": 3, "toma_min": 1.5}}) == {
        "script": {"auto_broll": True, "opening": 2, "max_camera": 3, "auto_min": 1.5}}
    assert legacy_es.plan({"tomas_encima": [{"tipo": "toma", "consulta": "x", "desde_palabra": [0, 1.5], "encaje": 0.6}]}) == {
        "cutaways": [{"type": "broll", "query": "x", "from_word": [0, 1.5], "fit": 0.6}]}


def words_of(text: str) -> list[tuple[str, float]]:
    return [(w, float(i)) for i, w in enumerate(text.split())]


@pytest.mark.parametrize("text, cut, expected", [
    ("Si cierras veinte al día son dos minutos por semana.", 0,
     {"template": "number", "value": "2", "unit": "minutos", "label": "Veinte al día son", "sub": "por semana"}),
    ("It takes 240 ms to go around the world.", 0,
     {"template": "number", "value": "240", "unit": "ms", "label": "It takes", "sub": "to go around the"}),
    ("No usan el procesador, casi no gastan batería y vuelven al instante.", 0,
     {"template": "list", "items": "No usan el procesador; Casi no gastan batería; Vuelven al instante", "marks": "n"}),
    ("Pero si las cierras, se van a la bodega y traerlas de vuelta cuesta.", 5,
     {"template": "scene", "action": "store", "text": "Bodega y traerlas de vuelta cuesta", "subject": "app",
      "target": "disk"}),
    ("La memoria es como una mesa, ahí esperan tus apps.", 5,
     {"template": "scene", "action": "wait", "text": "La memoria es como una mesa", "subject": "app"}),
    ("Tu celular ya sabe cuidarse.", 0, {"template": "title", "text": "Tu celular ya sabe cuidarse", "icon": "phone"}),
])
def test_what_the_engine_draws_follows_the_sentence(text, cut, expected):
    words = [[w, t, t + 0.3] for w, t in words_of(text)]
    assert director.card({"words": words, "start": 0.0, "text": text}, float(cut), "app") == expected


def test_figures_and_lists_are_read_carefully():
    assert director.figure(words_of("Al abrirla empieza de cero.")) is None                 # "de cero" is not a figure
    assert director.enumeration("Pero si las cierras, se van a la bodega y vuelven.") is None   # clauses, not items
    assert director.enumeration("Uno, dos") is None
    assert director.local_query(words_of("Pero si las cierras, se van a la bodega y traerlas de vuelta cuesta."), 0, None) == "bodega"
    assert director.local_query(words_of("Cierras tus apps para ahorrar batería"), 0, "app") == "apps bateria"


def test_a_figure_is_shown_not_searched():
    text = "Lo medí: veinte apps abiertas gastan dos por ciento de batería cada hora, todo el día, sin parar nunca."
    plan = plan_for(f"Close your apps to save the battery and make the phone faster, but you make it work harder.\n{text}\n"
                    "Do you close them?\n",
                    said(0.3, "Close your apps to save the battery and make the phone faster, but you make it work harder.", 0.4,
                         text, 0.3, "Do you close them?"))
    cuts = director.propose(plan, cfg(), OVERLAY)
    (number,) = [c for c in cuts if c["at_sentence"] == 1]
    assert number["type"] == "graphic" and number["template"] == "number" and (number["value"], number["unit"]) == ("20", "apps")
    assert "query" not in number and number["_auto"] == "director"
