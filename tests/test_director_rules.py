"""The director's hand rules read lists, figures, bars and quotes carefully (domain/director.py).

Every sentence here is synthetic and neutral."""
from __future__ import annotations

import pytest

from social_video_editor.domain import director
from social_video_editor.domain.support import sentence_notes


def words_of(text: str) -> list[tuple[str, float]]:
    return [(w, float(i)) for i, w in enumerate(text.split())]


def data(text: str) -> dict | None:
    found = director.said_data(words_of(text))
    return found and {k: v for k, v in found.items() if k != "at"}


# ── lists ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, items", [
    ("Necesitas una pantalla, un teclado y un ratón.", ["Una pantalla", "Un teclado", "Un ratón"]),
    ("Revisa el micrófono, la cámara, la ubicación y los contactos.",
     ["El micrófono", "La cámara", "La ubicación", "Los contactos"]),
    ("You need a screen, a keyboard, and a mouse.", ["A screen", "A keyboard", "A mouse"]),
    ("Rojo, verde, azul o negro.", ["Rojo", "Verde", "Azul", "Negro"]),
    ("No usan el procesador, casi no gastan batería y vuelven al instante.",
     ["No usan el procesador", "Casi no gastan batería", "Vuelven al instante"]),
    ("Son baratas y casi no pesan, por eso se venden rápido.",                 # "A y B, por eso C"
     ["Son baratas", "Casi no pesan", "Por eso se venden rápido"]),
])
def test_a_list_is_items_with_commas_and_one_last_join(text, items):
    assert director.enumeration(text) == items


@pytest.mark.parametrize("text", [
    "Oíste el timbre, pero no sonó, abres la puerta y nada, y ni siquiera esperabas a nadie.",   # clauses with "pero"
    "Por eso si llueve, cualquier ruido suena a trueno, es como esperar un tren, cada coche parece el tuyo.",
    "Parece cansancio o tener hambre, así que ya sabes, la planta no se secó.",
    "Lo pesé y son dos kilos por bolsa y tres kilos por caja.",                # no commas: "A y B y C"
    "You heard it ring, but it did not, you look and nothing, and you were not even near it.",
    "Pero si las guardas, se van al cajón y vuelven.",
    "Uno, dos",
])
def test_clauses_one_after_another_are_not_a_list(text):
    assert director.enumeration(text) is None


# ── figures ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, value, unit", [
    ("Hay 17 mil libros en la biblioteca.", "17000", "libros"),
    ("There are 17 thousand books in the library.", "17000", "books"),
    ("Se venden ciento sesenta y uno panes al día.", "161", "panes"),
    ("Son cincuenta y cuatro sillas en la sala.", "54", "sillas"),
    ("It takes one hundred and sixty-one seconds to boil.", "161", "seconds"),
    ("There are fifty-four chairs in the room.", "54", "chairs"),
    ("Mil sillas plegables.", "1000", "sillas"),
    ("Dos mil quinientos pasos al día.", "2500", "pasos"),
    ("Lo pesé: un kilo más cada vez.", None, None),                       # "un kilo": not a unit the engine knows
    ("Lo medí: un segundo más cada vez.", "1", "segundo"),               # "un" with a unit is a figure
    ("Son 2,5 horas de viaje.", "2,5", "horas"),
])
def test_figures_said_in_words_and_with_thousands(text, value, unit):
    got = director.figure(words_of(text))
    assert (got and (got["value"], got.get("unit"))) == ((value, unit) if value else None)


@pytest.mark.parametrize("text", ["Al abrirla empieza de cero.", "Tienes un problema con la red.",
                                  "Pagaron millones por la casa.", "No encontraron ni uno."])
def test_un_uno_cero_and_millions_alone_are_not_figures(text):
    assert director.figure(words_of(text)) is None


