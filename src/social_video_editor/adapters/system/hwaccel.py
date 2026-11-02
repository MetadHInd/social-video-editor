"""Hardware acceleration (VideoToolbox on macOS) with automatic fallback to software.

Detection asks FFmpeg which hwaccels/encoders it has; SVE_HWACCEL=0 forces software.
If a hardware command still fails at runtime, the caller retries in software (see run_with_fallback).
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from functools import lru_cache

from ...log import log
from .tools import FFMPEG


@lru_cache(maxsize=1)
def videotoolbox_available() -> bool:
    if os.environ.get("SVE_HWACCEL", "").lower() in ("0", "no", "none", "off", "false"):
        return False
    try:
        acc = subprocess.run([FFMPEG, "-hide_banner", "-hwaccels"], capture_output=True, text=True, timeout=20)
        enc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return "videotoolbox" in acc.stdout and "h264_videotoolbox" in enc.stdout


def decode_args(hw: bool) -> list[str]:
    return ["-hwaccel", "videotoolbox"] if hw else []


def proxy_encoder_args(hw: bool, bitrate: str = "3M") -> list[str]:
    if hw:
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate]
    return ["-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate]


def run_with_fallback(build: Callable[[bool], list[str]], run: Callable[[list[str]], object]):
    """Runs build(hw=True) when VideoToolbox is available; on failure retries build(hw=False)."""
    if videotoolbox_available():
        try:
            return run(build(True))
        except RuntimeError:
            log("VideoToolbox failed; retrying in software")
    return run(build(False))
