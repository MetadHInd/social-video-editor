"""Progress messages (always English)."""
from __future__ import annotations

import sys
import threading
import time

_lock = threading.Lock()


def log(msg: str) -> None:
    """One whole line per message, even from worker threads; never raises on unencodable characters
    (a Windows console with a legacy code page cannot print "→" or "⚠")."""
    line = f"[sve {time.strftime('%H:%M:%S')}] {msg}\n"
    with _lock:
        out = sys.stdout
        if out is None:                    # pythonw / GUI launchers have no console
            return
        try:
            out.write(line)
        except UnicodeEncodeError:
            enc = getattr(out, "encoding", None) or "ascii"
            out.write(line.encode(enc, errors="replace").decode(enc, errors="replace"))
        out.flush()