def test_spelled_numbers_stop_where_the_number_stops():
    said = "cinco y seis sillas".split()
    assert director.spelled(said, 0) == (5, 1)                          # "cinco y seis" is two numbers
    assert director.spelled("treinta y dos sillas".split(), 0) == (32, 3)
    assert director.spelled("veinte, y dos más".split(), 0) == (20, 1)  # a clause ends the number
    assert director.spelled("sillas".split(), 0) is None


def test_a_figure_keeps_no_trailing_comma():
    got = director.figure(words_of("Tiene 10, quizá 12 sillas libres."))
    assert got["value"] == "12" and "," not in got.get("label", "")
    alone = director.figure(words_of("Llegaron 10, nada más."))
    assert alone["value"] == "10"
    notes = sentence_notes([[w, t, t + 0.3] for w, t in words_of("Llegaron 10, nada más.")])
    assert [n["label"] for n in notes if n["kind"] == "figure"] == ["10"]


# ── bars ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Caminas dos horas al día y diez minutos a la semana en el parque.",           # a period names each bar
     {"template": "bars", "data": "Al día:120, A la semana:10", "unit": "min"}),
    ("Si riegas veinte macetas son dos minutos por semana y dos horas al año.",
     {"template": "bars", "data": "Por semana:2, Al año:120", "unit": "min"}),
    ("La cocina tiene ocho metros y la sala tiene doce metros.",
     {"template": "bars", "data": "Cocina:8, Sala:12", "unit": "metros"}),
    ("Lo herví dos minutos y después lo dejé cinco minutos seguidos.", None),       # "Lo herví" names nothing
    ("Duermes ocho horas y pasas cinco horas en el tren.", None),                    # "Duermes" is not a thing
    ("It uses 30 % and the heater 50 %.", None),                                     # "It" names nothing
    ("Twenty a day is 2 minutes, a year is 2 hours.", None),                         # a figure is not a name
])
def test_a_bar_is_named_by_the_thing_measured_or_not_drawn(text, expected):
    assert data(text) == expected


def test_a_bar_without_a_name_leaves_the_figure_card():
    text = "Duermes ocho horas y pasas cinco horas en el tren."
    words = [[w, t, t + 0.3] for w, t in words_of(text)]
    got = director.card({"words": words, "start": 0.0, "text": text}, 0.0, "phone")
    assert got["template"] == "number" and got["value"] == "8"


# ── quotes ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, outlet, said", [
    ("La OMS dice que dormir bien mejora la memoria.", "La OMS", "Dormir bien mejora la memoria"),
    ("De acuerdo con la NASA, el polvo cubre los paneles cada año.", "la NASA", "El polvo cubre los paneles cada año"),
    ("Un estudio de Stanford encontró que los perros duermen más en invierno.", "Stanford",
     "Los perros duermen más en invierno"),
    ("Un estudio de la Universidad de Oxford encontró que caminar ayuda mucho.", "Universidad de Oxford",
     "Caminar ayuda mucho"),
    ("Reuters reported that new trains run faster this year.", "Reuters", "New trains run faster this year"),
    ("A study by Stanford found that dogs sleep more in winter.", "Stanford", "Dogs sleep more in winter"),
    ("La ONU afirma que el agua dulce escasea cada vez más.", "La ONU", "El agua dulce escasea cada vez más"),
])
def test_a_named_source_is_quoted(text, outlet, said):
    got = data(text)
    assert got == {"template": "headline", "text": said, "outlet": outlet}


@pytest.mark.parametrize("text", [
    "Mi mamá dice que la sopa está fría.",
    "Tu reloj dice que es tarde para salir.",
    "Encontré que la puerta estaba abierta.",
    "I found that the door was open all night.",
    "Mucha gente dice que el pan engorda.",
    "Alguien dice que va a llover mañana.",
    "El vecino dice que la luz se fue.",
    "Según tú, ¿vale la pena?",
])
def test_no_quote_without_a_named_source(text):
    assert director.quote(words_of(text)) is None
