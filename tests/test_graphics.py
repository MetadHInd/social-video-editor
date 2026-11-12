"""Animated graphics: reading specs, timing, layout and drawing calls — without any drawing library
(templates draw on a recording canvas; text is measured with a fixed-width stand-in)."""
from __future__ import annotations

import math

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.application.graphic import SAMPLES, plan_graphic
from social_video_editor.domain.graphics import (ELEMENTS, TEMPLATES, RecordingCanvas, Stage, build_theme, kit, make_scene,
                                                  motion, parse_cue, paths)
from social_video_editor.domain.graphics.icons import ICONS, draw_icon, find_icon, icon_ink
from social_video_editor.domain.graphics.spec import decimals_of, format_number, number, pairs, split_list
from social_video_editor.domain.graphics.theme import DEFAULT_THEME, ink
from social_video_editor.domain.models import PlanError, UserError
from social_video_editor.domain.pieces import GraphicShot, Sequence, make_shot

VERTICAL, LANDSCAPE = (1080, 1920), (1920, 1080)
PRINTED = {"stroke": {"outline": 6, "halo": 3, "thread": 6, "thread_halo": 3.5}, "halftone": {"shadow_offset": 12},
           "motion": {"on_twos": True, "boil": True, "print_in": True}, "poster": {"extrude": 16, "outline": 14, "tilt": -4},
           "inks": {"green": "#00A95C", "blue": "#0078BF"}, "registration": {"green": [-4, -2]}, "accent_ink": "green"}


def stage(size=VERTICAL, theme=None, language="es") -> Stage:
    return Stage(*size, build_theme(theme), language=language)


def samples(language="es"):
    return [{**s, **({"image": "product.png"} if s.get("image") else {})} for s in SAMPLES[language]]


def drawn(scene, t) -> RecordingCanvas:
    cv = RecordingCanvas(scene.st.W, scene.st.H)
    scene.paint(cv, t)
    return cv


# ── reading a spec ───────────────────────────────────────────────────────────
def test_cue_is_a_template_name_and_quoted_pairs():
    spec = parse_cue('bars title="Battery use" data="Cold start:100, Asleep:20" unit=%')
    assert spec == {"template": "bars", "title": "Battery use", "data": "Cold start:100, Asleep:20", "unit": "%"}


@pytest.mark.parametrize("text", ["", 'bars "no key"', 'bars title="unclosed'])
def test_bad_cue_is_a_plan_error(text):
    with pytest.raises(PlanError):
        parse_cue(text)


def test_pairs_and_lists_accept_text_or_data():
    assert pairs("A:15, B:40") == [("A", "15"), ("B", "40")]
    assert pairs("Read:14,9 MB/s; Size:2 TB") == [("Read", "14,9 MB/s"), ("Size", "2 TB")]      # ";" when a value has ","
    assert pairs([["A", 15], {"label": "B", "value": 40}]) == [("A", "15"), ("B", "40")]
    assert pairs({"A": 1}) == [("A", "1")]
    assert split_list("one; two; three") == ["one", "two", "three"]
    assert split_list(["a", "b"]) == ["a", "b"] and split_list(None) == []


def test_numbers_are_written_plainly_and_shown_per_language():
    assert number("40 %") == 40.0 and number("2.5") == 2.5 and number("n/a") is None and number(True) is None
    assert decimals_of("2.50") == 2 and decimals_of(40) == 0
    assert format_number(14900, "es") == "14.900" and format_number(14900, "en") == "14,900"
    assert format_number(2.5, "es", 1) == "2,5" and format_number(1234567.5, "en", 1) == "1,234,567.5"


def test_spanish_cue_keys_and_template_names_are_translated():
    spec = legacy_es.graphic({"plantilla": "barras", "titulo": "Batería", "datos": "A:1, B:2", "unidad": "%"})
    assert spec == {"template": "bars", "title": "Batería", "data": "A:1, B:2", "unit": "%"}
    assert legacy_es.graphic({"tipo": "grafico", "plantilla": "cifra", "valor": 3})["type"] == "graphic"


# ── timing ───────────────────────────────────────────────────────────────────
def test_action_curve_pulls_back_then_overshoots_and_lands():
    assert motion.action(0) == 0 and motion.action(1) == 1
    assert motion.action(0.1) < 0                                   # anticipation
    assert max(motion.action(p / 100) for p in range(101)) > 1.02   # goes past the target
    assert abs(motion.action(0.999) - 1) < 0.01


