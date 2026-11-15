"""The newer graphics (line_chart, versus, code, screen, diagram flows), the subtitle line of kit.caption and
the helpers that let the engine choose them on its own. Synthetic data only; drawn on a recording canvas,
plus a few real (Skia) frames when the graphics extra is installed."""
from __future__ import annotations

import re

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.domain.graphics import RecordingCanvas, Stage, build_theme, kit, make_scene, parse_cue
from social_video_editor.domain.graphics import suggest
from social_video_editor.domain.graphics.charts import nice_ticks, versus_rows
from social_video_editor.domain.graphics.screens import code_lines, tokens
from social_video_editor.domain.models import PlanError

VERTICAL, LANDSCAPE = (1080, 1920), (1920, 1080)
PRINTED = {"stroke": {"outline": 6, "halo": 3, "thread": 6, "thread_halo": 3.5}, "halftone": {"shadow_offset": 12},
           "motion": {"on_twos": True, "boil": True, "print_in": True}, "inks": {"green": "#00A95C"},
           "registration": {"green": [-4, -2]}, "accent_ink": "green", "box": {"tilts": [-1.1, 0.8], "radius": 0}}

SPECS = {
    "line_chart": {"template": "line_chart", "title": "Phones sold", "data": "2015:12, 2018:30, 2021:44, 2024:75",
                   "data2": "2015:5, 2018:9, 2021:20, 2024:31", "series": "Phones, Laptops", "unit": "M",
                   "source": "Source: sample"},
    "versus": {"template": "versus", "title": "Which lasts longer?", "left": "Phone", "right": "Laptop",
               "rows": "Battery:20 h|9 h; Weight:0.2*|1.5; Screen:6|15; Look:Nice|Great*", "source": "Sample"},
    "code": {"template": "code", "title": "Install it", "lines": "brew install ffmpeg; pip install social-video-editor",
             "output": "ok; done", "highlight": 2},
    "code_editor": {"template": "code", "lang": "python", "text": "def f(x):\n    # twice\n    return x * 2\nprint(f(4))",
                    "output": "8", "highlight": "return", "file": "f.py"},
    "screen": {"template": "screen", "title": "Turn this off", "image": "shot.png", "focus_x": 0.3, "focus_y": 0.7,
               "url": "example.com", "label": "Refresh", "source": "Screenshot"},
    "flow": {"template": "diagram", "title": "Where a photo goes", "nodes": "Phone, Cache, Server, Cloud",
             "edges": "Phone>Cache:saves; Cache>Server:new; Cache>Cloud:copy; Server>Phone:done", "dur": 6},
}


def stage(size=VERTICAL, theme=None, image=(1600, 1000)) -> Stage:
    return Stage(*size, build_theme(theme), image_size=lambda f: image if f else None)


def drawn(scene, t) -> RecordingCanvas:
    cv = RecordingCanvas(scene.st.W, scene.st.H)
    scene.paint(cv, t)
    return cv


# ── every new graphic: both frames, both looks ───────────────────────────────
@pytest.mark.parametrize("name", list(SPECS))
@pytest.mark.parametrize("size", [VERTICAL, LANDSCAPE])
@pytest.mark.parametrize("theme", [None, PRINTED])
@pytest.mark.parametrize("image", [(1600, 1000), (900, 1950)])
def test_new_graphics_fit_draw_and_keep_moving(name, size, theme, image):
    scene = make_scene(SPECS[name], stage(size, theme, image))
    assert scene.fits(), f"{name} does not fit {size}"
    assert 0 < scene.settle <= scene.dur
    first = RecordingCanvas(*size)
    scene.draw(first, 0.0)
    assert first.calls, f"{name} draws nothing on its first frame"
    last = drawn(scene, scene.dur - 1 / 30)
    assert last.count("save") == last.count("restore")
    said = " ".join(last.texts())
    for line in scene.lines():
        assert re.search(r"\w+|\S", line)[0] in said, (name, line)          # code is drawn token by token
    # deterministic: the same instant draws the same calls
    assert drawn(scene, scene.settle * 0.5).calls == drawn(scene, scene.settle * 0.5).calls
    # once settled it still moves (the marker bobs, a cursor blinks, dashes march…)
    a, b = scene.frame_state(scene.settle + 0.2)[1], scene.frame_state(scene.settle + 0.9)[1]
    assert drawn(scene, a).calls != drawn(scene, b).calls, f"{name} is static once settled"


