"""The director's language judgments as closed questions (domain/director.py with domain/decide.py):
without a decider, or with one that answers like the rules, nothing changes; a model's answer changes
only what it was asked about, and the proposal says so. Synthetic sentences, no model is loaded."""
from __future__ import annotations

import re

import pytest

from fakes_decide import FakeDecider, FakeReasoner, decider_services
from social_video_editor.application.decide import run_decided
from social_video_editor.domain import decide as dd
from social_video_editor.domain import director
from social_video_editor.domain.explainer import build_plan
from social_video_editor.domain.graphics import TEMPLATES
from social_video_editor.domain.graphics.icons import ICONS, describe_icon, match_icon
from social_video_editor.domain.graphics.scenes import ACTIONS, action_of, action_text, actions_in
from social_video_editor.domain.script import parse_script
from social_video_editor.domain.script_align import align, transcript
from test_script import said

OVERLAY = {name for name, t in TEMPLATES.items() if getattr(t, "overlay", False)}
BREAD = ["Hoy cocinamos un pan casero con harina, agua y sal, y lo dejamos reposar toda la noche.",
         "Si la masa se queda abierta toda la noche, al día siguiente cuesta amasarla un poco más.",
         "El sol de abril calienta la cocina y la masa sube sola sobre la mesa de madera.",
         "¿Lo vas a probar?"]
SENTENCES = [
    ("Si la masa se queda abierta toda la noche, al día siguiente cuesta amasarla.", 3),
    ("La masa sube en el horno y la cocina huele a pan.", 0),
    ("Primero lavas el arroz, lo dejas reposar y después hay que esperar diez minutos.", 6),
    ("El sol de abril calienta la arena de la playa.", 0),
    ("It took 4 hours by train and the bus 9 hours.", 0),
    ("We packed the bags early and the train was waiting at the station.", 2),
    ("Marcamos 3 goles y ellos 1 goles.", 0),
]


def words_of(text: str) -> list[tuple[str, float]]:
    return [(w, float(i)) for i, w in enumerate(text.split())]


def sentence(text: str) -> dict:
    return {"words": [[w, t, t + 0.3] for w, t in words_of(text)], "start": 0.0, "text": text}


def bread_plan() -> dict:
    script = parse_script("\n".join(BREAD) + "\n", templates=set(TEMPLATES))
    recording = said(0.3, BREAD[0], 0.4, BREAD[1], 0.4, BREAD[2], 0.3, BREAD[3])
    return build_plan(script, align([s.text for s in script.sentences], transcript([{"words": recording}]), {}), {},
                      "script.md")


CFG = {"script": {"auto_broll": True}, "language": "es"}


def asked(fake: FakeDecider, topic: str) -> list[dd.Question]:
    return [q for q in fake.asked if q.topic == topic]


# ── without a model nothing changes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, cut", SENTENCES)
def test_answers_like_the_rules_change_nothing(text, cut):
    for decide in (FakeDecider(), dd.rules_only, dd.Recorder()):
        assert director.card(sentence(text), float(cut), "phone", decide=decide) == \
            director.card(sentence(text), float(cut), "phone")
        assert director.said_data(words_of(text), decide) == director.said_data(words_of(text))
        assert director.phrase(words_of(text), float(cut), decide=decide) == director.phrase(words_of(text), float(cut))


def test_a_plan_without_model_answers_is_the_same():
    plan = bread_plan()
    rules = director.propose(plan, CFG, OVERLAY)
    fake = FakeDecider()
    assert director.propose(plan, CFG, OVERLAY, decide=fake) == rules and rules
    assert {"title_span", "icon", "action"} <= set(fake.topics())
    assert not any("_decided" in c or "_decided" in (c.get("fallback") or {}) for c in rules)


def test_the_same_questions_on_every_pass():
    plan = bread_plan()
    fake = FakeDecider({"action": "none", "title_span": lambda q: q.options[-1].value})
    first = director.propose(plan, CFG, OVERLAY, decide=fake)
    keys = [q.key for q in fake.asked]
    fake.asked.clear()
    assert director.propose(plan, CFG, OVERLAY, decide=fake) == first
    assert [q.key for q in fake.asked] == keys
    every = [q for q in fake.asked]
    assert all(dd.validate(q, q.default) == q.default for q in every)              # the rules' answer is an option
    assert all(q.topic in dd.ROUTES and 0 < q.confidence <= 1 for q in every)