def test_ambient_loop_and_stroke_wobble_repeat_seamlessly():
    period = motion.LOOP_TICKS / motion.ON_TWOS_FPS
    for t in (0.0, 0.3, 1.234):
        assert motion.loop(t) == pytest.approx(motion.loop(t + period))
        assert motion.wave(t, 0.2, 2) == pytest.approx(motion.wave(t + period, 0.2, 2))
        assert motion.boil(3, t) == motion.boil(3, t + period)
    assert motion.wave(0.0) == pytest.approx(0.0)
    assert len({motion.boil(1, k * motion.BOIL_TICKS / motion.ON_TWOS_FPS) for k in range(8)}) == motion.BOIL_PHASES


def test_stepped_time_holds_on_twos():
    assert motion.stepped(0.0) == 0 and motion.stepped(0.06) == 0 and motion.stepped(0.07) == pytest.approx(1 / 15)
    assert motion.shake(-1) == 0 and motion.shake(0) == 1 and motion.shake(0.5) < 0.02


# ── paths ────────────────────────────────────────────────────────────────────
def test_polyline_helpers():
    line = [(0, 0), (100, 0), (100, 50)]
    assert paths.length(line) == 150
    assert paths.cut(line, 0.5)[-1] == (75, 0) and paths.cut(line, 1.0) == line and paths.cut(line, 0) == [(0, 0)]
    (x, y), deg = paths.point_at(line, 1.0)
    assert (x, y) == (100, 50) and deg == pytest.approx(90)
    assert paths.round_rect(10, 20, 100, 40, 8).bounds() == (10, 20, 110, 60)
    assert paths.rect(0, 0, 5, 5).moved(2, 3).bounds() == (2, 3, 7, 8)


def test_dashes_cover_the_line_and_march_with_the_offset():
    line = [(0, 0), (100, 0), (100, 50)]
    still = paths.dashes(line, 10, 10)
    assert len(still) == 8 and sum(paths.length(d) for d in still) == pytest.approx(80)
    moved = paths.dashes(line, 10, 10, 5)
    assert moved[0] == [(0.0, 0.0), (5.0, 0.0)]                      # the first dash is half gone
    assert paths.dashes(line, 10, 10, 20)[0] == still[0]             # one whole period later: the same picture
    assert paths.dashes([(0, 0)], 10, 10) == []


def test_smooth_curve_passes_through_its_points():
    pts = [(0, 0), (50, 40), (100, 0)]
    sampled = paths.smooth_points(pts, per=10)
    assert sampled[0] == pts[0] and sampled[10] == pytest.approx(pts[1]) and sampled[-1] == pytest.approx(pts[2])


# ── theme ────────────────────────────────────────────────────────────────────
def test_theme_overrides_merge_in_order_and_leave_the_default_alone():
    theme = build_theme({"colors": {"bg": "#111111"}}, None, {"colors": {"accent": "#FF0000"}, "grain": {"strength": 0.1}})
    assert theme["colors"]["bg"] == "#111111" and theme["colors"]["accent"] == "#FF0000"
    assert theme["colors"]["paper"] == DEFAULT_THEME["colors"]["paper"] and DEFAULT_THEME["grain"]["strength"] == 0.0


def test_colors_are_inks_theme_names_or_hex():
    theme = build_theme(PRINTED)
    assert ink(theme, "green") == ("#00A95C", "green") and ink(theme, None) == ("#00A95C", "green")
    assert ink(theme, "paper") == (theme["colors"]["paper"], None) and ink(theme, "#123456") == ("#123456", None)
    assert ink(build_theme(), None) == (DEFAULT_THEME["colors"]["accent"], None)
    with pytest.raises(ValueError, match="unknown color 'purple'"):
        ink(theme, "purple")


