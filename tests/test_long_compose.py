"""Long videos: chunked compose (many segments) and the audio-only mastering passes."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe
from social_video_editor.adapters.system.tools import FFMPEG

FPS = 30
SEG = 0.5            # seconds per tiny segment (15 frames)


def tiny_segments(folder: Path, n: int) -> list[Path]:
    """n segments of SEG s at 160×90 with stereo PCM audio, cut from one synthetic clip."""
    folder.mkdir(parents=True, exist_ok=True)
    dur = n * SEG
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s=160x90:r={FPS}:d={dur}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={dur}:r=48000",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-force_key_frames", f"expr:gte(t,n_forced*{SEG})", "-c:a", "pcm_s16le", "-ac", "2",
                    "-f", "segment", "-segment_time", str(SEG), "-reset_timestamps", "1",
                    str(folder / "seg_%03d.mkv")], check=True)
    files = sorted(folder.glob("seg_*.mkv"))
    assert len(files) == n
    return files


def timeline(n: int) -> tuple[list[dict], float]:
    """Alternating hard cuts and 0.1 s fades (3 frames); returns (segments, total length)."""
    segs, total = [], 0.0
    for i in range(n):
        tr = "fade" if i % 2 else "cut"
        ov = 0.1 if (i and tr == "fade") else 0.0
        segs.append({"dur": SEG, "overlap": ov, "transition": tr})
        total += SEG - ov
    return segs, total


@requires_ffmpeg
def test_chunked_compose_keeps_the_total_duration(tmp_path, cfg):
    n = 100
    files = tiny_segments(tmp_path / "segs", n)
    segs, total = timeline(n)
    cfg = {**cfg, "output": {**cfg["output"], "fps": FPS},
           "render": {"chunk_threshold": 60, "chunk_size": 40, "chunk_min_seconds": 0}}
    enc, probe = FfmpegEncoder(), FfprobeMediaProbe()
    chunked = enc.compose(segs, files, None, None, None, cfg, 160, 90, total, False, True,
                          tmp_path, tmp_path / "chunked.mkv")
    assert probe.duration(chunked) == pytest.approx(total, abs=1.5 / FPS)
    assert not (tmp_path / "_chunks").exists() or not list((tmp_path / "_chunks").glob("*.mkv"))   # cleaned
    # same length as the single-graph path (chunking disabled)
    single_cfg = {**cfg, "render": {"chunk_threshold": 0}}
    single = enc.compose(segs, files, None, None, None, single_cfg, 160, 90, total, False, True,
                         tmp_path, tmp_path / "single.mkv")
    assert probe.duration(single) == pytest.approx(probe.duration(chunked), abs=1.5 / FPS)


@requires_ffmpeg
def test_mastering_ignores_the_removed_long_master_setting(tmp_path, cfg):
    src = tmp_path / "pre.mkv"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=6:r=30",
                    "-f", "lavfi", "-i", "aevalsrc='0.05*sin(2*PI*440*t)':d=6:s=48000:c=stereo",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", "-shortest", str(src)],
                   check=True)
    cfg = {**cfg, "render": {"long_master_sec": 1}}      # old key (0.5.0 and earlier): harmless
    res = FfmpegEncoder().master_audio(src, tmp_path / "out.mp4", cfg)
    assert res["converged"] is True
    assert res["after_lufs"] == pytest.approx(-14, abs=0.5)
    probe = FfprobeMediaProbe().probe(tmp_path / "out.mp4")
    assert probe["has_audio"] and probe["width"] == 64
    assert not list(tmp_path.glob("pre_*"))                       # temporaries removed