# ── each judgment ──────────────────────────────────────────────────────────────────────────────

def test_an_action_answer_changes_the_scene():
    text = "Si la masa se queda abierta toda la noche, al día siguiente cuesta amasarla."
    rules = director.card(sentence(text), 3.0, "phone")
    assert rules["template"] == "scene" and rules["action"] == "wait"              # "cuesta" read as waiting
    fake = FakeDecider({"action": "sleep"})
    got = director.card(sentence(text), 3.0, "phone", decide=fake)
    assert got["template"] == "scene" and got["action"] == "sleep" and got["_decided"] == "llm"
    (q,) = asked(fake, "action")
    assert q.values() == (*ACTIONS, "none") and q.default == ("wait",) and q.confidence == 0.5   # not a plain verb
    assert q.hypothesis == "Esta frase habla de {}." and q.options[-1].text == action_text("none", "es")
    assert dict((o.value, o.text) for o in q.options)["wait"] == "esperar o tardar" and q.language == "es"


def test_none_removes_the_scene():
    text = "El sol de abril calienta la arena de la playa."
    assert director.card(sentence(text), 0.0, "phone")["action"] == "open"          # "abril" starts like "abrir"
    fake = FakeDecider({"action": "none"})
    got = director.card(sentence(text), 0.0, "phone", decide=fake)
    assert got["template"] == "title" and got["_decided"] == "llm"
    assert director.scene_for(words_of(text), 0.0, "phone", FakeDecider({"action": "none"})) is None
    assert not any("scene" in q.values() for q in asked(fake, "card_type"))


def test_a_plain_verb_is_surer_and_english_is_asked_in_english():
    fake = FakeDecider()
    director.scene_for(words_of("We packed the bags early and the train was waiting at the station."), 2.0, "phone", fake)
    (q,) = asked(fake, "action")
    assert q.default == ("wait",) and q.confidence == 0.85 and q.language == "en"
    assert q.hypothesis == "This sentence is about {}." and dict((o.value, o.text) for o in q.options)["open"] == \
        "opening or starting something"
    assert [(a, c) for a, _, c in actions_in("hay que guardarla y cuesta".split())] == [("store", True), ("wait", False)]
    assert action_of("hay que guardarla".split()) == ("store", 2)


def test_the_title_uses_the_chosen_span():
    text = "La masa sube en el horno y la cocina huele a pan."
    assert director.card(sentence(text), 0.0, "phone")["text"] == "La cocina huele a pan"
    fake = FakeDecider({"title_span": "La masa sube en el horno…"})
    got = director.card(sentence(text), 0.0, "phone", decide=fake)
    assert got == {"template": "title", "text": "La masa sube en el horno…", "icon": "phone", "_decided": "llm"}
    q = asked(fake, "title_span")[0]
    assert q.default == ("La cocina huele a pan",) and 2 <= len(q.options) <= 4 and q.hypothesis is None
    assert director.phrase(words_of(text), 0.0, decide=fake) == "La masa sube en el horno…"


def test_a_long_clause_offers_its_parts():
    text = "Si la masa se queda abierta toda la noche, al día siguiente cuesta amasarla un poco más."
    fake = FakeDecider()
    director.phrase(words_of(text), 12.0, decide=fake)
    (q,) = asked(fake, "title_span")
    assert q.confidence == 0.5 and "Siguiente cuesta amasarla un poco más" in q.default[0]       # cut short
    assert "Si la masa se queda abierta…" in q.values()                                         # the clause before
    short = FakeDecider()
    director.phrase(words_of("La cocina huele a pan."), 0.0, decide=short)
    assert not short.asked                                                  # one span only: nothing to ask


def test_a_weak_bar_label_is_replaced():
    words = words_of("It took 4 hours by train and the bus 9 hours.")
    assert director.comparison(words)["data"] == "It:4, And the bus:9"
    fake = FakeDecider({"bar_labels": lambda q: "By train" if q.default == ("It",) else None})
    got = director.comparison(words, fake)
    assert got["data"] == "By train:4, Bus:9" and got["_decided"] == "llm" and got["unit"] == "h"
    (first,) = asked(fake, "bar_labels")                # the next bar no longer reaches back to "by train"
    assert first.confidence == 0.3 and "4 hours" in first.prompt and first.default == ("It",)
    assert "By" not in first.values()                                       # a preposition alone names nothing
    rules = FakeDecider()
    director.comparison(words, rules)
    assert [(q.default, q.confidence) for q in asked(rules, "bar_labels")] == [(("It",), 0.3), (("And the bus",), 0.5)]