# ── every template, both orientations, both looks ────────────────────────────
@pytest.mark.parametrize("size", [VERTICAL, LANDSCAPE])
@pytest.mark.parametrize("theme", [None, PRINTED])
@pytest.mark.parametrize("language", ["es", "en"])
def test_every_template_lays_out_inside_the_safe_zone_and_draws(size, theme, language):
    st = stage(size, theme, language)
    seen = set()
    for spec in samples(language):
        scene = make_scene(spec, st)
        seen.add(scene.name)
        assert scene.fits(), f"{scene.name} does not fit {size}"
        assert 0 < scene.settle <= scene.dur
        first, last = drawn(scene, 0.0), drawn(scene, scene.dur - 1 / 30)
        if not scene.overlay:
            assert first.count("fill") + first.count("stroke") > 5                     # scenery from the first frame
        for line in scene.lines():                                                       # everything it says ends up drawn
            assert all(word in " ".join(last.texts()) for word in line.split()[:1]), (scene.name, line)
        assert last.count("save") == last.count("restore")
        for t in (0.1, scene.settle * 0.5, scene.settle, scene.dur):
            drawn(scene, t)
    assert seen == set(TEMPLATES)


def test_first_frame_is_never_empty():
    st = stage()
    for spec in samples():
        scene = make_scene(spec, st)
        cv = RecordingCanvas(st.W, st.H)
        scene.draw(cv, 0.0)                                         # the graphic itself, without the scenery
        assert cv.calls or scene.overlay, f"{scene.name} draws nothing on its first frame"   # a caption box opens


def test_a_graphic_grows_until_it_fills_the_safe_zone():
    st = stage(VERTICAL)
    few = make_scene({"template": "list", "items": "one; two"}, st)
    many = make_scene({"template": "list", "items": "one; two; three; four; five; six", "title": "A long list of things"}, st)
    assert few.k > many.k >= 0.8
    assert make_scene({"template": "bars", "data": "A:1, B:2"}, st).k > 1.5         # vertical frames are filled
    assert make_scene({"template": "bars", "data": "A:1, B:2"}, stage(LANDSCAPE)).k <= DEFAULT_THEME["scale"]["max"]


def test_settled_graphic_is_a_short_loop_of_states():
    scene = make_scene({"template": "number", "value": "376", "dur": 8}, stage(theme=PRINTED))
    keys = [scene.frame_state(i / 30)[0] for i in range(240)]
    moving = [k for k in keys if k[0] == "moving"]
    settled = {k for k in keys if k[0] == "settled"}
    assert moving and len(set(moving)) <= len(moving) / 2 + 1       # on twos: each state is held two frames
    assert len(settled) == motion.LOOP_TICKS                         # after the entrance: a loop, drawn once
    calm = make_scene({"template": "number", "value": "376", "dur": 8},
                      stage(theme={"motion": {"ambient": False}}))
    assert {k for k in (calm.frame_state(i / 30)[0] for i in range(240)) if k[0] == "settled"} == {("settled", 0)}


def test_lower_third_is_an_overlay_that_closes_at_the_end():
    scene = make_scene({"template": "lower_third", "text": "Official channel", "kicker": "Video", "dur": 3}, stage(LANDSCAPE))
    assert scene.overlay and scene.exit_at == pytest.approx(2.8)
    assert scene.frame_state(2.9)[0][0] == "moving"
    cv = RecordingCanvas(1920, 1080)
    scene.paint(cv, 1.5)
    assert "Official channel" in cv.texts()
    assert not any(c[0] == "fill" and c[1].bounds() == (0, 0, 1920, 1080) for c in cv.calls)   # no backdrop: transparent
    closed = RecordingCanvas(1920, 1080)
    scene.paint(closed, 3.0)
    assert closed.texts() == []


def test_values_count_up_and_are_formatted_for_the_language():
    es = make_scene({"template": "bars", "data": "Antes:1500, Hoy:14900", "unit": "MB/s"}, stage(language="es"))
    en = make_scene({"template": "bars", "data": "Before:1500, Today:14900", "unit": "MB/s"}, stage(language="en"))
    assert "14.900" in drawn(es, es.dur).texts() and "14,900" in drawn(en, en.dur).texts()
    half = [t for t in drawn(es, 0.3).texts() if t.replace(".", "").isdigit()]
    assert half and all(int(t.replace(".", "")) < 14900 for t in half)                 # still counting
    text = make_scene({"template": "number", "value": "2 × 2 TB"}, stage())
    assert "2 × 2 TB" in drawn(text, 0.1).texts()                                       # not a number: shown as is


