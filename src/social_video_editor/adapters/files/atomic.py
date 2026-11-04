"""Files written under a temporary name and published with an atomic replace, so a half-written file
never looks finished and two writers of the same output (threads or processes) never share a file."""
from __future__ import annotations

import itertools
import os
import threading
import time
from pathlib import Path

_WINDOWS = os.name == "nt"
_COUNTER = itertools.count()          # next() on a count is atomic in CPython


def temp_name(out: Path) -> Path:
    """A temporary name next to `out`, unique per process and call (thread ids are reused once a thread
    ends, hence the counter), keeping the extension (FFmpeg picks the muxer from it, numpy and Pillow
    the format)."""
    out = Path(out)
    return out.with_name(f"{out.stem}.{os.getpid()}-{threading.get_ident()}-{next(_COUNTER)}.tmp{out.suffix}")


def publish(tmp: Path, out: Path, attempts: int = 6) -> Path:
    """Atomically moves a finished temporary file to `out` (replacing it). On Windows a file that was
    just closed can stay locked for a moment (antivirus, indexer, a reader of the old `out`), so
    PermissionError is retried there with a short back-off (1.55 s in all)."""
    for k in range(attempts):
        try:
            os.replace(tmp, out)
            return Path(out)
        except PermissionError:
            if not _WINDOWS or k == attempts - 1:
                raise
            time.sleep(0.05 * 2 ** k)
    return Path(out)
