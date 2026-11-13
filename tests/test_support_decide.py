"""The decider in the notes around the presenter (domain/support.py) and in shortened cards
(domain/review.shorten): synthetic sentences, fake deciders, no model."""
from __future__ import annotations

from fakes_decide import FakeDecider, FakeReasoner, decider_services
from social_video_editor.application import make
from social_video_editor.domain.decide import rules_only
from social_video_editor.domain.review import shorten
from social_video_editor.domain.support import propose, sentence_notes

VERB = "Los vecinos guardan 3 cajas en el sótano de la casa."        # "guardan" is a verb that reads as a long word
SAID = "Ellas guardan todo en el sótano."


def _words(text: str, start: float = 0.0, step: float = 0.4) -> list[list]:
    return [[w, round(start + i * step, 2), round(start + i * step + 0.3, 2)] for i, w in enumerate(text.split())]


def _spoken(*sentences, gap: float = 0.6) -> dict:
    plan, t = {"sentences": [], "cutaways": []}, 0.0
    for n, text in enumerate(sentences):
        words = _words(text, t, step=0.7)
        plan["sentences"].append({"n": n, "text": text, "source": 0, "start": t, "end": words[-1][2], "words": words})
        t = words[-1][2] + gap
    return plan


def _drop(word: str):
    return {"label_words": lambda q: [v for v in q.values() if v != word]}


def test_a_verb_the_decider_drops_is_not_a_note():
    rules = [n["label"] for n in sentence_notes(_words(VERB), "es")]
    assert "Guardan" in rules
    fake = FakeDecider(_drop("Guardan"))
    notes = sentence_notes(_words(VERB), "es", decide=fake)
    assert [n["label"] for n in notes] == [x for x in rules if x != "Guardan"]
    (q,) = fake.asked                                         # one question for the sentence
    assert q.topic == "label_words" and q.kind == "multi" and q.context == VERB and q.hypothesis is None
    assert q.values() == ("Vecinos", "Guardan") and q.default == ("Vecinos", "Guardan")   # a noun ending like a verb, a long word
    assert [o.text for o in q.options] == ["vecinos", "guardan"] and q.confidence == 0.5
    assert "sustantivo" in q.prompt


def test_sure_candidates_are_never_asked_about():
    fake = FakeDecider({"label_words": []})                   # the decider would drop every unsure word
    notes = sentence_notes(_words("La gente del mundo guarda 3 cajas en el sótano."), "es", decide=fake)
    kinds = {n["label"]: n["kind"] for n in notes}
    assert kinds == {"Gente": "icon", "Mundo": "icon", "3 cajas": "figure", "Sótano": "word"}
    for q in fake.asked:
        assert not {"Gente", "Mundo", "3 cajas", "Sótano"} & set(q.values())
    fake = FakeDecider({"label_words": []})
    english = sentence_notes(_words("The workers carry 5 boxes to their garage."), "en", decide=fake)
    assert [n["label"] for n in english] == ["Workers", "5 boxes", "Garage"] and fake.asked == []


def test_a_sentence_without_anything_else_asks_about_its_key_word():
    fake = FakeDecider({"label_words": []})
    assert sentence_notes(_words("Hay que traerlas luego."), "es", key_word=True, decide=fake) == []
    (q,) = fake.asked
    assert q.values() == ("Luego",) and q.confidence == 0.4
    assert [n["label"] for n in sentence_notes(_words("Hay que traerlas luego."), "es", key_word=True)] == ["Luego"]


def test_without_a_model_the_notes_are_the_rules_ones():
    plan = _spoken("Aquí empieza todo lo demás de hoy.", VERB, "Ellas esperan tranquilas junto al río.",
                   "The workers carry 5 boxes to their garage.")
    before = propose(plan, "es")
    assert before and propose(plan, "es", decide=rules_only) == before
    assert propose(plan, "es", decide=FakeDecider()) == before                     # no answer for the topic
    keep_all = FakeDecider({"label_words": lambda q: q.default})                   # a model that agrees
    assert propose(plan, "es", decide=keep_all) == before
    assert all("_decided" not in o for o in before + propose(plan, "es", decide=keep_all))
    for text in (VERB, "Ellas esperan tranquilas junto al río."):
        assert sentence_notes(_words(text), "es", decide=rules_only) == sentence_notes(_words(text), "es")


def test_one_question_per_sentence_the_same_on_every_pass_and_marked_when_it_changed():
    plan = _spoken("Aquí empieza todo lo demás de hoy.", SAID, "Ellos esperan tranquilos junto al río.")
    fake = FakeDecider(_drop("Guardan"))
    first = propose(plan, "es", decide=fake)
    contexts = [q.context for q in fake.asked if q.topic == "label_words"]
    assert len(contexts) == len(set(contexts)) <= 3
    keys = [q.key for q in fake.asked]
    fake.asked.clear()
    assert propose(plan, "es", decide=fake) == first and [q.key for q in fake.asked] == keys   # deterministic
    items = [x for o in first for x in o["items"]]
    assert "Guardan" not in items and "Guardan" in [x for o in propose(plan, "es") for x in o["items"]]
    assert any(o.get("_decided") == "llm" for o in first)


def test_shorten_offers_other_versions_and_takes_the_deciders_choice():
    spec = {"template": "title", "text": "Las ciudades crecen rápido, pero el agua no alcanza para todos"}
    rules = shorten(spec, 30)
    assert rules == {"text": "Las ciudades crecen rápido…"} and shorten(spec, 30, rules_only) == rules
    fake = FakeDecider({"shorten": lambda q: [v for v in q.values() if v.startswith("El agua")]})
    assert shorten(spec, 30, fake) == {"text": "El agua no alcanza para todos"}
    (q,) = fake.asked
    assert q.topic == "shorten" and q.kind == "choice" and q.context == spec["text"] and q.confidence == 0.6
    assert q.default == ("Las ciudades crecen rápido…",) and 2 <= len(q.options) <= 4
    assert all(len(v) <= 30 for v in q.values()) and q.prompt.startswith("¿Cuál")
    assert shorten(spec, 30, fake, language="en") and fake.asked[-1].prompt.startswith("Which")
    # nothing else to choose from: no question
    fake = FakeDecider({"shorten": lambda q: q.values()[-1]})
    assert shorten({"template": "list", "items": "uno; dos; tres; cuatro"}, 8, fake) == {"items": "uno; dos"}
    assert shorten({"template": "number", "sub": "al mes"}, 4, fake) == {"sub": ""} and fake.asked == []


def test_notes_proposed_again_by_make_go_through_the_decider(tmp_path):
    llm = FakeReasoner(answer=lambda prompt, keys: ([k for k in keys if f"{k}) guardan" not in prompt], 0.9))
    svc, project = decider_services(tmp_path, reasoner=llm)
    project.cfg["script"] = {"support": True}
    plan = _spoken("Aquí empieza todo lo demás de hoy.", SAID, "Y sigue así.")
    make._notes_again(svc, project, plan)
    items = [x for c in plan["cutaways"] for x in c["items"]]
    assert items and "Guardan" not in items and "Sótano" in items
    assert any(c.get("_decided") == "llm" for c in plan["cutaways"]) and len(llm.prompts) >= 1
    # without models it is the rules' proposal
    svc, project = decider_services(tmp_path / "off")
    project.cfg["decider"]["enabled"] = False
    project.cfg["script"] = {"support": True}
    plan = _spoken("Aquí empieza todo lo demás de hoy.", SAID, "Y sigue así.")
    make._notes_again(svc, project, plan)
    assert "Guardan" in [x for c in plan["cutaways"] for x in c["items"]]
