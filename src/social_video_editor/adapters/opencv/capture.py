"""Opening a video with OpenCV whatever characters its path has.

On Windows, OpenCV builds have failed to open paths outside the ANSI code page (C:\\Users\\José\\…),
depending on the version and the backend, and the failure is silent (an unopened capture: no frames).
When a non-ASCII path does not open, the file is opened by Python and handed to OpenCV as a stream
(OpenCV ≥ 4.11 reads from a file-like object through its FFmpeg backend), so the path never reaches
OpenCV's C++ string conversion. ASCII paths, and paths that open, take the plain path as before.
"""
from __future__ import annotations

from pathlib import Path


class StreamCapture:
    """A cv2.VideoCapture reading from an open Python file; release() also closes the file."""

    def __init__(self, cap, file):
        self._cap, self._file = cap, file

    @property
    def capture(self):
        return self._cap

    def release(self) -> None:
        try:
            self._cap.release()
        finally:
            self._file.close()

    def __getattr__(self, name):            # read, grab, retrieve, get, set, isOpened…
        return getattr(self._cap, name)


def _from_stream(path: str):
    import cv2

    try:
        f = open(path, "rb")                # noqa: SIM115 (closed by StreamCapture.release)
    except OSError:
        return None
    try:
        cap = cv2.VideoCapture(f, cv2.CAP_FFMPEG, [])
    except (TypeError, cv2.error):          # OpenCV older than 4.11: no stream input
        f.close()
        return None
    if not cap.isOpened():
        cap.release()
        f.close()
        return None
    return StreamCapture(cap, f)


def open_capture(video: str | Path):
    """cv2.VideoCapture of `video` (or a StreamCapture, same methods). Like OpenCV itself, a file that
    cannot be read gives an unopened capture rather than an error."""
    import cv2

    path = str(video)
    cap = cv2.VideoCapture(path)
    if cap.isOpened() or path.isascii():
        return cap
    stream = _from_stream(path)
    if stream is None:
        return cap
    cap.release()
    return stream


def needs_stream(cap) -> bool:
    """True when `cap` reads from a stream, so tools that reopen the path (PySceneDetect) would fail."""
    return isinstance(cap, StreamCapture)
