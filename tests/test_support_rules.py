"""Notes around the presenter (domain/support.py): no question about a word a sure note already shows,
and (script.support_spread) the notes of words under a full-frame cutaway come in before it."""
from __future__ import annotations

from social_video_editor.domain import decide as dd
from social_video_editor.domain import support


def words(text: str, start: float = 0.0, step: float = 0.4) -> list[list]:
    return [[w, round(start + step * i, 3), round(start + step * i + 0.3, 3)] for i, w in enumerate(text.split())]


def offered(text: str) -> list[str]:
    rec = dd.Recorder()
    support._notes(words(text), "es", True, rec)
    return [o.value for q in rec.asked.values() for o in q.options]


def test_a_word_that_says_again_what_a_sure_note_shows_is_not_offered():
    text = "Tu teléfono se calienta y el celular se apaga."         # "celular": the same picture as "teléfono"
    labels = [n["label"] for n in support.sentence_notes(words(text), "es", True)]
    assert "Teléfono" in labels and "Celular" not in labels
    assert "Celular" not in offered(text)
    plural = "Abres una app y luego las apps se duermen rápido."    # "apps" after "app"
    assert not any(x.lower().startswith("app") for x in offered(plural))


def plan_with_a_cutaway_inside() -> dict:
    first = words("Hoy hablamos de algo que pasa en casa todos los días.", 0.0)
    second = words("La cocina de la casa guarda la sartén, el horno y la nevera.", 5.0)
    cut = second[8][1]                                             # a full-frame graphic from "el horno" on
    return {"sentences": [{"n": 0, "source": 0, "start": 0.0, "end": first[-1][2], "words": first,
                           "text": "Hoy hablamos de algo que pasa en casa todos los días."},
                          {"n": 1, "source": 0, "start": 5.0, "end": second[-1][2], "words": second,
                           "text": "La cocina de la casa guarda la sartén, el horno y la nevera."},
                          {"n": 2, "source": 0, "start": 10.0, "end": 12.0, "words": words("¿Y tú qué haces?", 10.0),
                           "text": "¿Y tú qué haces?"}],
            "cutaways": [{"type": "graphic", "template": "title", "text": "x", "at_sentence": 1, "until_sentence": 1,
                          "from_word": [0, cut]}]}


def notes_in(out: list[dict], begin: float, end: float) -> list[tuple[str, float]]:
    return [(item, t[1]) for c in out for item, t in zip(c["items"], c["item_times"]) if begin <= t[1] < end]


def test_by_default_the_notes_under_a_cutaway_are_left_out():
    plan = plan_with_a_cutaway_inside()
    out = support.propose(plan, "es")
    assert out == support.propose(plan, "es", settings={"script": {"support_spread": False}})
    cut = plan["cutaways"][0]["from_word"][1]
    assert notes_in(out, 5.0, 10.0) and not notes_in(out, cut, 10.0)


def test_spread_brings_the_covered_notes_into_the_part_on_camera():
    plan = plan_with_a_cutaway_inside()
    cut = plan["cutaways"][0]["from_word"][1]
    plain = support.propose(plan, "es")
    spread = support.propose(plan, "es", settings={"script": {"support_spread": True}})
    before, after = notes_in(plain, 5.0, 10.0), notes_in(spread, 5.0, 10.0)
    assert len(after) > len(before)
    assert {"Sartén", "Horno", "Nevera"} & {i for i, _ in after} - {i for i, _ in before}   # said under the cutaway
    times = sorted(t for _, t in after)
    assert all(5.0 <= t <= cut - support.LAST_SECONDS + 1e-6 for t in times)   # all on camera
    assert all(b - a >= support.MIN_GAP - 1e-6 for a, b in zip(times, times[1:]))
    assert len(after) <= support.MAX_PER_SENTENCE
    assert notes_in(spread, 0.0, 5.0) == notes_in(plain, 0.0, 5.0)  # the other sentences do not change
    assert notes_in(spread, 10.0, 99.0) == notes_in(plain, 10.0, 99.0)
