"""Linux portability: font lookup in nested font folders, missing emoji font, recipe descriptions,
FFmpeg version detection and the decoder-thread cap of shot segments (FFmpeg 7.0 - 8.0)."""
from __future__ import annotations

import pytest

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



def test_ffmpeg_version_parsing():
    from social_video_editor.adapters.system.tools import parse_ffmpeg_version as parse

    assert parse("ffmpeg version 7.1.1-1ubuntu4.2 Copyright (c) 2000-2025") == (7, 1)
    assert parse("ffmpeg version n8.1-12-gabcdef Copyright") == (8, 1)
    assert parse("ffmpeg version 8.0 Copyright") == (8, 0)
    assert parse("ffmpeg version N-118000-gabcdef Copyright") is None             # git snapshot
    assert parse("ffmpeg version 2025-01-15-git-abc-full_build") is None
    assert parse("") is None


@pytest.mark.parametrize("version, override, expected", [
    ((7, 0), None, "2"), ((7, 1), None, "2"), ((8, 0), None, "2"),      # can deadlock: capped
    ((8, 1), None, None), ((9, 0), None, None), ((6, 1), None, None),   # FFmpeg's own default
    (None, None, "2"),                                                  # unknown version: safe side
    ((7, 1), "auto", None), ((7, 1), "0", None), ((9, 0), " 6 ", "6"), ((8, 1), "1", "1"),
])
def test_segment_decoder_threads(version, override, expected):
    from social_video_editor.adapters.ffmpeg.encoder import segment_decoder_threads

    assert segment_decoder_threads(version, override) == expected


@pytest.mark.parametrize("bad", ["-1", "65", "2; rm -rf /", "two", "1.5"])
def test_segment_decoder_threads_rejects_bad_overrides(bad):
    from social_video_editor.adapters.ffmpeg.encoder import segment_decoder_threads
    from social_video_editor.domain.models import UserError

    with pytest.raises(UserError, match="SVE_DECODER_THREADS"):
        segment_decoder_threads((9, 0), bad)


def _shot_command(tmp_path, monkeypatch, cfg, version, env=None) -> list[str]:
    """The FFmpeg command encode_shot builds (filters stubbed, nothing is run)."""
    from social_video_editor.adapters.ffmpeg import encoder as enc_mod

    for name in ("prescale", "sdr_chain", "correction_chain", "motion", "flash_filters"):
        monkeypatch.setattr(enc_mod.filters, name, lambda *a, **k: "")
    monkeypatch.setattr(enc_mod, "ffmpeg_version", lambda: version)
    if env is None:
        monkeypatch.delenv("SVE_DECODER_THREADS", raising=False)
    else:
        monkeypatch.setenv("SVE_DECODER_THREADS", env)
    built = []

    def capture(build, run):
        built.append([str(x) for x in build(False)])
        raise RuntimeError("stop: command captured")

    monkeypatch.setattr(enc_mod, "run_with_fallback", capture)
    shot = {"start": 0.5, "end": 2.5, "dur": 2.0, "index": 0}
    info = {"path": "clip.mp4", "has_audio": True, "audio_index": 1}
    with pytest.raises(RuntimeError, match="captured"):
        enc_mod.FfmpegEncoder().encode_shot(shot, info, cfg, {}, False, 540, 960, True, tmp_path / "s.mkv")
    return built[0]


def _input_threads(cmd: list[str]) -> str | None:
    i = cmd.index("-i")
    return cmd[cmd.index("-threads") + 1] if "-threads" in cmd[:i] else None


def test_shot_segments_cap_decoder_threads_only_where_ffmpeg_deadlocks(tmp_path, monkeypatch, cfg):
    """Regression: FFmpeg 7.0 - 8.0 can hang forever on a shot whose source decoder runs 5+ frame threads
    while its audio chain buffers (afftdn) and pads (apad). There the shot input gets 2 threads."""
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (7, 1))) == "2"
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (8, 0))) == "2"
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (8, 1))) is None
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (8, 1), env="4")) == "4"
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (7, 1), env="auto")) is None
