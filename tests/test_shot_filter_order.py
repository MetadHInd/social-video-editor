"""Shot segments apply speed + output frame rate before scaling: the dropped frames of a 60 fps source
are never processed, and the segment is the same as with the frame rate converted at the end."""
from __future__ import annotations

import subprocess

import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import encoder as enc_mod
from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe
from social_video_editor.adapters.ffmpeg.runner import run
from social_video_editor.adapters.system.tools import FFMPEG

W, H = 180, 320


def _command(monkeypatch, tmp_path, cfg, shot, info) -> list[str]:
    """The software command encode_shot builds (nothing is run)."""
    built = []

    def capture(build, _run):
        built.append([str(x) for x in build(False)])
        raise RuntimeError("stop: command captured")

    monkeypatch.setattr(enc_mod, "run_with_fallback", capture)
    with pytest.raises(RuntimeError, match="captured"):
        enc_mod.FfmpegEncoder().encode_shot(shot, info, cfg, {}, False, W, H, False, tmp_path / "s.mkv")
    monkeypatch.undo()
    return built[0]


def _frames(path) -> list[str]:
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


def test_shot_filters_start_with_speed_and_frame_rate(monkeypatch, tmp_path, cfg):
    shot = {"start": 0.5, "end": 2.5, "dur": 1.6, "index": 0, "speed": 1.25}
    info = {"path": "clip.mp4", "width": 2160, "height": 3840, "has_audio": False}
    vf = _command(monkeypatch, tmp_path, cfg, shot, info)
    vf = vf[vf.index("-vf") + 1]
    assert vf.startswith("setpts=(PTS-STARTPTS)/1.2500,fps=30,scale=")


def _ssim(a, b) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(r.stderr.rsplit("All:", 1)[1].split()[0])


@requires_ffmpeg
@pytest.mark.parametrize("rate, speed", [(60, 1.0), (30, 1.0), (25, 1.25)])
def test_early_frame_rate_keeps_the_segment(monkeypatch, tmp_path, cfg, rate, speed):
    """Identical to converting the frame rate at the end (0.5.0's order) when the source frames fall on
    the output grid (30 / 60 fps at speed 1); otherwise the zoom is evaluated at the output frame's own
    time instead of the source frame's (less than a frame apart), so the segments differ very little."""
    src = tmp_path / f"src{rate}.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=s=360x640:r={rate}:d=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    info = FfprobeMediaProbe().probe(src)
    dur = round(2.0 / speed, 4)
    shot = {"start": 0.4, "end": 2.4, "dur": dur, "index": 1, "speed": speed, "freeze": 0.2,
            "effects": [{"type": "beat_pulse"}, {"type": "zoom_punch", "t": 0.6}, {"type": "flash", "t": 1.0}],
            "_beats": [0.3, 0.8, 1.3]}
    new = _command(monkeypatch, tmp_path, cfg, shot, info)
    i = new.index("-vf")
    setpts = f"setpts=(PTS-STARTPTS)/{speed:.4f}"
    head = f"{setpts},fps={cfg['output']['fps']},"
    assert new[i + 1].startswith(head)
    motion = filters.motion(W, H, cfg, shot, shot["dur"], shot["index"])
    rest = new[i + 1][len(head):]
    assert rest.count(motion) == 1
    old = list(new)                      # 0.5.0's order: speed before the zoom, frame rate at the very end
    old[i + 1] = rest.replace(motion, f"{setpts}," + motion)
    files = []
    for name, cmd in (("new.mkv", new), ("old.mkv", old)):
        files.append(tmp_path / name)
        run([*cmd[:-1], str(files[-1])])
    a, b = _frames(files[0]), _frames(files[1])
    assert len(a) == len(b) >= round(shot["dur"] * cfg["output"]["fps"]) - 1
    if rate in (30, 60) and speed == 1.0:
        assert a == b
    else:
        assert a != b and _ssim(*files) > 0.97
