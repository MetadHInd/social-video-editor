"""Loose ends: the public exact font lookup, the lower third clear of landscape subtitles."""
from __future__ import annotations

from social_video_editor.adapters.system import fonts
from social_video_editor.domain.graphics.templates import LOWER_THIRD_LANDSCAPE_BOTTOM
from social_video_editor.domain.graphics import make_scene
from test_graphics import LANDSCAPE, VERTICAL, stage


def test_exact_font_lookup_is_public_and_never_falls_back(tmp_path):
    font = tmp_path / "MyFont.ttf"
    font.write_bytes(b"\0")
    assert fonts.font_file_exact(str(font)) == font
    assert fonts.font_file_exact("No-Such-Font-At-All") is None
    from social_video_editor.adapters.skia import renderer
    assert renderer.font_file_exact(f"{font}:1") == font            # a face index is not part of the name


def test_lower_third_stays_clear_of_landscape_subtitles_and_is_unchanged_in_vertical():
    spec = {"template": "lower_third", "text": "Official channel", "kicker": "Video", "dur": 3}
    wide = make_scene(spec, stage(LANDSCAPE))
    assert wide.y + wide.h <= LOWER_THIRD_LANDSCAPE_BOTTOM * 1080 + 1e-6 < 0.77 * 1080       # subtitles start at 0.77·H
    tall = make_scene(spec, stage(VERTICAL))
    st = stage(VERTICAL)
    assert tall.y + tall.h == st.safe[3]                              # vertical: the bottom of the safe zone, as before


def test_report_names_who_heard_the_words_the_shorts_and_what_the_sound_passes_did(workspace, capsys):
    import json

    import report_projects as rp
    from social_video_editor.cli import main

    d = rp.busy_explainer(workspace)
    main(["report", d.name])
    out = capsys.readouterr().out
    assert "SOURCES 1 · 06:40.00 total · words: faster-whisper small (CPU (int8))" in out
    assert "SHORTS 2 (1 proposed ~) · packets: sentences 1–4" in out
    assert "~ auto-1: sentences 10–16" in out and "starts with a question" in out
    assert "breaths: 34 pauses lowered 12 dB (21 with a breath or mouth noise, 9.6 s)" in out
    assert "music: 10 dB under the voice while it speaks, 4 dB in pauses" in out
    assert "PRODUCTS" not in out                                     # an explainer never detects products
    analysis = d / "cache" / "lesson_0_analysis.json"
    analysis.write_text(json.dumps({"words": []}), encoding="utf-8")     # analyzed before the engine was recorded
    main(["report", d.name])
    assert "words: faster-whisper (not recorded)" in capsys.readouterr().out


def test_report_says_how_products_are_named(workspace, capsys):
    import report_projects as rp
    from social_video_editor.cli import main

    d = rp.unboxing(workspace)
    main(["report", d.name])
    out = capsys.readouterr().out
    assert "named by clip · ⚠ 4 low confidence: names unsure" in out
