"""The decider: closed questions (domain/decide.py) and the cascade that answers them
(application/decide.py) with test doubles instead of models."""
from __future__ import annotations

import pytest

from fakes_decide import FakeClassifier, FakeDecider, FakeReasoner, FakeVisual, decider_services
from social_video_editor.adapters.files import legacy_es
from social_video_editor.application.decide import Decisions, decider_available, run_decided
from social_video_editor.domain import decide as dd

@pytest.fixture(autouse=True)
def routes(monkeypatch):
    """The cascade's mechanics are tested with routes of their own (the real table is measured and changes)."""
    monkeypatch.setattr(dd, "ROUTES", {"action": ("nli", "llm"), "title_span": ("llm",), "card_type": ("llm",)})


ACTIONS = (dd.Option("open", "abrir"), dd.Option("wait", "esperar"), dd.Option("none", "ninguna"))


def question(topic="action", kind="choice", default=("wait",), confidence=0.5, context="Volver a abrirla cuesta.",
             options=ACTIONS, hypothesis="Esta frase habla de {}."):
    return dd.Question(topic, kind, "¿Qué acción describe la frase?", options, context, tuple(default), confidence,
                       "es", hypothesis)


def test_answers_must_be_options():
    q = question()
    assert dd.validate(q, ["open"]) == ("open",)
    assert dd.validate(q, ["open", "wait"]) is None              # a choice has one answer
    assert dd.validate(q, ["fly"]) is None and dd.validate(q, 3) is None
    m = question(kind="multi", default=())
    assert dd.validate(m, ["none", "open"]) == ("open", "none")  # in the options' order
    assert dd.validate(m, []) == () and dd.validate(m, ["open", "open"]) is None
    assert dd.ask(None, q) == dd.Answer(("wait",), 0.5, "rules")
    assert dd.ask(lambda _: dd.Answer(("fly",), 0.99, "llm"), q).source == "rules"   # invalid → the rules


def test_question_identity_and_answers_round_trip():
    q = question()
    assert q.key == question().key and q.key != question(context="Otra frase.").key
    a = dd.Answer(("open",), 0.912345, "llm")
    assert dd.Answer.from_dict(a.as_dict()) == dd.Answer(("open",), 0.9123, "llm")
    assert dd.Answer.from_dict({"values": None}) is None and a.value == "open"


def test_settings_routes_and_requests():
    s = dd.settings({"decider": {"threshold": 0.7}})
    assert s["threshold"] == 0.7 and s["llm_model"] == "qwen3.5:9b" and s["enabled"] is False
    assert dd.needs(question(confidence=0.5), s) and not dd.needs(question(confidence=0.9), s)
    assert dd.stages(question()) == ("nli", "llm") and dd.stages(question(hypothesis=None)) == ("llm",)
    assert not dd.needs(question(topic="unknown"), s)
    prompt, keys, many = dd.llm_request(question())
    assert keys == ["a", "b", "c"] and not many
    assert "a) abrir" in prompt and "c) ninguna" in prompt and "Volver a abrirla cuesta." in prompt
    assert dd.from_keys(question(), ["B"]) == ("wait",) and dd.from_keys(question(), ["z"]) is None
    assert dd.nli_input(question()) == ("Volver a abrirla cuesta.", ["abrir", "esperar", "ninguna"],
                                        "Esta frase habla de {}.", False)
    assert legacy_es.config({"decisor": {"activado": "auto", "umbral": 0.7, "modelo_llm": "x"}}) == {
        "decider": {"enabled": "auto", "threshold": 0.7, "llm_model": "x"}}


def test_a_stage_only_replaces_a_less_sure_answer():
    s = dd.settings({})
    q, rules = question(), dd.rules_only(question())
    assert dd.pick(q, "nli", [0.7, 0.2, 0.1], None, s, rules) == dd.Answer(("open",), 0.7, "nli")
    assert dd.pick(q, "nli", [0.4, 0.35, 0.25], None, s, rules) is None          # less sure than the rules
    assert dd.pick(q, "nli", [0.9], None, s, rules) is None                       # wrong length
    assert dd.pick(q, "llm", ["a"], None, s, rules) == dd.Answer(("open",), 0.5, "llm")   # no confidence: llm_min
    assert dd.pick(q, "llm", ["a"], 0.3, s, rules) is None                        # under llm_min
    assert dd.pick(q, "llm", ["x"], 0.9, s, rules) is None
    m = question(kind="multi", default=())
    assert dd.pick(m, "nli", [0.9, 0.2, 0.6], None, s, dd.rules_only(m)) == dd.Answer(("open", "none"), 0.6, "nli")