def test_the_graphics_fill_the_frame():
    """No empty thirds: the content spans most of the safe zone's height (vertical) and width (landscape)."""
    for name, spec in SPECS.items():
        for size in (VERTICAL, LANDSCAPE):
            st = stage(size)
            scene = make_scene(spec, st)
            w, h = scene.block
            if size == VERTICAL and name not in ("flow",):
                assert h >= st.safe_h * 0.5 or w >= st.safe_w * 0.9, (name, size, scene.block)
            else:
                assert w >= st.safe_w * 0.5 or h >= st.safe_h * 0.6, (name, size, scene.block)


# ── reading the specs ────────────────────────────────────────────────────────
@pytest.mark.parametrize("spec, message", [
    ({"template": "line_chart"}, "needs 'data'"),
    ({"template": "line_chart", "data": "2019:3"}, "2 to 12 points"),
    ({"template": "line_chart", "data": "2019:3, 2020:x"}, "needs a number"),
    ({"template": "line_chart", "data": "3, 4"}, "needs a label"),
    ({"template": "line_chart", "data": "A:1, B:2", "data2": "A:1, C:2"}, "same labels"),
    ({"template": "line_chart", "data": "A:1, B:2", "highlight": "Z"}, "highlight 'Z'"),
    ({"template": "line_chart", "data": "A:1, B:2", "series": "One, Two"}, "'series' names 2"),
    ({"template": "versus", "right": "B", "rows": "X:1|2"}, "needs 'left'"),
    ({"template": "versus", "left": "A", "right": "B", "rows": "X:1-2"}, "Label:left\\|right"),
    ({"template": "versus", "left": "A", "right": "B", "rows": "a:1|2;b:1|2;c:1|2;d:1|2;e:1|2;f:1|2"}, "1 to 5 rows"),
    ({"template": "versus", "left": "A", "right": "B", "rows": "X:1|2", "icons": "phone"}, "two entries"),
    ({"template": "versus", "left": "A", "right": "B", "rows": "X:1|2", "icons": "phone, unicorn"}, "unicorn"),
    ({"template": "code"}, "needs 'lines'"),
    ({"template": "code", "lines": ";".join("x" * 3 for _ in range(13))}, "1 to 12 lines"),
    ({"template": "code", "lines": "ls", "highlight": 4}, "highlight 4"),
    ({"template": "code", "lines": "ls", "highlight": "rm"}, "not in any line"),
    ({"template": "screen"}, "needs 'image'"),
    ({"template": "screen", "image": "shot.png", "zoom": 20}, "'zoom' must be"),
    ({"template": "screen", "image": "shot.png", "focus_x": 2}, "'focus_x' must be"),
    ({"template": "screen", "image": "shot.png", "frame": "tablet"}, "frame 'tablet'"),
    ({"template": "diagram", "nodes": "A, B, C, D, E, F", "edges": "A>B; B>C"}, "6 when it branches"),
    ({"template": "diagram", "nodes": "A, B", "edges": "A>B", "layout": "tree"}, "layout 'tree'"),
])
def test_bad_specs_say_what_is_wrong(spec, message):
    with pytest.raises(PlanError, match=message):
        make_scene(spec, stage())


def test_a_screen_without_its_picture_is_an_error_at_validation():
    st = Stage(*VERTICAL, build_theme(), image_size=lambda f: None)
    with pytest.raises(PlanError, match="image not found or not readable: missing.png"):
        make_scene({"template": "screen", "image": "missing.png"}, st)


def test_versus_rows_and_who_wins():
    rows = versus_rows("Speed:10|40, Price:5*|9, Camera:Good|Great, Look:A|B*, Size:3|3")
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [("Speed", "10", "40", 1), ("Price", "5", "9", 0),
                                                       ("Camera", "Good", "Great", None), ("Look", "A", "B", 1),
                                                       ("Size", "3", "3", None)]
    assert versus_rows("Weight (kg):0,2|1,5; Battery:20 h|9 h")[0][1:3] == ("0,2", "1,5")
    scene = make_scene(SPECS["versus"], stage())
    assert scene.wins == [2, 2] and scene.champion is None
    win = make_scene({**SPECS["versus"], "rows": "Battery:20|9; Weight:0.2*|1.5"}, stage())
    assert win.champion == 0 and win.wins == [2, 0]


