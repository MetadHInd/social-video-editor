"""The director's questions say where the drawing comes in, and may carry the script's topic
(domain/director.py, domain/decide.py)."""
from __future__ import annotations

from fakes_decide import FakeDecider

from social_video_editor.domain import decide as dd
from social_video_editor.domain import director


def sentence(text: str) -> dict:
    return {"words": [[w, float(i), float(i) + 0.3] for i, w in enumerate(text.split())], "start": 0.0, "text": text}


TEXT = "Si la masa se queda abierta toda la noche, al día siguiente cuesta amasarla un poco más."


def asked(fake: FakeDecider, topic: str) -> list[dd.Question]:
    return [q for q in fake.asked if q.topic == topic]


def test_title_and_card_questions_say_where_the_drawing_comes_in():
    fake = FakeDecider()
    director.card(sentence(TEXT), 9.0, "phone", decide=fake, language="es")
    (title, *_) = asked(fake, "title_span")
    assert "desde que dice «al día siguiente cuesta amasarla un…»" in title.prompt
    english = FakeDecider()
    director.phrase([(w, float(i)) for i, w in enumerate("If the dough stays open all night, the next day it is hard to knead.".split())],
                    7.0, decide=english, language="en")
    (q,) = asked(english, "title_span")
    assert q.prompt.startswith("A drawing covers the presenter from the moment he says “the next day it is hard…”")
    assert dd.ROUTES_VERSION >= 4                                          # cached answers are asked again


def test_the_topic_goes_only_to_the_chosen_questions(monkeypatch):
    fake = FakeDecider()
    director.card(sentence(TEXT), 9.0, "phone", decide=fake, language="es", about="Cómo hacer pan en casa.")
    assert not any("El video trata de" in q.prompt for q in fake.asked)     # off by default
    monkeypatch.setattr(director, "TOPIC_TOPICS", {"title_span"})
    topic = FakeDecider()
    director.card(sentence(TEXT), 9.0, "phone", decide=topic, language="es", about="Cómo hacer pan en casa.")
    assert all(q.prompt.startswith("El video trata de: «Cómo hacer pan en casa.»") for q in asked(topic, "title_span"))
    assert not any("El video trata de" in q.prompt for q in topic.asked if q.topic != "title_span")


def test_without_a_decider_the_card_is_the_same_with_or_without_a_topic():
    assert director.card(sentence(TEXT), 9.0, "phone") == director.card(sentence(TEXT), 9.0, "phone", about="Pan.")
