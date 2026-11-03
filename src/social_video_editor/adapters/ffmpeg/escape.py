"""The ONE place that escapes file paths for FFmpeg text syntaxes (filter graphs, ffconcat lists).

A value inside a filter option (`lut3d=file=<value>`) is parsed twice by FFmpeg:

  1. graph level (avfilter_graph_parse, av_get_token with the terminators "[],;"): single quotes
     enclose a literal run (no escapes inside), a backslash outside quotes escapes the next char;
  2. option level (av_opt_set_from_string → av_get_token with "=" and ":"): the same rules again
     on the text the graph level produced.

filter_value() therefore escapes the option-level specials (backslash, quote, colon) with a
backslash, then wraps the result in single quotes for the graph level, closing and reopening the
quotes around every literal quote (`'\\''`). The same string works in `-vf` / `-af` arguments and
in `-/filter_complex` script files (the script's content is parsed exactly like the argument).
Checked with FFmpeg 7.1 and 8.1 on folders named like `x y'z,[1];:`, `a\\b` and `semi;colon:=x`.

On Windows a path's backslashes become forward slashes (FFmpeg and the Windows API accept both,
and a backslash would otherwise need one more escaping layer); on POSIX a backslash is a legal
filename character and is escaped like any other special.

Known limit: av_get_token strips unquoted leading/trailing whitespace at the option level, and the
escaping above leaves the option-level token unquoted, so a path that begins or ends with spaces
(never produced by this engine; an absolute path begins with "/" or a drive letter) would lose
them.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_OPTION_SPECIALS = re.compile(r"([\\':])")


def _norm(path, windows: bool | None = None) -> str:
    """The path as text FFmpeg reads: forward slashes on Windows, unchanged on POSIX."""
    windows = os.name == "nt" if windows is None else windows
    s = str(path)
    return s.replace("\\", "/") if windows else s


def filter_value(s: str) -> str:
    """Any text as ONE filter option value (both parsing levels escaped)."""
    lvl1 = _OPTION_SPECIALS.sub(r"\\\1", str(s))           # option level: \ ' :
    return "'" + lvl1.replace("'", "'\\''") + "'"           # graph level: quote, literal ' outside quotes


def filter_path(path, windows: bool | None = None) -> str:
    """A file path as a filter option value: f"lut3d=file={filter_path(p)}" (`~` expanded)."""
    return filter_value(_norm(Path(path).expanduser(), windows))


def concat_path(path, windows: bool | None = None) -> str:
    """A path for an ffconcat `file` directive (one quoting level: single quotes, `'\\''` for a quote)."""
    return "'" + _norm(path, windows).replace("'", "'\\''") + "'"