def test_line_chart_places_years_by_their_value_and_rounds_its_axis():
    scene = make_scene({"template": "line_chart", "data": "2015:12, 2016:30, 2024:75"}, stage(LANDSCAPE))
    xs = [x for x, _ in scene.pts[0]]
    assert (xs[1] - xs[0]) < (xs[2] - xs[1]) / 4                     # one year apart vs eight
    even = make_scene({"template": "line_chart", "data": "Mon:1, Tue:5, Sun:2"}, stage(LANDSCAPE))
    exs = [x for x, _ in even.pts[0]]
    assert exs[1] - exs[0] == pytest.approx(exs[2] - exs[1])
    assert scene.ticks[0] == 0 and scene.ticks[-1] >= 75
    assert nice_ticks(0, 75) == [0, 20, 40, 60, 80] and nice_ticks(-3, 9)[0] <= -3
    assert scene.hi == 2 and make_scene({**SPECS["line_chart"], "highlight": "2018"}, stage()).hi == 1


def test_code_lines_tokens_and_a_shell_prompt():
    assert code_lines("ls -la; cd src") == ["ls -la", "cd src"]
    assert code_lines("a = 1; b = 2\\nprint(a)") == ["a = 1; b = 2", "print(a)"]
    assert code_lines(["x", "y"]) == ["x", "y"]
    kinds = dict(tokens('git commit -m "first" # done', shell=True))
    assert kinds["git"] == "command" and kinds["-m"] == "flag" and kinds['"first"'] == "string" and kinds["# done"] == "comment"
    assert dict(tokens("return x * 2", shell=False))["return"] == "keyword"
    shell = make_scene(SPECS["code"], stage())
    assert shell.prompt == "$" and shell.inline_out and shell.hi == 1
    editor = make_scene(SPECS["code_editor"], stage())
    assert editor.prompt == "" and not editor.inline_out and editor.hi == 2 and editor.panel_h > 0
    cv = drawn(shell, shell.dur - 0.05)
    assert any(c[0] == "text" and c[4] == "mono" for c in cv.calls)        # monospace from the theme
    assert "mono" in build_theme()["fonts"]


def test_long_commands_wrap_in_a_vertical_frame_instead_of_shrinking():
    long = "pip install --upgrade some-really-long-package-name another-package-name"
    scene = make_scene({"template": "code", "lines": long}, stage(VERTICAL))
    assert len(scene.wrapped[0]) > 1 and scene.size >= scene.z(40)
    a, b = scene.wrapped[0][0]
    assert long[b - 1] in " -"                                    # broken after a space or a sign


def test_screen_zooms_inside_the_frame_or_shows_a_loupe_in_the_empty_band():
    bare = {"template": "screen", "image": "shot.png", "focus_x": 0.3, "focus_y": 0.7}
    wide_in_landscape = make_scene(bare, stage(LANDSCAPE, image=(1920, 1080)))
    assert wide_in_landscape.loupe is None
    x0, y0, w0, h0 = wide_in_landscape._image_box(0.0)
    x1, y1, w1, h1 = wide_in_landscape._image_box(wide_in_landscape.settle)
    assert w1 > w0 * 1.4                                          # it zoomed in…
    vx, vy, vw, vh = wide_in_landscape.view
    assert x1 <= vx + 0.01 and x1 + w1 >= vx + vw - 0.01           # …and no edge of the screen is left empty
    assert make_scene(SPECS["screen"], stage(VERTICAL, image=(1920, 1080))).loupe[0] == "below"
    tall = make_scene(SPECS["screen"], stage(LANDSCAPE, image=(900, 1950)))
    assert tall.loupe[0] == "beside" and tall.frame == "phone"


def test_diagram_flow_branches_and_loops_and_chains_stay_chains():
    chain = make_scene({"template": "diagram", "nodes": "Phone, Router, Internet",
                        "edges": "Phone>Router; Router>Internet; Internet>Router"}, stage(LANDSCAPE))
    assert not chain.flow
    flow = make_scene(SPECS["flow"], stage(LANDSCAPE))
    assert flow.flow and flow.back == {(2, 0)}
    (px, py), (cx, cy), (sx, sy), (ux, uy) = flow.centers
    assert px < cx < sx == ux and sy != uy                        # a branch opens into a column of two
    assert sy > uy                                                # the loop's end sits below, where the loop runs
    vertical = make_scene(SPECS["flow"], stage(VERTICAL))
    (px, py), (cx, cy), (sx, sy), (ux, uy) = vertical.centers
    assert py < cy < sy == uy and sx > ux                          # rows top to bottom; the loop's end on the right
    forced = make_scene({**SPECS["flow"], "layout": "chain", "nodes": "Phone, Cache, Server, Cloud"}, stage(LANDSCAPE))
    assert not forced.flow
    six = make_scene({"template": "diagram", "nodes": "A, B, C, D, E, F",
                      "edges": "A>B; A>C; B>D; C>D; D>E; E>F", "dur": 7}, stage(VERTICAL))
    assert six.flow and six.fits()


