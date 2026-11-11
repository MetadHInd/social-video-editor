"""Graphics drawn for real (Skia), packs, the `sve graphic` / `sve doctor` commands and a render made
of graphic shots. Everything is synthetic and written to the temporary workspace."""
from __future__ import annotations

import json

import numpy as np
import pytest
import yaml
from PIL import Image

from conftest import requires_ffmpeg
from social_video_editor import cli
from social_video_editor.adapters.files import packs as packs_module
from social_video_editor.adapters.files.packs import FilePackRepository
from social_video_editor.adapters.system import doctor
from social_video_editor.application import graphic
from social_video_editor.container import build_services
from social_video_editor.domain.graphics import ELEMENTS, TEMPLATES, build_theme, make_scene, motion
from social_video_editor.domain.models import PlanError, UserError

pytest.importorskip("skia", reason="the graphics extra (skia-python) is not installed or cannot load", exc_type=ImportError)
from social_video_editor.adapters.skia.renderer import SkiaGraphicRenderer  # noqa: E402

SMALL = (270, 480)            # a quarter-size vertical frame: fast to draw


@pytest.fixture
def renderer() -> SkiaGraphicRenderer:
    return SkiaGraphicRenderer()


@pytest.fixture
def registries():
    """Packs register templates and elements for the whole process: put the registries back afterwards."""
    elements, templates, loaded = dict(ELEMENTS), dict(TEMPLATES), dict(packs_module._loaded)
    yield
    ELEMENTS.clear(), ELEMENTS.update(elements)
    TEMPLATES.clear(), TEMPLATES.update(templates)
    packs_module._loaded.clear(), packs_module._loaded.update(loaded)


def write_pack(workspace, name="channel", theme=None, module: str | None = None):
    folder = workspace / "packs" / name
    folder.mkdir(parents=True)
    (folder / "pack.yaml").write_text(yaml.safe_dump({"theme": theme or {"colors": {"accent": "#FF0000"}}}), encoding="utf-8")
    if module is not None:
        (folder / "elements.py").write_text(module, encoding="utf-8")
    return folder


# ── drawing ──────────────────────────────────────────────────────────────────
def test_frames_are_rgb_of_the_stage_size_and_text_is_measured_with_a_real_font(renderer):
    assert renderer.ready() is None
    st = renderer.stage(*SMALL, build_theme())
    assert 0 < st.measure("Hello", "label", 40) < st.measure("Hello world", "label", 40)
    scene = make_scene({"template": "bars", "data": "A:15, B:40", "unit": "%", "dur": 1.0}, st)
    frames = list(renderer.frames(scene, 30, 30))
    assert len(frames) == 30 and all(len(f) == SMALL[0] * SMALL[1] * 3 for f in frames)
    assert frames[0] != frames[10]                                   # it moves
    still = renderer.still(scene, 0.9)
    assert still.shape == (SMALL[1], SMALL[0], 3) and still.std() > 5      # not a flat picture


def test_each_state_of_a_graphic_is_drawn_once(renderer, monkeypatch):
    st = renderer.stage(*SMALL, build_theme({"motion": {"on_twos": True, "boil": True}}))
    scene = make_scene({"template": "number", "value": "376", "dur": 6}, st)
    draws = []
    paint = scene.paint
    monkeypatch.setattr(scene, "paint", lambda cv, t: (draws.append(t), paint(cv, t))[1])
    assert sum(1 for _ in renderer.frames(scene, 30, 180)) == 180
    entrance = motion.ticks(scene.settle) + 2
    assert len(draws) <= entrance + motion.LOOP_TICKS                # 180 frames, far fewer drawings
    assert len(draws) < 90


def test_grain_changes_the_pixels_but_not_the_picture(renderer):
    spec = {"template": "title", "text": "Hello", "dur": 1}
    plain = renderer.still(make_scene(spec, renderer.stage(*SMALL, build_theme({"motion": {"ambient": False}}))), 0.9)
    grainy = renderer.still(make_scene(spec, renderer.stage(*SMALL, build_theme({"motion": {"ambient": False},
                                                                                 "grain": {"strength": 0.2}}))), 0.9)
    diff = np.abs(plain.astype(int) - grainy.astype(int))
    assert diff.max() > 0 and diff.mean() < 12


def test_overlay_is_transparent_around_its_box(renderer):
    st = renderer.stage(960, 540, build_theme())
    scene = make_scene({"template": "lower_third", "text": "Official channel", "kicker": "Video", "dur": 2}, st)
    pictures = list(renderer.overlay_frames(scene, 30, 40))
    middle = pictures[30]
    assert middle.shape == (540, 960, 4)
    assert middle[10, 10, 3] == 0 and middle[:, :, 3].max() == 255      # clear corner, solid box
    assert pictures[0][:, :, 3].max() == 0                               # closed on its first frame


