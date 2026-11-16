"""The director chooses the newer graphics by itself (line_chart, versus, code, screen, diagram), reads
figures in context, covers a voice memo, and gives exactly the plan of before with every new setting off."""
from __future__ import annotations

import json
from pathlib import Path

from social_video_editor.adapters.files import legacy_es
from social_video_editor.application.explainer import director_lines
from social_video_editor.domain import director, support
from social_video_editor.domain.explainer import build_plan
from social_video_editor.domain.graphics import TEMPLATES
from social_video_editor.domain.script import parse_script
from social_video_editor.domain.script_align import align, transcript
from test_script import said

ON = director.settings({})
OVERLAY = {name for name, t in TEMPLATES.items() if getattr(t, "overlay", False)}


def w(text: str, start: float = 0.0) -> list[tuple[str, float]]:
    return [(x, round(start + 0.4 * i, 3)) for i, x in enumerate(text.split())]


def chosen(text: str, **kw):
    return director.said_data(w(text), opts={**ON, **kw})


def plan_for(sentences: list[str]) -> dict:
    parts = []
    for s in sentences:
        parts += [s, 0.4]
    script = parse_script("\n".join(sentences), templates=set(TEMPLATES))
    return build_plan(script, align([s.text for s in script.sentences], transcript([{"words": said(*parts)}]), {}), {},
                      "script.md")


# ── line chart ───────────────────────────────────────────────────────────────
def test_figures_over_years_make_a_line_chart_in_digits_or_words():
    es = chosen("Pasó de doce millones en dos mil quince a treinta en dos mil dieciocho y setenta y cinco en dos mil veinticuatro.")
    assert es["template"] == "line_chart" and es["data"] == "2015:12, 2018:30, 2024:75" and es["unit"] == "millones"
    en = chosen("Sales went from 12 million in 2015 to 30 million in 2018 and 75 million in 2024.")
    assert en["template"] == "line_chart" and en["data"] == "2015:12, 2018:30, 2024:75"
    assert chosen("Pasó de 12 en 2015 a 75 en 2024.") is None or chosen("Pasó de 12 en 2015 a 75 en 2024.")["template"] != "line_chart"
    assert chosen("Sales went from 12 million in 2015 to 30 million in 2018 and 75 million in 2024.",
                  auto_line_chart=False) is None


def test_years_over_three_sentences_make_one_line_chart():
    group = [{"words": w("En 2015 había doce millones de usuarios.")}, {"words": w("En 2018 eran treinta millones.", 10)},
             {"words": w("Y en 2024, setenta y cinco millones.", 20)}]
    assert director.chart_across(group)["data"] == "2015:12, 2018:30, 2024:75"
    assert director.chart_across([{"words": w("Hoy hay muchos usuarios.")}, *group[1:]]) is None   # the first says no year


# ── versus ───────────────────────────────────────────────────────────────────
def test_two_named_things_with_figures_make_a_versus():
    marked = chosen("The iPhone lasts 20 hours versus 18 hours on the Pixel.")
    assert (marked["template"], marked["left"], marked["right"], marked["rows"]) == ("versus", "iPhone", "Pixel", "Hours:20|18")
    named = chosen("El iPhone dura veinte horas y el Pixel dieciocho.")
    assert (named["template"], named["left"], named["right"], named["rows"]) == ("versus", "iPhone", "Pixel", "Horas:20|18")


def test_a_part_of_a_whole_stays_bars():
    got = chosen("Tu celular tiene ocho gigas y la app usa dos gigas.")
    assert got["template"] == "bars" and got["data"] == "Celular:8, App:2"
    assert chosen("El iPhone dura veinte horas y el Pixel dieciocho.", auto_versus=False) is None or \
        chosen("El iPhone dura veinte horas y el Pixel dieciocho.", auto_versus=False)["template"] != "versus"


def test_rows_gathered_over_two_sentences_about_the_same_two_things():
    a = {"words": w("El iPhone dura veinte horas y el Pixel dieciocho.")}
    b = {"words": w("El iPhone cuesta 900 dólares y el Pixel 700.", 10)}
    got = director.across(a, b, ON)
    assert got["template"] == "versus" and got["rows"] == "Horas:20|18; Dólares:900|700"
    assert director.across(a, {"words": w("El Galaxy cuesta 900 dólares y el Moto 700.", 10)}, ON) is None


# ── code ─────────────────────────────────────────────────────────────────────
def test_a_command_said_makes_a_code_graphic():
    assert chosen("Escribe brew install ffmpeg en la terminal.") == \
        {"template": "code", "lines": "brew install ffmpeg", "at": 0.4}
    assert chosen("Then run npm run build and wait.")["lines"] == "npm run build"
    assert chosen("Pega `git status` y mira.")["lines"] == "git status"