def test_without_models_it_is_the_rules(tmp_path):
    svc, project = decider_services(tmp_path, FakeClassifier(reason="not installed"), FakeReasoner(reason="no server"))
    calls = []
    assert run_decided(svc, project, lambda d: calls.append(d(question())) or "done") == "done"
    assert calls == [dd.rules_only(question())] and decider_available(svc, project.cfg) == (False, False)
    project.cfg["decider"]["enabled"] = False                         # off: the models are not even asked
    off = FakeClassifier()
    svc.classifier = off
    assert decider_available(svc, project.cfg) == (False, False) and off.batches == []


def test_the_cascade_answers_in_batches_one_model_at_a_time_and_caches(tmp_path):
    order = []
    nli = FakeClassifier(score=lambda text, label: 0.95 if label == "abrir" else 0.02, log=order)
    llm = FakeReasoner(answer=lambda prompt, keys: (["b"], 0.9), log=order)
    svc, project = decider_services(tmp_path, nli, llm, FakeVisual(log=order))
    sure = question(topic="title_span", context="Una frase", default=("x",), confidence=0.95,
                    options=(dd.Option("x", "x"), dd.Option("y", "y")), hypothesis=None)
    by_llm = question(topic="card_type", context="Otra frase", default=("x",), confidence=0.4,
                      options=(dd.Option("x", "x"), dd.Option("y", "y")), hypothesis=None)

    def step(decide):
        return [decide(question()).value, decide(sure).value, decide(by_llm).value]

    assert run_decided(svc, project, step) == ["open", "x", "y"]
    assert len(nli.batches) == 1 and len(nli.batches[0]) == 1         # only the unsure question with a template
    assert len(llm.prompts) == 1                                       # the NLI was sure enough for the first one
    assert order == ["nli", "nli-release", "clip-release", "llm", "llm-release"]
    again = []
    svc.classifier, svc.reasoner = FakeClassifier(log=again), FakeReasoner(log=again)
    assert run_decided(svc, project, step) == ["open", "x", "y"] and again == []   # from the cache


def test_new_questions_after_a_decision_are_answered_too(tmp_path):
    llm = FakeReasoner(answer=lambda prompt, keys: (["a"], 0.9))
    svc, project = decider_services(tmp_path, FakeClassifier(reason="no"), llm)
    first = question(hypothesis=None)
    then = question(topic="title_span", context="Abrir", hypothesis=None, default=("none",),
                    options=(dd.Option("open", "Abrir"), dd.Option("none", "ninguna")))

    def step(decide):
        return decide(then).value if decide(first).value == "open" else "rules"

    assert run_decided(svc, project, step) == "open" and len(llm.prompts) == 2


def test_a_failing_model_leaves_the_rules_answer(tmp_path):
    def boom(prompt, keys):
        raise RuntimeError("server went away")
    svc, project = decider_services(tmp_path, FakeClassifier(reason="no"), FakeReasoner(answer=boom))
    d = Decisions(svc, project)
    assert d(question(hypothesis=None)).source == "rules" and d.settle() == 1
    assert d(question(hypothesis=None)) == dd.rules_only(question(hypothesis=None))
    assert svc.reasoner.released == 1


def test_the_fake_decider_answers_by_topic():
    fake = FakeDecider({"action": "open", "icon": lambda q: None})
    assert fake(question()) == dd.Answer(("open",), 0.95, "llm")
    assert fake(question(topic="icon")).source == "rules" and fake.topics() == ["action", "icon"]
    assert FakeDecider({"action": "fly"})(question()).source == "rules"


@pytest.mark.parametrize("value, expected", [(True, (True, True)), ("auto", (True, True)), ("no", (False, False))])
def test_enabled_values(tmp_path, value, expected):
    svc, project = decider_services(tmp_path, FakeClassifier(), FakeReasoner())
    project.cfg["decider"]["enabled"] = value
    assert decider_available(svc, project.cfg) == expected


def test_the_measured_routes(monkeypatch):
    monkeypatch.undo()
    assert dd.ROUTES["label_words"] == ("llm",) and dd.ROUTES["title_span"] == ("llm",)
    assert dd.ROUTES["action"] == () and dd.ROUTES["icon"] == () and dd.ROUTES["card_type"] == ()
    assert not any("nli" in r for r in dd.ROUTES.values())          # the small classifier is never loaded today


def test_a_failed_answer_never_empties_a_multi_question():
    s = dd.settings({})
    m = question(kind="multi", default=("open", "wait"), hypothesis=None)
    assert dd.pick(m, "llm", [], None, s, dd.rules_only(m)) is None          # a failure: the rules' answer stands
    prompt, keys, many = dd.llm_request(m)
    assert many and keys == ["a", "b", "c", "d"] and "d) ninguna de estas" in prompt
    assert dd.pick(m, "llm", ["d"], 0.9, s, dd.rules_only(m)) == dd.Answer((), 0.9, "llm")   # "none" is an answer
    assert dd.from_keys(m, ["a", "d"]) is None                                # none together with options: unclear