def test_variable_font_axes_and_missing_fonts_fall_back(renderer, tmp_path):
    theme = build_theme({"fonts": {"display": {"file": str(tmp_path / "missing.ttf"), "wght": 900}, "label": "no-such-font"}})
    st = renderer.stage(*SMALL, theme)
    assert st.measure("Hello", "display", 40) > 0 and st.measure("Hello", "label", 40) > 0


def test_product_photo_cut_out_is_trimmed_and_missing_photo_is_an_error(renderer, tmp_path):
    cut_out = tmp_path / "product.png"
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    img.paste((200, 60, 40, 255), (150, 100, 250, 200))               # a 100 × 100 object with wide empty margins
    img.save(cut_out)
    st = renderer.stage(*SMALL, build_theme({"sticker": {"border": 6}, "stroke": {"outline": 3}}))
    assert st.image_size(str(cut_out)) == (100, 100)
    scene = make_scene({"template": "product_card", "name": "Thing", "image": str(cut_out), "specs": "Size:2 TB"}, st)
    still = renderer.still(scene, scene.dur)
    reddish = (still[:, :, 0] > 170) & (still[:, :, 1] < 90) & (still[:, :, 2] < 70)
    assert reddish.sum() > 500                                        # the object is on the picture, enlarged
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (300, 200), (20, 120, 200)).save(photo)
    renderer.still(make_scene({"template": "product_card", "name": "Thing", "image": str(photo)}, st), 1.0)
    with pytest.raises(PlanError, match="image not found"):
        make_scene({"template": "product_card", "name": "Thing", "image": str(tmp_path / "nothing.png")}, st)


def test_sheets_pictures_and_the_sample_product(renderer, tmp_path):
    sample = renderer.sample_image(tmp_path / "out" / "sample.png")
    assert Image.open(sample).mode == "RGBA"
    pics = [np.full((40, 30, 3), v, np.uint8) for v in (10, 120, 250)]
    sheet = Image.open(renderer.save_sheet(pics, tmp_path / "out" / "sheet.png", columns=2, tile_width=60))
    assert sheet.size == (126, 166)
    assert Image.open(renderer.save_picture(pics[1], tmp_path / "deep" / "one.png")).size == (30, 40)


# ── packs ────────────────────────────────────────────────────────────────────
def test_pack_gives_a_theme_and_registers_its_elements(workspace, registries, renderer):
    folder = write_pack(workspace, theme={"colors": {"accent": "#FF0000"}, "fonts": {"label": "fonts/missing.ttf"}},
                        module="from social_video_editor.domain.graphics import element\n"
                               "@element('marker')\n"
                               "def marker(cv, st, x, y, t=0.0, **options):\n"
                               "    cv.fill(__import__('social_video_editor.domain.graphics.paths', fromlist=['circle'])"
                               ".circle(x, y, 9), '#00FF00')\n")
    repo = FilePackRepository()
    assert repo.available() == ["channel"]
    before = ELEMENTS["marker"]
    pack = repo.load("channel")
    assert pack["dir"] == folder and pack["theme"]["colors"]["accent"] == "#FF0000"
    assert ELEMENTS["marker"] is not before
    marker = ELEMENTS["marker"]
    repo.load("channel")                                              # the module runs once per process
    assert ELEMENTS["marker"] is marker
    svc = build_services()
    theme = graphic.graphics_theme(svc, {"graphics": {"pack": "channel", "theme": {"colors": {"bg": "#000001"}}}})
    assert theme["colors"]["accent"] == "#FF0000" and theme["colors"]["bg"] == "#000001"      # default ← pack ← project
    assert graphic.graphics_theme(svc, {"graphics": {"pack": None, "theme": {}}})["colors"]["accent"] != "#FF0000"


def test_pack_font_paths_are_relative_to_the_pack(workspace, registries, tmp_path):
    folder = write_pack(workspace, theme={"fonts": {"display": {"file": "fonts/a.ttf", "wght": 900}, "label": "fonts/a.ttf",
                                                    "number": "Some Installed Font"}})
    (folder / "fonts").mkdir()
    (folder / "fonts" / "a.ttf").write_bytes(b"not a real font")
    fonts = FilePackRepository().load("channel")["theme"]["fonts"]
    assert fonts["display"] == {"file": str((folder / "fonts" / "a.ttf").resolve()), "wght": 900}
    assert fonts["label"] == str((folder / "fonts" / "a.ttf").resolve()) and fonts["number"] == "Some Installed Font"