def test_words_that_look_like_commands_are_not_code():
    assert chosen("You open the app and run it.") is None
    assert chosen("Make it work and go test it.") is None
    assert chosen("Escribe brew install ffmpeg en la terminal.", auto_code=False) is None


# ── screen ───────────────────────────────────────────────────────────────────
def test_a_screenshot_the_sentence_names_is_shown():
    screens = ["capturas/captura_ajustes_wifi.png", "Screenshot 2026-01-01 at 10.00.00.png"]
    got = director.said_data(w("Entra en los ajustes del wifi y mira la red."), opts=ON, screens=screens)
    assert got["template"] == "screen" and got["image"] == "capturas/captura_ajustes_wifi.png" and got["at"] == 1.2
    assert director.said_data(w("Turn the wifi off."), opts=ON, screens=screens) is None        # one word only
    one = ["screenshot_battery.png"]
    assert director.screen_for(w("Look at the battery screen."), one)["image"] == "screenshot_battery.png"
    assert director.screen_for(w("The battery lasts all day."), one) is None
    assert director.screen_for(w("Entra en los ajustes del wifi."), screens + ["captura_wifi_ajustes.jpg"]) is None   # two fit


def test_photo_cues_of_screenshots_become_screen_graphics():
    assert director.is_screenshot("Captura de pantalla 2026-03-01.png") and director.is_screenshot("shot.png", (1170, 2532))
    assert not director.is_screenshot("IMG_0042.jpg", (1170, 2532)) and not director.is_screenshot("pantalla_rota.jpg")
    cues = [{"type": "photo", "file": "Screenshot 1.png", "at_sentence": 2, "until_sentence": 2},
            {"type": "photo", "file": "board.jpg", "at_sentence": 3, "until_sentence": 3}]
    out = director.screen_cues(cues)
    assert out[0] == {"type": "graphic", "template": "screen", "image": "Screenshot 1.png", "at_sentence": 2,
                      "until_sentence": 2, "_auto": "screen"}
    assert out[1] == cues[1]


# ── diagram ──────────────────────────────────────────────────────────────────
def test_a_flow_between_illustrated_things_is_a_diagram():
    es = director.diagram_for(w("Tu celular manda la foto a la antena y de ahí al servidor."))
    assert es["nodes"] == "Tu celular, Antena, Servidor" and es["icons"] == "phone, tower, server"
    assert es["edges"].startswith("Tu celular>Antena:Foto")
    en = director.diagram_for(w("The phone sends the photo to the router, then to the cloud."))
    assert en["nodes"] == "Phone, Router, Cloud"
    branch = director.diagram_for(w("El celular manda el mensaje al router y de ahí al servidor o a la nube."))
    assert branch["layout"] == "flow"


def test_no_diagram_without_a_flow_or_illustrations():
    assert director.diagram_for(w("Mañana va a llover de lunes a viernes.")) is None
    assert director.diagram_for(w("Juan manda la carta a María.")) is None          # nothing illustrated
    sentence = {"words": [[x, t] for x, t in w("Your phone sends the photo to the router and then to the cloud.")], "start": 0.0}
    assert director.card(sentence, 0.0, opts=ON)["template"] == "diagram"
    assert director.card(sentence, 0.0, opts=ON, last="diagram")["template"] != "diagram"    # never twice in a row
    assert director.card(sentence, 0.0)["template"] != "diagram"                     # without the settings: as before


# ── figures in context ───────────────────────────────────────────────────────
def test_a_figure_with_its_unit_left_unsaid_over_two_sentences_makes_bars():
    a = {"words": w("Por la mañana, la página carga en doce segundos.")}
    b = {"words": w("Por la noche, en cuatro.", 10)}
    got = director.across(a, b, ON)
    assert got["template"] == "bars" and got["data"] == "Por la mañana:12, Por la noche:4" and got["unit"] == "s"
    en = director.across({"words": w("In the morning, the page takes twelve seconds to load.")},
                         {"words": w("In the evening, only four.", 10)}, ON)
    assert en["data"] == "In the morning:12, In the evening:4"
    assert director.across(a, b, {**ON, "figure_context": False}) is None
    assert director.across(a, {"words": w("Después cierras todo.", 10)}, ON) is None


def test_a_range_of_hours_is_not_a_figure():
    words = w("Y de siete a nueve de la noche, todos ven series.")
    assert director.figure(words)["value"] == "7"                                 # as before
    assert director.figure(words, context=True) is None
    assert director.figure(w("From eight to ten at night everybody watches at once."), context=True) is None


def test_bars_named_by_their_verbs_only_when_asked():
    es = w("Duermes ocho horas y pasas cinco en el celular.")
    assert director.comparison(es, context=True) is None
    assert director.comparison(es, context=True, verbs=True)["data"] == "Duermes:8, En el celular:5"
    en = w("You sleep eight hours and spend five on the phone.")
    assert director.comparison(en, context=True, verbs=True)["data"] == "Sleep:8, On the phone:5"
    assert director.comparison(w("Medí veinte minutos y pasé treinta en el celular."), context=True, verbs=True) is None   # a past form
    assert director.comparison(w("Es ocho horas y pasas cinco en el celular."), context=True, verbs=True) is None   # a link verb


