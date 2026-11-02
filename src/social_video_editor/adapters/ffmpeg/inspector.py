"""RenderInspector with FFmpeg: EBU R128 loudness series, decoded audio, black/frozen frames, snippets."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from ..system.tools import FFMPEG
from .loudness import ebur128
from .runner import run, stderr_of


class FfmpegRenderInspector:
    def loudness(self, path: Path) -> dict:
        """{"integrated", "true_peak", "lra", "t": […], "momentary": […], "short_term": […]} (every 0.1 s)."""
        return ebur128(path, series=True)       # the same measurement the mastering uses

    def audio(self, path: Path, sr: int = 48000) -> np.ndarray:
        """First audio stream decoded to float32, shape (samples, 2): no downmix (it would add up the
        channels and fake clipping) and no clipping (float samples may exceed 1)."""
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
                            "-map", "0:a:0", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
                           capture_output=True, check=True)
        return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32).reshape(-1, 2)

    def video_events(self, path: Path, black_min: float, freeze_min: float) -> dict:
        """{"black": [[start, end]], "freeze": [[start, end]]} in one decoding pass (scaled down)."""
        err = stderr_of(["-nostats", "-i", str(path), "-map", "0:v:0", "-vf",
                         f"scale=270:-2,blackdetect=d={black_min}:pix_th=0.10,"
                         f"freezedetect=n=-60dB:d={freeze_min}", "-f", "null", "-"])
        black = [[float(a), float(b)] for a, b in re.findall(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", err)]
        starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", err)]
        ends = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", err)]
        dur = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
        total = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else None
        freeze = [[s, ends[i] if i < len(ends) else total] for i, s in enumerate(starts)]
        return {"black": black, "freeze": freeze}

    def snippet(self, path: Path, start: float, dur: float, out: Path) -> Path:
        """A short wav of the video's audio (16-bit, 48 kHz) with 10 ms fades."""
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path, "-map", "0:a:0", "-af",
             f"afade=t=in:d=0.01,afade=t=out:st={max(dur - 0.01, 0):.3f}:d=0.01",
             "-c:a", "pcm_s16le", "-ar", "48000", out])
        return out
