"""The shot zoom grows about the focus point (0.5.0 and earlier: always from the top-left corner,
because the crop after a per-frame scale kept the first frame's size), and shake moves the picture."""
from __future__ import annotations

import subprocess

import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe
from social_video_editor.adapters.system.tools import FFMPEG

cv2 = pytest.importorskip("cv2")
W, H, FPS = 360, 640, 30
WM, HM = 450, 800          # work_size(W, H) with the default zoom_margin 1.25: the source is not rescaled
PUSH = 0.10


@pytest.fixture(scope="module")
def still(tmp_path_factory):
    """A static textured picture at the working size (W × H × zoom_margin) as a lossless 3 s clip: every
    motion in a segment comes from the zoom / crop, and the expected crops can be computed from it."""
    d = tmp_path_factory.mktemp("still")
    png, clip = d / "still.png", d / "still.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={WM}x{HM}:r=1:d=1", "-frames:v", "1", str(png)], check=True)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-r", str(FPS), "-i", str(png),
                    "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", str(clip)],
                   check=True)
    first = subprocess.run([FFMPEG, "-v", "error", "-i", str(clip), "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return FfprobeMediaProbe().probe(clip), np.frombuffer(first, np.uint8).reshape(HM, WM)    # decoded like segments


def _segment(tmp_path, cfg, info, **shot) -> np.ndarray:
    shot = {"start": 0.0, "end": 2.0, "dur": 2.0, "index": 0, **shot}
    cfg = {**cfg, "framing": {**cfg["framing"], "slow_push": PUSH}, "color": {**cfg["color"], "sharpen": 0, "vibrance": 0}}
    out = FfmpegEncoder().encode_shot(shot, info, cfg, {}, False, W, H, False, tmp_path / "seg.mkv")
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def _crop(picture: np.ndarray, k: int, fx: float, fy: float) -> np.ndarray:
    """Frame k of a slow push done by hand: the picture scaled to W·Z × H·Z (even sizes), cropped at the focus."""
    z = 1 + PUSH * (k / FPS) / 2.0
    sw, sh = int(W * z / 2) * 2, int(H * z / 2) * 2
    big = cv2.resize(picture, (sw, sh), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    x, y = int((sw - W) * fx), int((sh - H) * fy)
    return big[y:y + H, x:x + W]


def _offset(frame: np.ndarray, expected: np.ndarray) -> float:
    """How far (pixels) the frame's picture is from the expected crop (phase correlation)."""
    (dx, dy), _ = cv2.phaseCorrelate(expected, frame)
    return float(np.hypot(dx, dy))


@requires_ffmpeg
@pytest.mark.parametrize("fx, fy", [(0.5, 0.5), (1.0, 0.0), (0.2, 0.9)])
def test_slow_push_zooms_about_the_focus_point(tmp_path, cfg, still, fx, fy):
    info, picture = still
    f = _segment(tmp_path, cfg, info, focus_x=fx, focus_y=fy)
    k = len(f) - 1
    assert _offset(f[k], _crop(picture, k, fx, fy)) < 1.5
    assert _offset(f[k], _crop(picture, k, 0.0, 0.0)) > 8          # 0.5.0 always zoomed from the top-left


@requires_ffmpeg
def test_shake_moves_the_picture(tmp_path, cfg, still):
    info, _ = still
    calm = _segment(tmp_path, cfg, info)
    shaken = _segment(tmp_path, cfg, info, effects=[{"type": "shake", "t": 0.5, "dur": 0.5, "amplitude": 14}])
    k = int(0.6 * FPS)
    assert np.abs(calm[:10] - shaken[:10]).mean() < 1.0             # the same before the shake (x264 noise)
    (dx, dy), _ = cv2.phaseCorrelate(calm[k], shaken[k])
    assert np.hypot(dx, dy) > 2