def test_caption_is_drawn_with_the_graphic():
    scene = make_scene({"template": "number", "value": 5, "caption": "And it happens all day."}, stage())
    assert "And it happens all day." in drawn(scene, 1.0).texts()


def test_title_keeps_forced_lines_and_never_leaves_a_word_alone():
    st = stage(VERTICAL)
    assert make_scene({"template": "title", "text": "Is your phone|listening?"}, st).rows == ["Is your phone", "listening?"]
    auto = make_scene({"template": "title", "text": "Is your phone listening to you today?"}, st)
    widths = [st.measure(r, "display", 100) for r in auto.rows]
    assert len(auto.rows) > 1 and min(widths) >= 0.5 * max(widths)
    assert auto.size > make_scene({"template": "title", "text": "Is your phone listening to you today?"}, stage(LANDSCAPE)).size * 0.5


def test_headline_marks_the_phrase_wherever_it_wraps():
    scene = make_scene({"template": "headline", "text": "Prices keep climbing with no ceiling in sight",
                        "highlight": "no ceiling in sight"}, stage(VERTICAL))
    assert scene.marks and all(b > a for _, a, b in scene.marks)
    rows = {r for r, _, _ in scene.marks}
    assert rows <= set(range(len(scene.rows)))


@pytest.mark.parametrize("spec, message", [
    ({"template": "pie"}, "Unknown graphic template 'pie'"),
    ({"template": "bars"}, "needs 'data'"),
    ({"template": "bars", "data": "A:x, B:2"}, "needs a number"),
    ({"template": "bars", "data": "A:1, B:2", "highlight": "C"}, "highlight 'C'"),
    ({"template": "bars", "data": "A:1", "color": "purple"}, "unknown color 'purple'"),
    ({"template": "list", "items": "1;2;3;4;5;6;7"}, "1 to 6 items"),
    ({"template": "headline", "text": "Some news", "highlight": "missing words"}, "is not in the text"),
    ({"template": "diagram", "nodes": "A"}, "2 to 5 nodes"),
    ({"template": "diagram", "nodes": "A, B", "edges": "A>C"}, "edge 'A>C'"),
    ({"template": "diagram", "nodes": "A, B", "icons": "phone"}, "one entry per node"),
    ({"template": "diagram", "nodes": "A, B", "icons": "phone, unicorn"}, "no illustration called 'unicorn'"),
    ({"template": "diagram", "nodes": "A, B, C", "edges": "A>B; B>C; C>A; A>B; B>C; C>A", "dur": 2}, "do not fit"),
    ({"template": "number", "value": 3, "icon": "unicorn"}, "no illustration called 'unicorn'"),
    ({"template": "number", "value": 3, "dur": "long"}, "number of seconds"),
])
def test_bad_specs_say_what_is_wrong(spec, message):
    with pytest.raises(PlanError, match=message):
        make_scene(spec, stage())
    assert issubclass(PlanError, UserError)


# ── illustrations ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("label, name", [
    ("Celular", "phone"), ("Tu celular", "phone"), ("teléfono", "phone"), ("Phone", "phone"), ("Router", "router"),
    ("Wi-Fi", "router"), ("Internet", "globe"), ("La nube", "cloud"), ("Servidores", "server"), ("servidor", "server"),
    ("PC", "laptop"), ("Procesador", "chip"), ("Memoria RAM", "ram"), ("Disco", "disk"), ("Batería", "battery"),
    ("Antena", "tower"), ("Apps", "app"), ("Tú", "user"), ("Contraseña", "lock"),
])
def test_a_name_gets_its_illustration(label, name):
    assert find_icon(label) == name


def test_unknown_names_have_no_illustration():
    assert find_icon("Blockchain") is None and find_icon("") is None


def test_every_illustration_draws_and_blinks():
    st = stage(theme=PRINTED)
    for name in ICONS:
        cv = RecordingCanvas(st.W, st.H)
        draw_icon(cv, st, name, 500, 500, 200, t=0.4, active=1.0)
        assert cv.count("fill") + cv.count("stroke") >= 8 and cv.count("save") == cv.count("restore"), name
    assert icon_ink(stage(theme={**PRINTED, "icon_inks": {"phone": "blue"}}), "phone") == ("#0078BF", "blue")
    assert icon_ink(st, "phone", "#101010") == ("#101010", None)


