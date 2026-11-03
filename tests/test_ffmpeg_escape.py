"""Escaping of file paths for FFmpeg filter options and ffconcat lists (pure string tests).

The real FFmpeg parses these strings in tests/test_integration.py (lut3d / concat in odd folders)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.adapters.ffmpeg.escape import concat_path, filter_path, filter_value


def _unescape_option(s: str) -> str:
    """What FFmpeg's two parsing levels (graph, then option; av_get_token rules) make of a value."""
    def token(text: str, specials: str) -> str:
        out, i, quoted = [], 0, False
        while i < len(text):
            c = text[i]
            if quoted:
                if c == "'":
                    quoted = False
                else:
                    out.append(c)
            elif c == "'":
                quoted = True
            elif c == "\\" and i + 1 < len(text):
                i += 1
                out.append(text[i])
            else:
                assert c not in specials, f"unescaped {c!r} would end the token"
                out.append(c)
            i += 1
        assert not quoted
        return "".join(out)
    return token(token(s, "[],;"), ":")


def test_windows_path_becomes_forward_slashes_with_the_drive_colon_escaped():
    assert filter_path(r"C:\Users\x y\a'b.cube", windows=True) == r"'C\:/Users/x y/a\'\''b.cube'"
    assert concat_path(r"C:\Users\x y\a'b.mkv", windows=True) == r"'C:/Users/x y/a'\''b.mkv'"


ODD_PATHS = ["/home/u/x y'z,[1];:/l'ut.cube", "/tmp/a\\b/c.cube", "/tmp/q''q/semi;colon:=x.cube",
             "/tmp/ünï ✓/l.cube", "/tmp/%d/l.cube", "/tmp/plain/lut.cube"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths (a Windows Path turns / into \\)")
@pytest.mark.parametrize("path", ODD_PATHS)
def test_posix_paths_round_trip_through_both_parsing_levels(path):
    esc = filter_path(path, windows=False)
    assert _unescape_option(esc) == path                       # backslash kept: a real filename char on POSIX


@pytest.mark.parametrize("path", ODD_PATHS + [r"C:\Users\x y\a'b.cube", r"D:\[1];,x\l.cube"])
def test_windows_paths_round_trip_with_forward_slashes(path):
    assert _unescape_option(filter_path(path, windows=True)) == path.replace("\\", "/")


@pytest.mark.parametrize("text", ["a:b", "it's", "x\\y", "[0:v];[1:v]", "a,b", "''", ""])
def test_filter_value_round_trips(text):
    assert _unescape_option(filter_value(text)) == text


def test_filter_path_expands_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))            # Windows
    assert filter_path("~/looks/warm.cube") == filter_path(tmp_path / "looks" / "warm.cube")


def test_concat_path_quotes_quotes_and_keeps_names():
    assert concat_path("c00001.png", windows=False) == "'c00001.png'"
    assert concat_path("a'b c.mkv", windows=False) == "'a'\\''b c.mkv'"
    assert concat_path("/x/a\\b.mkv", windows=False) == "'/x/a\\b.mkv'"     # inside quotes: literal


def test_lut_sites_use_the_escaped_path(tmp_path):
    lut = tmp_path / "it's [a]; b:c.cube"
    assert f"lut3d=file={filter_path(lut)}" in filters.creative_chain({"lut": str(lut)})
    assert "'" + str(Path(lut)) + "'" not in filters.creative_chain({"lut": str(lut)})   # the old (broken) quoting
