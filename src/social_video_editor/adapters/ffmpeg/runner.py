"""Running FFmpeg (and ffprobe): one place that starts the binaries and decodes their output.

FFmpeg prints UTF-8 (file names, metadata, ffprobe JSON); Python's default text encoding is the
locale's (cp1252 on most Windows systems), which would garble or fail on any non-ASCII name, so text
output is always decoded as UTF-8 with replacement characters for anything invalid.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from collections import deque

from ...domain.models import UserError
from ..system.tools import FFMPEG

try:                                    # the platform layer's install hint, when it provides one
    from ..system.tools import MISSING_FFMPEG
except ImportError:                     # pragma: no cover - older tools.py
    MISSING_FFMPEG = ("FFmpeg was not found. Install FFmpeg 7 or newer and make sure `ffmpeg` and `ffprobe` "
                      "are on the PATH (Windows: `winget install Gyan.FFmpeg`, then open a new terminal; "
                      "macOS: `brew install ffmpeg`; Linux: your package manager).")

TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def not_found(binary) -> UserError:
    """The user-facing error for an FFmpeg-family binary that cannot be started."""
    return UserError(f"{MISSING_FFMPEG} (could not start {binary})")


def call(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run of an FFmpeg / ffprobe command; a missing binary → UserError (not a traceback)."""
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as e:
        raise not_found(cmd[0]) from e


def popen(cmd: list, **kwargs) -> subprocess.Popen:
    """subprocess.Popen of an FFmpeg command; a missing binary → UserError."""
    try:
        return subprocess.Popen(cmd, **kwargs)
    except FileNotFoundError as e:
        raise not_found(cmd[0]) from e


class StderrDrain:
    """Reads a child's stderr pipe in a daemon thread while the caller writes stdin / reads stdout.

    Without it the child blocks once the stderr pipe fills (about 4 KiB on Windows, 64 KiB on Linux)
    while the parent blocks on the other pipe: a deadlock. Keeps the last `limit` bytes for messages.
    """

    def __init__(self, stream, limit: int = 64 * 1024):
        self._stream, self._limit = stream, limit
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, name="ffmpeg-stderr", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        read = getattr(self._stream, "read1", self._stream.read)
        try:
            while chunk := read(4096):
                with self._lock:
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    while self._size > self._limit and len(self._chunks) > 1:
                        self._size -= len(self._chunks.popleft())
        except (OSError, ValueError):       # pipe closed under us (the child was killed)
            pass

    def text(self, timeout: float | None = 10.0) -> str:
        """What the child wrote (decoded as UTF-8), once it closed stderr or after `timeout` seconds."""
        self._thread.join(timeout)
        with self._lock:
            data = b"".join(self._chunks)
        return data[-self._limit:].decode("utf-8", errors="replace")


def run(cmd: list, quiet: bool = True) -> subprocess.CompletedProcess:
    """Runs a command ("ffmpeg" is replaced by the real binary); on failure shows the end of the error."""
    cmd = [str(c) for c in cmd]
    if cmd[0] == "ffmpeg":
        cmd[0] = FFMPEG
        cmd[1:1] = ["-hide_banner", "-y", "-nostdin"]
        if quiet:
            cmd[1:1] = ["-loglevel", "error"]
    r = call(cmd, capture_output=True, **TEXT)
    if r.returncode != 0:
        sys.stderr.write("\n".join(r.stderr.splitlines()[-25:]) + "\n")
        raise RuntimeError(f"Failed: {' '.join(cmd[:6])} …")
    return r


def stderr_of(args: list) -> str:
    """Runs FFmpeg and returns its stderr (for measuring filters such as loudnorm)."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin", *[str(c) for c in args]]
    r = call(cmd, capture_output=True, **TEXT)
    if r.returncode != 0:
        raise RuntimeError("\n".join(r.stderr.splitlines()[-20:]))
    return r.stderr