# ── the narration box as a subtitle line ─────────────────────────────────────
def test_caption_by_default_is_where_it_always_was():
    st = stage()
    cv = RecordingCanvas(*VERTICAL)
    x, y, w, h = kit.caption(cv, st, "It all starts on your phone.")
    assert (x, y) == (st.px(72), st.H * 0.792)


@pytest.mark.parametrize("size", [VERTICAL, LANDSCAPE])
def test_caption_marks_the_word_being_said_anywhere(size):
    st = stage(size)
    accent = build_theme()["colors"]["accent"]
    cv = RecordingCanvas(*size)
    x, y, w, h = kit.caption(cv, st, "This drains the battery", highlight="Battery!", x=st.W / 2, y=st.H * 0.5,
                             anchor="center", width=st.W * 0.9)
    assert x == pytest.approx(st.W / 2 - w / 2) and y == st.H * 0.5 and w <= st.W * 0.9 + 1
    bands = [c for c in cv.calls if c[0] == "fill" and c[2] == accent]
    assert len(bands) == 1
    bx0, _, bx1, _ = bands[0][1].bounds()
    word_x = x + st.px(58 if st.vertical else 46) * 0.62 + st.measure("This drains the ", "label", st.px(58 if st.vertical else 46))
    assert bx0 < word_x < bx1
    half = RecordingCanvas(*size)
    kit.caption(half, st, "This drains the battery", highlight=1, mark=0.5)
    full = RecordingCanvas(*size)
    kit.caption(full, st, "This drains the battery", highlight=1, mark=1.0)
    width = [next(c[1].bounds() for c in cv_.calls if c[0] == "fill" and c[2] == accent) for cv_ in (half, full)]
    assert (width[0][2] - width[0][0]) < (width[1][2] - width[1][0])
    none = RecordingCanvas(*size)
    kit.caption(none, st, "This drains the battery", highlight="phone")
    assert not any(c[0] == "fill" and c[2] == accent for c in none.calls)


# ── Spanish names ────────────────────────────────────────────────────────────
def test_spanish_template_names_keys_and_values():
    spec = legacy_es.graphic(parse_cue('pantalla imagen="a.png" enfoque_x=0.2 aumento=3 marco=celular direccion="x.com"'))
    assert spec == {"template": "screen", "image": "a.png", "focus_x": "0.2", "zoom": "3", "frame": "phone", "url": "x.com"}
    assert legacy_es.graphic(parse_cue('frente_a_frente izquierda=A derecha=B filas="X:1|2"'))["template"] == "versus"
    assert legacy_es.graphic(parse_cue('lineas datos="2019:1, 2020:2" datos2="2019:2, 2020:3" minimo=0'))["data2"]
    code = legacy_es.graphic(parse_cue('codigo lineas="ls" salida="a" lenguaje=bash indicador=">"'))
    assert code == {"template": "code", "lines": "ls", "output": "a", "lang": "bash", "prompt": ">"}
    assert legacy_es.graphic(parse_cue('diagrama nodos="A, B" disposicion=flujo'))["layout"] == "flow"
    assert legacy_es.cue(parse_cue('pantalla imagen="a.png" marco=navegador'))["frame"] == "browser"


# ── choosing them on their own ───────────────────────────────────────────────
def words(text: str):
    return [(w, round(i * 0.3, 2)) for i, w in enumerate(text.split())]


def test_line_chart_from_figures_tied_to_years():
    spec = suggest.line_chart_from_words(words("Se vendieron 12 millones en 2015, 30 en 2018 y 75 millones en 2024."))
    assert spec == {"template": "line_chart", "data": "2015:12, 2018:30, 2024:75", "at": 0.6}
    assert suggest.line_chart_from_words(words("In 2019 it used 40% and in 2022 55% and in 2025 70%"))["unit"] == "%"
    assert suggest.line_chart_from_words(words("Pasó de 12 en 2015 a 75 en 2024")) is None        # two points: bars
    assert suggest.line_chart_from_words(words("Tengo 3 apps, 4 fotos y 5 videos")) is None
    assert suggest.line_chart_spec([("Q1", 3), ("Q2", 5), ("Q3", "8")])["data"] == "Q1:3, Q2:5, Q3:8"
    assert suggest.line_chart_spec([("enero", 3), ("marzo", 5), ("julio", 8)]) is not None
    assert suggest.line_chart_spec([("2020", 3), ("2019", 5), ("2021", 8)]) is None             # not in order
    assert suggest.line_chart_spec([("Phone", 3), ("PC", 5), ("TV", 8)]) is None                # not times
    assert make_scene(suggest.line_chart_spec([("2019", 3), ("2020", 5), ("2021", 8)]), stage()).fits()