def test_figures_the_rules_do_not_compare_may_be_compared():
    words = words_of("Marcamos 3 goles y ellos 1 goles.")
    assert director.said_data(words) is None
    fake = FakeDecider({"is_comparison": "yes"})
    got = director.said_data(words, fake)
    assert got["template"] == "bars" and got["data"] == "Marcamos:3, Ellos:1" and got["_decided"] == "llm"
    (q,) = asked(fake, "is_comparison")
    assert q.kind == "yesno" and q.default == ("no",) and q.confidence == 0.5 and q.hypothesis == "Esta frase {}."
    sure = FakeDecider({"is_comparison": "no"})
    assert director.said_data(words_of("De 15 % antes a 40 % hoy."), sure)["data"] == "Antes:15, Hoy:40"
    assert not sure.asked                                                   # "antes / hoy": the rules are sure


def test_the_card_type_answer_picks_the_drawing():
    text = "Primero lavas el arroz, lo dejas reposar y después hay que esperar diez minutos."
    assert director.card(sentence(text), 6.0, "phone")["template"] == "number"
    fake = FakeDecider({"card_type": "scene"})
    got = director.card(sentence(text), 6.0, "phone", decide=fake)
    assert got["template"] == "scene" and got["action"] == "wait" and got["_decided"] == "llm"
    (q,) = asked(fake, "card_type")
    assert q.values() == ("number", "scene", "title") and q.default == ("number",) and q.confidence == 0.6
    texts = [o.text for o in q.options]
    assert "10" in texts[0] and "minutos" in texts[0] and texts[1].startswith("una escena animada")
    assert texts[2].startswith("un título: «")


def test_the_title_icon_may_be_none():
    text = "La masa sube en el horno y la cocina huele a pan."
    fake = FakeDecider({"icon": "none"})
    got = director.card(sentence(text), 0.0, "phone", decide=fake)
    assert got == {"template": "title", "text": "La cocina huele a pan", "_decided": "llm"}
    (q,) = asked(fake, "icon")
    assert q.values() == (*ICONS, "none") and q.default == ("phone",) and q.confidence == 0.5   # nothing named
    assert dict((o.value, o.text) for o in q.options)["battery"] == describe_icon("battery", "es")
    named = FakeDecider()
    director.card(sentence("Lleva la batería a la playa."), 0.0, "phone", decide=named)
    assert asked(named, "icon")[0].confidence == 0.9 and asked(named, "icon")[0].default == ("battery",)
    assert match_icon("baterías") == ("battery", False) and match_icon("cocina") is None
    assert describe_icon("none", "en") == "none of these things"


# ── in a plan ──────────────────────────────────────────────────────────────────────────────────

def test_proposals_say_a_model_changed_them():
    plan = bread_plan()
    rules = director.propose(plan, CFG, OVERLAY)
    got = director.propose(plan, CFG, OVERLAY, decide=FakeDecider({"action": "none"}, source="nli"))
    before = {c["at_sentence"]: c for c in rules}
    changed = [c for c in got if c["fallback"] != before[c["at_sentence"]]["fallback"]]
    assert changed and all(c["_decided"] == "nli" and c["fallback"]["_decided"] == "nli" for c in changed)
    assert all("_decided" not in c for c in got if c not in changed)


def test_the_cascade_runs_the_director_until_its_answers_settle(tmp_path):
    def none_for_actions(prompt: str, keys: list[str]):
        letter = re.search(r"^([a-z])\) ninguna de estas acciones$", prompt, re.M)
        return ([letter.group(1)] if letter else []), 0.9

    reasoner = FakeReasoner(answer=none_for_actions)
    svc, project = decider_services(tmp_path, reasoner=reasoner)
    plan = bread_plan()
    got = run_decided(svc, project, lambda d: director.propose(plan, CFG, OVERLAY, decide=d))
    assert all(c["fallback"]["template"] != "scene" for c in got)
    assert any(c.get("_decided") == "llm" for c in got) and reasoner.released >= 1
