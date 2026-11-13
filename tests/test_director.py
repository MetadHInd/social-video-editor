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


@pytest.mark.parametrize("text, expected", [
    ("Si esperas un poco cada día son 70 segundos por semana, al año 1 hora mirando un reloj de arena.",
     {"template": "bars", "data": "Por semana:70, Al año:3600", "unit": "s"}),      # hours put in seconds
    ("Si esperas un poco cada día son 70 segundos por semana, al año 1 hora de espera.",
     {"template": "bars", "data": "Por semana:70, Al año:3600", "unit": "s"}),      # its own clause names it
    ("De 15 % antes a 40 % hoy.", {"template": "bars", "data": "Antes:15, Hoy:40", "unit": "%"}),
    ("Antes tardaba 3 segundos y ahora 1 segundo.", {"template": "bars", "data": "Antes:3, Ahora:1", "unit": "s"}),
    ("Un iPhone tiene 8 GB y un Android 12 GB.", {"template": "bars", "data": "iPhone:8, Android:12", "unit": "GB"}),
    ("The old phone took 4 seconds, the new one 2 seconds.",
     {"template": "bars", "data": "Old phone:4, New one:2", "unit": "s"}),
    ("Si esperas un poco cada día son 70 segundos por semana.", None),               # one figure: a number, not bars
    ("Abre 5 apps y luego otras 10 apps.", None),                                     # one after another, not compared
    ("Según la OMS, dormir bien mejora la memoria.",
     {"template": "headline", "text": "Dormir bien mejora la memoria", "outlet": "la OMS"}),
    ("Reuters publicó que los teléfonos nuevos duran más.",
     {"template": "headline", "text": "Los teléfonos nuevos duran más", "outlet": "Reuters"}),
    ("According to Reuters, new phones last longer this year.",
     {"template": "headline", "text": "New phones last longer this year", "outlet": "Reuters"}),
    ("Según tú, ¿vale la pena?", None),                                              # a source has a name
])
def test_data_said_aloud_is_drawn_as_data(text, expected):
    found = director.said_data(words_of(text))
    assert (found and {k: v for k, v in found.items() if k != "at"}) == expected


def test_a_comparison_is_shown_as_bars_when_it_is_said():
    text = "Antes tardaba 3 segundos en abrir y ahora tarda 1 segundo nada más."
    plan = plan_for(f"Close your apps to save the battery and make the phone faster, but you make it work harder.\n{text}\n"
                    "Do you close them?\n",
                    said(0.3, "Close your apps to save the battery and make the phone faster, but you make it work harder.", 0.4,
                         text, 0.3, "Do you close them?"))
    cuts = director.propose(plan, cfg(), OVERLAY)
    (bars,) = [c for c in cuts if c["at_sentence"] == 1]
    assert bars["type"] == "graphic" and bars["template"] == "bars" and bars["data"] == "Antes:3, Ahora:1"
    assert "query" not in bars and bars["_auto"] == "director" and "at" not in bars
    s1 = plan["sentences"][1]
    assert bars["from_word"][1] >= s1["start"] + 0.3 - 1e-6                    # the face is seen when the sentence starts


def test_with_no_word_in_reach_the_footage_covers_the_sentence_from_its_start():
    plan = plan_for("Apps sleep in memory, they wait on a table for you all day and night.\nLo vimos un minuto.\n"
                    "Do you close them?\n",
                    said(0.3, "Apps sleep in memory, they wait on a table for you all day and night.", 0.3,
                         "Lo", 0.6, "vimos un minuto.", 0.3, "Do you close them?"))
    s1 = plan["sentences"][1]                                   # 1.9 s, its second word too late for 1.8 s of footage
    cuts = [c for c in director.propose(plan, cfg(opening=5.5), OVERLAY) if c["at_sentence"] == 1]
    assert cuts and cuts[0]["from_word"][1] == s1["start"]


def test_data_comes_in_before_its_figure_when_the_face_has_had_its_time():
    text = "Si esperas un poco cada día son 70 segundos por semana, al año 1 hora de espera."
    plan = plan_for(f"Apps sleep in memory, they wait on a table for you all day and night.\n{text}\nDo you close them?\n",
                    said(0.3, "Apps sleep in memory, they wait on a table for you all day and night.", 0.3, text, 0.3,
                         "Do you close them?"))
    s0, s1 = plan["sentences"][0], plan["sentences"][1]
    (bars,) = [c for c in director.propose(plan, cfg(opening=5.5, max_camera=4), OVERLAY) if c["at_sentence"] == 1]
    figure_at = next(t for w, t, _ in s1["words"] if w == "70")
    at = bars["from_word"][1]
    assert at < figure_at and (s0["end"] - s0["start"]) + (at - s1["start"]) <= 5.5 + 0.4     # within the limit
    assert at in [t for _, t, _ in s1["words"]]                                              # at a word
