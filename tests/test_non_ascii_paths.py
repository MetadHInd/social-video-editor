"""Videos in folders whose names are not ASCII (C:\\Users\\José\\…): OpenCV readers and scene detection
work the same as with an ASCII path, also when OpenCV cannot open the path itself (as some Windows
builds do), in which case the file is read as a stream."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.opencv import capture
from social_video_editor.adapters.opencv.video_analyzer import OpenCvVideoAnalyzer
from social_video_editor.adapters.system.tools import FFMPEG

cv2 = pytest.importorskip("cv2")

FOLDER = "José ñ 日本 – clips"


def _clip(path) -> None:
    """4 s at 10 fps: a test pattern, then a hard cut to a flat color (one scene change at 2 s; scenes
    are at least 15 frames long)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=s=160x96:d=2:r=10",
                    "-f", "lavfi", "-i", "color=c=0x3366cc:s=160x96:d=2:r=10",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


@pytest.fixture
def clips(tmp_path):
    ascii_clip = tmp_path / "plain" / "clip.mp4"
    _clip(ascii_clip)
    other = tmp_path / FOLDER / "clip ñ.mp4"
    other.parent.mkdir()
    shutil.copyfile(ascii_clip, other)
    return ascii_clip, other


def _readings(video) -> dict:
    a = OpenCvVideoAnalyzer()
    read = a.frame_reader(str(video))
    try:
        frame = read(3.0)
    finally:
        read.close()
    return {"samples": a.samples(str(video), 4), "frames": [f.mean() for f in a.read_frames(str(video), [0.5, 2.8])],
            "reader": None if frame is None else float(frame.mean()), "scenes": a.scenes(str(video), 27)}


def _assert_same(got: dict, want: dict) -> None:
    assert len(got["samples"]) == len(want["samples"]) > 0
    assert got["samples"] == want["samples"]
    assert got["frames"] == pytest.approx(want["frames"]) and len(got["frames"]) == 2
    assert got["reader"] is not None and got["reader"] == pytest.approx(want["reader"])
    assert got["scenes"] == want["scenes"]


@requires_ffmpeg
def test_opencv_reads_a_video_in_a_non_ascii_folder(clips):
    ascii_clip, other = clips
    want = _readings(ascii_clip)
    assert want["scenes"] and want["scenes"][0][1] == pytest.approx(2.0, abs=0.11)   # the cut was found
    _assert_same(_readings(other), want)


@requires_ffmpeg
def test_a_path_opencv_cannot_open_is_read_as_a_stream(clips, monkeypatch):
    """Windows-like OpenCV: a non-ASCII path string gives an unopened capture (silently)."""
    ascii_clip, other = clips
    want = _readings(ascii_clip)
    real = cv2.VideoCapture

    def windows_like(src=None, *args):
        if isinstance(src, str) and not src.isascii():
            return real()                                   # unopened, like the failing builds
        return real() if src is None else real(src, *args)

    monkeypatch.setattr(cv2, "VideoCapture", windows_like)
    cap = capture.open_capture(other)
    try:
        if not capture.needs_stream(cap):
            pytest.skip("this OpenCV cannot read from a Python file object (needs 4.11+)")
        assert cap.isOpened() and cap.read()[0]
    finally:
        cap.release()
    _assert_same(_readings(other), want)


def test_ascii_paths_and_missing_files_keep_opencvs_own_behavior(tmp_path):
    cap = capture.open_capture(tmp_path / "missing.mp4")
    assert not capture.needs_stream(cap) and not cap.isOpened()
    cap = capture.open_capture(tmp_path / FOLDER / "missing.mp4")
    assert not capture.needs_stream(cap) and not cap.isOpened()
