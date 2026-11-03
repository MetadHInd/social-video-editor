"""Linux portability: font lookup in nested font folders, missing emoji font, recipe descriptions,
decoder threads of shot segments (FFmpeg 7.1)."""
from __future__ import annotations

from social_video_editor.adapters.files.recipes import header_description
from social_video_editor.adapters.pillow import rich_text as rt
from social_video_editor.adapters.system import fonts


def _clear_caches():
    for cached in (fonts._index, fonts.label_font, fonts.emoji_font, rt._emoji_font, rt._emoji_image):
        cached.cache_clear()


def _fake_linux_fonts(tmp_path, monkeypatch, files):
    """Font folders that contain only `files` (paths relative to a fake /usr/share/fonts)."""
    root = tmp_path / "share_fonts"
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"")
    monkeypatch.setattr(fonts, "FONT_DIRS", [root])
    monkeypatch.setattr(fonts, "LINUX_FONT_DIRS", [root])
    _clear_caches()
    monkeypatch.chdir(tmp_path)
    return root


def test_fonts_in_linux_subfolders_and_fallbacks(tmp_path, monkeypatch):
    root = _fake_linux_fonts(tmp_path, monkeypatch, ["opentype/inter/InterDisplay-Black.otf",
                                                     "truetype/dejavu/DejaVuSans-Bold.ttf",
                                                     "truetype/noto/NotoColorEmoji.ttf"])
    try:
        assert fonts.find_font_file("InterDisplay-Black") == (root / "opentype/inter/InterDisplay-Black.otf", 0)
        assert fonts.find_font_file("interdisplay-black:1")[1] == 1                       # case-insensitive
        # missing (macOS-only) fonts fall back to an installed bold sans-serif
        assert fonts.find_font_file("Bradley Hand Bold") == (root / "truetype/dejavu/DejaVuSans-Bold.ttf", 0)
        assert fonts.label_font() == root / "truetype/dejavu/DejaVuSans-Bold.ttf"
        assert fonts.emoji_font() == root / "truetype/noto/NotoColorEmoji.ttf"
    finally:
        _clear_caches()


def test_without_any_font_text_and_emoji_still_render(tmp_path, monkeypatch):
    _fake_linux_fonts(tmp_path, monkeypatch, [])
    try:
        assert fonts.find_font_file("Inter-Black") == (None, 0)
        assert fonts.emoji_font() is None
        im = rt._emoji_image("👀", 40)                         # no emoji font: an empty square, no crash
        assert im.size == (40, 40) and im.getbbox() is None
    finally:
        _clear_caches()


def test_recipe_description_skips_decorative_comment_lines():
    text = "# ─────────────\n#\n#   ==== \n# BASE RECIPE: defaults\n# more\n\nlanguage: es\n"
    assert header_description(text) == "BASE RECIPE: defaults"
    assert header_description("# RECIPE: MINE\nduration: {target: 9}\n") == "RECIPE: MINE"
    assert header_description("# ────\nlanguage: es\n# RECIPE: too late\n") == ""      # only the header
    assert header_description("") == ""


def test_packaged_recipe_descriptions_are_meaningful(recipes):
    for name in recipes.available():
        desc = recipes.description(name)
        assert desc and any(ch.isalpha() for ch in desc), name
        assert "─" not in desc and not desc.startswith("#"), name
    assert recipes.description("base").startswith("BASE RECIPE")


def test_shot_segments_cap_the_decoder_threads(tmp_path, monkeypatch, cfg):
    """FFmpeg 7.1 can deadlock when a frame-threaded decoder (default: cores + 1 threads) holds 5+ frames
    while the shot's audio chain buffers (afftdn) and pads (apad): the shot input gets a fixed count."""
    from social_video_editor.adapters.ffmpeg import encoder as enc_mod

    for name in ("prescale", "sdr_chain", "correction_chain", "motion", "flash_filters"):
        monkeypatch.setattr(enc_mod.filters, name, lambda *a, **k: "")
    built = []

    def capture(build, run):
        built.append([str(x) for x in build(False)])
        raise RuntimeError("stop: command captured")

    monkeypatch.setattr(enc_mod, "run_with_fallback", capture)
    shot = {"start": 0.5, "end": 2.5, "dur": 2.0, "index": 0}
    info = {"path": "clip.mp4", "has_audio": True, "audio_index": 1}
    try:
        enc_mod.FfmpegEncoder().encode_shot(shot, info, cfg, {}, False, 540, 960, True, tmp_path / "s.mkv")
    except RuntimeError:
        pass
    cmd = built[0]
    j = cmd.index("-threads")
    assert j < cmd.index("-i") and cmd[j + 1] == enc_mod.SEGMENT_DECODER_THREADS == "2"