def test_missing_or_broken_pack_says_so(workspace, registries):
    write_pack(workspace, "good")
    with pytest.raises(UserError, match="Pack 'nope' not found.*Available: good"):
        FilePackRepository().load("nope")
    write_pack(workspace, "broken", module="raise RuntimeError('boom')\n")
    with pytest.raises(UserError, match="elements.py failed to load: RuntimeError: boom"):
        FilePackRepository().load("broken")


# ── commands ─────────────────────────────────────────────────────────────────
def test_graphic_command_previews_a_cue_in_spanish(workspace, capsys):
    cli.main(["graphic", "--cue", 'barras titulo="Batería" datos="Antes:15, Hoy:40" unidad=%', "--out", str(workspace / "out")])
    out = capsys.readouterr().out.split()
    assert len(out) == 4 and all(p.endswith(".png") for p in out)
    assert Image.open(workspace / "out" / "cue_vertical.png").size == (1080, 1920)
    assert Image.open(workspace / "out" / "cue_landscape.png").size == (1920, 1080)


def test_graphic_command_needs_something_to_draw(workspace):
    with pytest.raises(UserError, match="Say what to draw"):
        cli.main(["graphic"])
    with pytest.raises(UserError, match="--shot needs a project"):
        cli.main(["graphic", "--shot", "1"])


def test_style_proof_shows_every_template_in_both_orientations(workspace, registries):
    write_pack(workspace, theme={"motion": {"on_twos": True}})
    svc = build_services()
    cfg = {**svc.recipes.load("base"), "graphics": {"pack": "channel", "theme": {}}}
    paths = graphic.proof(svc, cfg, workspace / "proof")
    assert [p.name for p in paths] == ["proof_vertical.png", "proof_landscape.png"]
    stills = sorted(p.name for p in (workspace / "proof" / "stills").iterdir())
    assert stills == sorted(f"{name}_{o}.png" for name in TEMPLATES for o in ("vertical", "landscape"))


def test_doctor_reports_parts_and_packs(workspace, capsys):
    write_pack(workspace)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "FFmpeg" in out and "graphics: animated graphics" in out and "Packs: channel" in out
    assert out.strip().splitlines()[-1].startswith(("Ready", "1 problem", "2 problem"))


def test_doctor_lines_count_problems_and_optional_parts():
    lines = doctor.doctor_lines([("ok", "A", "fine"), ("missing", "B", "gone"), ("optional", "C", "not set up")])
    assert lines == ["OK  A: fine", "!!  B: gone", "--  C: not set up", "1 problem(s) to fix · 1 optional part(s) not set up"]
    assert doctor.doctor_lines([("ok", "A", "fine")])[-1] == "Ready"
    listing = " ... xfade             VV->V      Cross fade\n T.. overlay           VV->V      Overlay\n"
    assert doctor.listed_names(listing) == {"xfade", "overlay"}
    assert doctor.part_checks()[-1][1] == "speech to text"


# ── a video made of graphics ─────────────────────────────────────────────────
@pytest.mark.integration
@requires_ffmpeg
def test_render_a_plan_of_graphic_shots(workspace):
    project = workspace / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(yaml.safe_dump({
        "name": "demo", "recipe": "base", "sources": [],
        "settings": {"output": {"width": 360, "height": 640}, "music": {"file": "none"}, "subtitles": {"auto": False},
                     "export": {"cover": False}}}), encoding="utf-8")
    (project / "plan.json").write_text(json.dumps({"shots": [
        {"type": "graphic", "template": "title", "text": "Hello world", "dur": 1.0},
        {"tipo": "grafico", "plantilla": "barras", "datos": "A:15, B:40", "unidad": "%", "dur": 1.5},      # Spanish keys
    ], "texts": []}), encoding="utf-8")
    cli.main(["render", "demo"])
    version = project / "v1"
    summary = json.loads((version / "summary.json").read_text(encoding="utf-8"))
    assert summary["duration_sec"] == 2.5 and summary["shots"] == 2 and summary["sound_effects"] >= 2
    assert [s["type"] for s in summary["shot_details"]] == ["graphic", "graphic"]
    used = json.loads((version / "plan_used.json").read_text(encoding="utf-8"))
    assert used["shots"][1]["template"] == "bars" and used["shots"][1]["_sounds"][0][0] == "whoosh"
    segments = sorted(p.name for p in (project / "cache" / "segments").glob("graphic_*.mkv"))
    assert len(segments) == 2
    cli.main(["render", "demo"])                                       # unchanged graphics are not drawn again
    assert sorted(p.name for p in (project / "cache" / "segments").glob("graphic_*.mkv")) == segments
    cli.main(["report", "demo"])
    cli.main(["graphic", "demo", "--shot", "2"])
    assert (project / "cache" / "graphics" / "shot2_vertical.png").exists()