def test_diagram_draws_illustrations_for_known_names_and_cards_for_the_rest():
    st = stage(LANDSCAPE)
    scene = make_scene({"template": "diagram", "nodes": "Phone, Blockchain, Internet", "edges": "Phone>Blockchain; Blockchain>Internet"}, st)
    assert scene.icons == ["phone", None, "globe"]
    plain = make_scene({"template": "diagram", "nodes": "Phone, Router", "icons": "-, server"}, st)
    assert plain.icons == [None, "server"]
    texts = drawn(scene, scene.dur).texts()
    assert {"Phone", "Blockchain", "Internet"} <= set(texts)


def test_vertical_diagram_zigzags_and_landscape_is_a_row():
    spec = {"template": "diagram", "nodes": "Phone, Router, Internet", "edges": "Phone>Router:asks; Router>Internet; Internet>Router:answers"}
    v = make_scene(spec, stage(VERTICAL))
    xs, ys = [c[0] for c in v.centers], [c[1] for c in v.centers]
    assert xs[0] == xs[2] < xs[1] and ys == sorted(ys)
    h = make_scene(spec, stage(LANDSCAPE))
    assert len({c[1] for c in h.centers}) == 1 and [c[0] for c in h.centers] == sorted(c[0] for c in h.centers)
    for scene in (v, h):
        st = scene.st
        for e in scene.edges:                                       # arrows stay on the frame
            assert all(0 <= x <= st.W and 0 <= y <= st.H for x, y in e["pts"])
        assert len(scene.sounds()) == 1 + len(scene.edges)


def test_vertical_diagram_counts_its_steps_in_the_corner_the_zigzag_leaves_empty():
    spec = {"template": "diagram", "nodes": "Phone, Router, Internet", "edges": "Phone>Router; Router>Internet"}
    v = make_scene(spec, stage(VERTICAL))
    x0, y0, x1, y1 = v.st.safe
    assert len(v.steps["xs"]) == 2 and all(x0 <= x - v.steps["r"] and x + v.steps["r"] <= x1 for x in v.steps["xs"])
    last, before = v.centers[-1], v.centers[-2]
    assert abs(sum(v.steps["xs"]) / 2 - before[0]) < 1 and last[1] < v.steps["y"] <= y1      # beside the last thing
    assert {"1", "2"} <= set(drawn(v, v.dur).texts())
    # an arrow that leaves the last thing passes through that corner: no counter; landscape has no empty corner
    back = make_scene({**spec, "edges": "Phone>Router; Router>Internet; Internet>Router"}, stage(VERTICAL))
    assert back.steps is None and make_scene(spec, stage(LANDSCAPE)).steps is None


def test_the_marker_is_an_element_a_pack_can_replace():
    st = stage()
    calls = []
    original = ELEMENTS["marker"]
    try:
        ELEMENTS["marker"] = lambda cv, st, x, y, t=0.0, **options: calls.append((round(x), round(y)))
        scene = make_scene({"template": "bars", "data": "A:1, B:2"}, st)
        drawn(scene, scene.dur)
    finally:
        ELEMENTS["marker"] = original
    assert len(calls) == 1


def test_scenery_stays_out_of_the_safe_zone():
    st = stage(VERTICAL)
    lay = kit._traces(st, seed=7)
    x0, y0, x1, y1 = st.safe
    assert lay["traces"] and lay["bits"]
    for pts in lay["traces"]:
        assert all(not (y0 < y < y1) for _, y in pts)
    assert kit._traces(st, seed=7) is lay                             # computed once per frame size
    off = stage(VERTICAL, {"decor": {"traces": 0, "bits": 0, "chips": 0}})
    assert kit._traces(off, seed=7) == {"traces": [], "chips": [], "bits": []}


def test_wrapping_helpers():
    st = stage()
    assert kit.wrap(st, "one two three", "label", 10, 45) == ["one two", "three"]
    assert kit.wrap(st, "one|two three", "label", 10, 500) == ["one", "two three"]
    assert kit.balanced(st, "aaaa bbbb cc dd", "label", 10, 62) == ["aaaa bbbb", "cc dd"]
    assert kit.split_lines(st, "a b c d", "label", 10, 2) == ["a b", "c d"] and kit.split_lines(st, "a", "label", 10, 2) is None
    assert kit.fit_size(st, "abcdefghij", "label", 20, 55) == pytest.approx(10)
    assert len(kit.squiggle(0, 100, 0, 5, 3)) > 6 and math.isclose(kit.squiggle(0, 100, 0, 5, 3)[-1][0], 100)