def test_versus_from_two_things_set_against_each_other():
    spec = suggest.versus_from_words(words("El celular dura 20 horas frente a 9 horas del portátil"))
    assert (spec["left"], spec["right"], spec["rows"]) == ("celular", "portátil", "Horas:20|9")
    spec = suggest.versus_from_words(words("Phone 20 hours vs laptop 9 hours"))
    assert (spec["left"], spec["right"], spec["rows"]) == ("Phone", "laptop", "Hours:20|9")
    assert suggest.versus_from_words(words("Dura 20 horas y carga en 2")) is None               # nothing set against
    assert suggest.versus_from_words(words("Phone 20 hours vs laptop")) is None                 # one side has no figure
    spec = suggest.versus_spec("Phone", "Laptop", [("Price", "900", "1200"), ("Battery", "20", "9")],
                               lower_better=("Price",))
    assert spec["rows"] == "Price:900*|1200; Battery:20|9"
    assert suggest.versus_spec("Phone", "phone", [("X", "1", "2")]) is None
    assert make_scene(spec, stage()).champion == 0


def test_code_from_a_command_in_the_sentence():
    spec = suggest.code_from_words(words("Escribe `brew install ffmpeg` en la terminal"))
    assert spec == {"template": "code", "lines": "brew install ffmpeg", "at": 0.3}
    assert suggest.code_from_words(words("Luego corre npm run build y listo"))["lines"] == "npm run build"
    assert suggest.code_from_words(words("Abre la app y espera un poco")) is None
    assert suggest.code_from_words(words("Ve a la carpeta de fotos")) is None
    assert suggest.code_spec("def f(x):\n    return x * 2")["lang"] == "python"
    assert suggest.code_spec("Esto no es código.") is None
    assert suggest.looks_like_code("$ ls") and not suggest.looks_like_code("cd es un disco")


def test_screen_from_a_picture_file():
    spec = suggest.screen_spec("fotos/Captura de pantalla 1.png", (1170, 2532), (0.5, 0.3), label="Here")
    assert spec == {"template": "screen", "image": "fotos/Captura de pantalla 1.png", "frame": "phone",
                    "focus_x": 0.5, "focus_y": 0.3, "label": "Here"}
    assert suggest.screen_spec("clip.mp4") is None
    assert suggest.is_screenshot("Screenshot 2026-01-02.png") and not suggest.is_screenshot("beach.jpg")


def test_flow_from_things_that_branch_or_loop():
    spec = suggest.flow_from_words(words("Tu celular manda la foto al servidor o a la nube y el servidor vuelve al celular"))
    assert spec["nodes"] == "Celular, Servidor, Nube" and spec["layout"] == "flow"
    assert spec["edges"] == "Celular>Servidor; Celular>Nube; Servidor>Celular"
    assert make_scene(spec, stage()).flow
    assert suggest.flow_from_words(words("El celular habla con el router y el router con internet")) is None   # a chain
    assert suggest.diagram_flow_spec(["A", "B", "C"], [("A", "B"), ("A", "C", "no")])["edges"] == "A>B; A>C:no"
    assert suggest.diagram_flow_spec(["A", "B", "C"], [("A", "B"), ("B", "C")]) is None
    assert suggest.diagram_flow_spec(["A", "B"], [("A", "Z")]) is None


# ── real frames ──────────────────────────────────────────────────────────────
def test_new_graphics_draw_real_frames_the_same_every_time(tmp_path):
    skia_renderer = pytest.importorskip("social_video_editor.adapters.skia.renderer", exc_type=ImportError)
    pytest.importorskip("skia", exc_type=ImportError)
    r = skia_renderer.SkiaGraphicRenderer()
    shot = r.sample_image(tmp_path / "shot.png")
    for name, spec in SPECS.items():
        spec = {**spec, **({"image": str(shot)} if "image" in spec else {})}
        for W, H in ((270, 480), (480, 270)):
            scene = make_scene(spec, r.stage(W, H, build_theme(PRINTED), "en"))
            a, b = r.still(scene, scene.settle), r.still(scene, scene.settle)
            assert a.shape == (H, W, 3) and a.std() > 5 and (a == b).all(), name
