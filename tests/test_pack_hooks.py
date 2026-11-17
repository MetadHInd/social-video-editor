"""The small hooks a pack uses to draw places of its own: a template's scenery(), the "backdrop" element of a
voice memo, dressers that complete a graphic from the whole sentence, and SceneAction.arrange()/play() to put
a scene's action somewhere else. Without a pack nothing changes."""
from __future__ import annotations

import yaml

from social_video_editor.adapters.files import packs as packs_module
from social_video_editor.adapters.files.packs import FilePackRepository
from social_video_editor.domain.backdrop import Backdrop
from social_video_editor.domain.graphics import (DRESSERS, ELEMENTS, TEMPLATES, RecordingCanvas, Stage, build_theme,
                                                 dress, make_scene)
from social_video_editor.domain.graphics.scenes import SceneAction

import pytest


@pytest.fixture
def registries():
    elements, templates, dressers, loaded = dict(ELEMENTS), dict(TEMPLATES), list(DRESSERS), dict(packs_module._loaded)
    yield
    ELEMENTS.clear(), ELEMENTS.update(elements)
    TEMPLATES.clear(), TEMPLATES.update(templates)
    DRESSERS[:] = dressers
    packs_module._loaded.clear(), packs_module._loaded.update(loaded)


def stage(W=1080, H=1920):
    return Stage(W, H, build_theme())


def drawn(scene, t=1.0):
    cv = RecordingCanvas(scene.st.W, scene.st.H)
    scene.paint(cv, t)
    return cv


def test_without_a_pack_a_scene_paints_the_theme_stage_and_the_backdrop_only_that():
    st = stage()
    title = make_scene({"template": "title", "text": "Hello"}, st)
    assert drawn(title).calls[0][0] == "fill"                       # the stage comes first (kit.backdrop)
    assert "backdrop" not in ELEMENTS
    back = Backdrop({"dur": 2}, st)
    plain = drawn(back)
    assert plain.calls and all(c[0] != "text" for c in plain.calls)


def test_a_template_may_draw_its_own_place_instead_of_the_stage(registries):
    base = TEMPLATES["title"]

    class Placed(base):
        def scenery(self, cv, t):
            cv.fill(None, "#123456")

    TEMPLATES["title"] = Placed
    cv = drawn(make_scene({"template": "title", "text": "Hello"}, stage()))
    assert cv.calls[0][:3] == ("fill", None, "#123456")
    assert sum(1 for c in cv.calls if c[0] == "stroke" and c[2] == build_theme()["colors"]["grid"]) == 0


def test_a_pack_backdrop_element_is_drawn_over_the_stage_of_a_voice_memo(registries):
    seen = []
    ELEMENTS["backdrop"] = lambda cv, st, x, y, t, **o: seen.append((x, y, round(t, 2), o.get("dur")))
    drawn(Backdrop({"dur": 2.5}, stage(1920, 1080)), 0.5)
    assert seen == [(960.0, 540.0, 0.5, 2.5)]


def test_dress_without_dressers_gives_the_same_spec_and_a_dresser_completes_it(registries):
    spec = {"template": "scene", "action": "swipe"}
    assert dress(spec, ["You", "close", "it"]) is spec
    DRESSERS.append(lambda s, words, language: {**s, "said": " ".join(words), "lang": language})
    DRESSERS.append(lambda s, words, language: None)               # None leaves it as it is
    out = dress(spec, ["You", "close", "it"], "en")
    assert out == {"template": "scene", "action": "swipe", "said": "You close it", "lang": "en"}
    assert spec == {"template": "scene", "action": "swipe"}         # the original is not changed


def test_a_scene_action_can_be_arranged_in_another_box_and_played_alone():
    scene = make_scene({"template": "scene", "action": "send", "subject": "phone", "target": "globe"}, stage())
    assert isinstance(scene, SceneAction) and scene.tall          # vertical: one above the other
    scene.arrange((100, 100, 300, 300), tall=False)
    assert scene.area == (100, 100, 300, 300) and (scene.cx, scene.cy) == (200, 200)
    assert scene.a[1] < scene.b[1] + 1 and scene.a[0] < scene.b[0]  # side by side now
    cv = RecordingCanvas(1080, 1920)
    scene.play(cv, 2.0)
    assert cv.count("fill") > 0 and not cv.texts()                  # the action alone, no poster


