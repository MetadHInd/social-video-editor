"""Running FFmpeg."""
from __future__ import annotations

import subprocess
import sys

from ..system.tools import FFMPEG


def run(cmd: list, quiet: bool = True) -> subprocess.CompletedProcess:
    """Runs a command ("ffmpeg" is replaced by the real binary); on failure shows the end of the error."""
    cmd = [str(c) for c in cmd]
    if cmd[0] == "ffmpeg":
        cmd[0] = FFMPEG
        cmd[1:1] = ["-hide_banner", "-y", "-nostdin"]
        if quiet:
            cmd[1:1] = ["-loglevel", "error"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("\n".join(r.stderr.splitlines()[-25:]) + "\n")
        raise RuntimeError(f"Failed: {' '.join(cmd[:6])} …")
    return r


def stderr_of(args: list) -> str:
    """Runs FFmpeg and returns its stderr (for measuring filters such as loudnorm)."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin", *[str(c) for c in args]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("\n".join(r.stderr.splitlines()[-20:]))
    return r.stderr
