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
