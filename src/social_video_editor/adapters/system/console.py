"""Console output that never crashes on characters the terminal cannot encode (→, ⚠, emoji…).

Windows consoles and redirected output often use a legacy code page (cp1252, cp850…), where printing
"→" raises UnicodeEncodeError. Unencodable characters are replaced instead; redirected output on
Windows is written as UTF-8 so log files and pipes keep every character.
"""
from __future__ import annotations

import codecs
import os
import sys


def _is_utf8(encoding: str | None) -> bool:
    try:
        return codecs.lookup(encoding or "").name == "utf-8"
    except LookupError:
        return False


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def configure_console(environ=None, platform: str | None = None) -> None:
    """Reconfigures sys.stdout / sys.stderr (when they support it) so printing never raises.

    An explicit PYTHONIOENCODING is respected (only the error handler changes); a Windows pipe or file
    that is not UTF-8 becomes UTF-8; everything else keeps its encoding and replaces what it cannot encode."""
    env = os.environ if environ is None else environ
    plat = platform or sys.platform
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:            # replaced by a test harness or an IDE, nothing to do
            continue
        try:
            if env.get("PYTHONIOENCODING"):
                reconfigure(errors="replace")
            elif plat == "win32" and not _isatty(stream) and not _is_utf8(getattr(stream, "encoding", None)):
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(errors="replace")
        except (OSError, ValueError):      # e.g. a closed stream
            continue