def test_a_scene_fits_what_is_done():
    sentence = {"words": [[x, t] for x, t in w("Todos abren la ducha a la vez.")], "start": 0.0}
    assert director.card(sentence, 0.0)["template"] == "scene"                       # as before: "open an app"
    assert director.card(sentence, 0.0, opts=ON)["template"] == "title"
    app = {"words": [[x, t] for x, t in w("Abre la app y espera un poco.")], "start": 0.0}
    assert director.card(app, 0.0, opts=ON)["template"] == "scene"


# ── the whole director ───────────────────────────────────────────────────────
SCRIPT = [
    "In the morning, the page takes twelve seconds to load.",
    "In the evening, only four.",
    "Sales went from 12 million in 2015 to 30 million in 2018 and 75 million in 2024.",
    "The iPhone lasts 20 hours versus 18 hours on the Pixel.",
    "Then type `brew install ffmpeg` in the terminal and wait a little.",
    "Your phone sends the photo to the router and then to the cloud.",
    "From eight to ten at night everybody opens the shower at once.",
    "You sleep eight hours and spend five on the phone.",
    "So close the apps you do not use and keep the battery for later.",
    "Do you close them?",
]


def test_with_every_new_setting_off_the_plan_is_the_one_of_before():
    plan = plan_for(SCRIPT)
    cfg = {"script": {"auto_broll": True, **director.OFF}, "language": "en"}
    cuts = director.propose(plan, cfg, OVERLAY)
    notes = support.propose({**plan, "cutaways": cuts}, "en", settings=cfg)
    before = json.loads((Path(__file__).parent / "director_before_choices.json").read_text())
    assert json.loads(json.dumps(cuts + notes, sort_keys=True)) == before


def test_the_director_draws_varied_graphics_by_itself():
    plan = plan_for(SCRIPT)
    cuts = director.propose(plan, {"script": {"auto_broll": True}, "language": "en"}, OVERLAY)
    drawn = {c["at_sentence"]: c for c in cuts}
    assert drawn[0]["template"] == "bars" and drawn[0]["until_sentence"] == 1
    assert [drawn[k]["template"] for k in (2, 3, 4, 5)] == ["line_chart", "versus", "code", "diagram"]
    assert all(c["_auto"] == "director" and c["from_word"] for c in cuts)
    assert 9 not in drawn                                                       # the closing stays on camera
    plan["cutaways"] = cuts
    lines = director_lines(plan)
    assert lines and "line_chart" in lines[0] and "versus" in lines[0]


def test_a_voice_memo_is_covered_whole():
    sentences = ["Your phone keeps the apps asleep in memory.", "That is why they open at once.", "Fine.",
                 "The battery barely notices it.", "Do you close them?", "Tell me."]
    plan = {**plan_for(sentences), "pictureless": [0]}
    cuts = director.propose(plan, {"script": {"auto_broll": True}, "language": "en"}, OVERLAY)
    covered = {n for c in cuts for n in range(c["at_sentence"], c["until_sentence"] + 1)}
    assert covered == set(range(len(sentences)))                               # the closing ones too
    starts = {s["n"]: s["start"] for s in plan["sentences"]}
    assert all(abs(c["from_word"][1] - starts[c["at_sentence"]]) < 1e-6 for c in cuts)   # no face first
    plain = plan_for(sentences)
    assert director.propose(plain, {"script": {"auto_broll": True}, "language": "en"}, OVERLAY, pictureless={0}) == cuts
    assert any("voice memo" in line for line in director_lines({**plan, "cutaways": cuts}))


def test_notes_over_a_voice_memo_read_alone():
    plan = plan_for(["Your phone keeps twenty apps asleep in the warehouse all day long."])
    words = {n["label"] for c in support.propose(plan, "en") for n in [{"label": x} for x in c["items"]]}
    alone = {x for c in support.propose({**plan, "pictureless": [0]}, "en") for x in c["items"]}
    assert alone and alone < words and "Warehouse" in words and "Warehouse" not in alone


def test_spanish_names_of_the_settings():
    assert legacy_es.config({"guion": {"lineas_auto": False, "versus_auto": False, "codigo_auto": False,
                                       "capturas_auto": False, "diagramas_auto": False, "cifras_en_contexto": False,
                                       "escenas_ajustadas": False, "barras_con_verbos": True}}) == {
        "script": {"auto_line_chart": False, "auto_versus": False, "auto_code": False, "auto_screen": False,
                   "auto_diagram": False, "figure_context": False, "scene_fit": False, "verb_bars": True}}