# ── graphic shots in a plan ──────────────────────────────────────────────────
def test_graphic_shot_is_a_drawn_shot_without_dependencies():
    shot = make_shot({"type": "graphic", "template": "bars", "data": "A:1, B:2", "transition": "cut", "role": "story",
                      "dur": 2.5, "_sounds": [["whoosh", 0.0], ["pop", 0.8], ["pop", 9.0]]}, 0)
    assert isinstance(shot, GraphicShot) and shot.duration() == 2.5 and shot.dependencies() == set()
    assert shot.spec() == {"template": "bars", "data": "A:1, B:2", "dur": 2.5}
    assert shot.sound_events(10.0, 2.5, -12, {}) == [("whoosh", 10.0, -14), ("pop", 10.8, -14)]
    assert shot.summary()["template"] == "bars" and "bars" in shot.describe()
    assert make_shot({"type": "graphic", "template": "title", "text": "x"}, 0).duration() == GraphicShot.DEFAULT_SECONDS
    with pytest.raises(PlanError, match="needs 'template'"):
        make_shot({"type": "graphic"}, 3).template


def test_plan_with_graphics_orders_and_previews():
    plan = {"shots": [{"source": 0, "start": 0, "end": 2}, {"type": "graphic", "template": "title", "text": "Hi"},
                      {"type": "graphic", "template": "number", "value": 3, "transition": "cut", "_sounds": []}]}
    assert Sequence.from_plan(plan).render_order() == [0, 1, 2]
    assert plan_graphic(plan, 2) == {"template": "number", "value": 3}
    with pytest.raises(UserError, match="2 graphic shots"):
        plan_graphic(plan, 3)


# ── illustrated scenes ───────────────────────────────────────────────────────
from social_video_editor.domain.graphics.scenes import ACTIONS, action_of  # noqa: E402


@pytest.mark.parametrize("action", sorted(ACTIONS))
@pytest.mark.parametrize("size", [VERTICAL, LANDSCAPE])
def test_every_scene_action_draws_from_the_first_frame_and_keeps_living(action, size):
    scene = make_scene({"template": "scene", "action": action, "text": "Una frase corta"}, stage(size, PRINTED))
    assert scene.fits() and scene.settle < scene.dur
    first, middle, late = drawn(scene, 0.0), drawn(scene, 0.9), drawn(scene, scene.dur - 0.1)
    assert first.texts() and len(first.calls) > 20                        # the words and the illustration from the start
    assert len(middle.calls) > 20 and len(late.calls) > 20
    keys = {scene.frame_state(scene.settle + k / 15)[0] for k in range(40)}
    assert len(keys) > 1                                                # after the action it is a loop, not a still


def test_scene_reads_its_subject_and_rejects_bad_specs():
    scene = make_scene({"template": "scene", "action": "store", "subject": "Tu celular", "target": "memoria"}, stage())
    assert (scene.subject, scene.target) == ("phone", "ram")
    assert make_scene({"template": "scene", "action": "drain"}, stage()).subject == "battery"
    with pytest.raises(PlanError, match="action must be one of"):
        make_scene({"template": "scene", "action": "dance"}, stage())
    with pytest.raises(PlanError, match="no illustration called"):
        make_scene({"template": "scene", "action": "sleep", "subject": "unicorn"}, stage())


@pytest.mark.parametrize("words, action", [
    ("Si las cierras empiezan de cero", "swipe"), ("No están abiertas, están dormidas", "sleep"),
    ("Gasta batería sin usarla", "drain"), ("Tu mensaje viaja bajo el mar", "send"),
    ("Se van a la bodega", "store"), ("Cuando se congela", "freeze"), ("Close it and open it again", "swipe"),
    ("El cielo es azul", None),
])
def test_the_action_comes_from_a_verb(words, action):
    found = action_of(words.split())
    assert (found[0] if found else None) == action