def test_arrange_by_default_gives_the_same_layout_as_before():
    for W, H in ((1080, 1920), (1920, 1080)):
        st = stage(W, H)
        scene = make_scene({"template": "scene", "action": "store", "text": "Kept in memory"}, st)
        before = (scene.cx, scene.cy, scene.S, scene.a, scene.b)
        scene.arrange(scene.area)
        assert (scene.cx, scene.cy, scene.S, scene.a, scene.b) == before


def test_a_synthetic_pack_registers_a_place_a_backdrop_and_a_dresser(workspace, registries):
    folder = workspace / "packs" / "tiny"
    folder.mkdir(parents=True)
    (folder / "pack.yaml").write_text(yaml.safe_dump({"theme": {"colors": {"accent": "#FF0000"}}}), encoding="utf-8")
    (folder / "elements.py").write_text(
        "from social_video_editor.domain.graphics import TEMPLATES, dresser, element, template\n"
        "Base = TEMPLATES['scene']\n"
        "@template('scene')\n"
        "class Room(Base):\n"
        "    def scenery(self, cv, t):\n"
        "        cv.fill(None, '#00FF00')\n"
        "@element('backdrop')\n"
        "def room(cv, st, x, y, t=0.0, **_):\n"
        "    cv.fill(None, '#0000FF')\n"
        "@dresser\n"
        "def place(spec, words, language):\n"
        "    return {**spec, 'place': 'room'} if 'night' in words else None\n", encoding="utf-8")
    pack = FilePackRepository().load("tiny")
    st = Stage(1080, 1920, build_theme(pack["theme"]))
    scene = make_scene({"template": "scene", "action": "sleep"}, st)
    assert drawn(scene).calls[0][:3] == ("fill", None, "#00FF00")
    assert ("fill", None, "#0000FF") in [c[:3] for c in drawn(Backdrop({"dur": 1}, st)).calls]
    assert dress({"template": "title"}, ["at", "night"])["place"] == "room"
    assert "place" not in dress({"template": "title"}, ["by", "day"])


def test_the_director_lets_a_pack_dress_the_card_it_chose():
    """Without a dresser the plan is the one of before; with one, the card gets what the pack adds from the sentence."""
    from social_video_editor.domain import director
    from social_video_editor.domain.graphics import DRESSERS

    text = "Every night the whole building is on the phone at once."
    words = [[w, 1.0 + 0.4 * i] for i, w in enumerate(text.split())]
    plan = {"sentences": [{"n": 0, "text": "A first line said to the camera here.", "source": 0, "start": 0.0, "end": 0.9,
                           "words": [["A", 0.0], ["first", 0.2], ["line", 0.4], ["said", 0.5], ["here.", 0.7]]},
                          {"n": 1, "text": text, "source": 0, "start": 1.0, "end": 5.6, "words": words},
                          {"n": 2, "text": "And that is the end of it.", "source": 0, "start": 6.0, "end": 7.5,
                           "words": [["And", 6.0], ["that", 6.2], ["is", 6.4], ["the", 6.6], ["end.", 6.9]]}],
            "cutaways": []}
    cfg = {"language": "en", "script": {"opening": 0.5, "max_camera": 1.0}}
    before = director.propose(plan, cfg)
    saved = list(DRESSERS)
    try:
        DRESSERS.append(lambda spec, said, language: {**spec, "place": "building"} if "building" in said else None)
        after = director.propose(plan, cfg)
    finally:
        DRESSERS[:] = saved
    assert before == director.propose(plan, cfg)                      # nothing registered: the same plan again
    drawn = [c for c in after if c.get("at_sentence") == 1]
    plain = [c for c in before if c.get("at_sentence") == 1]
    assert drawn and plain and all("place" not in str(c) for c in plain)
    assert any(c.get("place") == "building" or (c.get("fallback") or {}).get("place") == "building" for c in drawn)
