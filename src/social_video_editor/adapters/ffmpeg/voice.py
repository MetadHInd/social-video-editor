"""A recording's voice cleaned once, whole (audio.clean_voice): background noise (a fan, the room, the
street) taken out by RNNoise — FFmpeg's `arnndn`, a small neural network made for speech — over the
entire source, so the noise reduction never restarts at a cut (per-shot denoising leaves audible seams).

The model (≈300 KB, the "somnolent-hogwash" model of rnnoise-models: recorded noise, speech signal;
not subject to copyright per its authors) is looked up in the user cache (<cache>/models/) and
downloaded there once. Without it (offline), FFmpeg's FFT denoiser (`afftdn`) is used, also on the
whole recording, which takes out less.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from ...log import log
from ..system.tools import user_cache_dir
from .escape import filter_path
from .runner import run

RNNOISE_FILE = "rnnoise_sh.rnnn"
RNNOISE_URL = ("https://raw.githubusercontent.com/GregorR/rnnoise-models/master/"
               "somnolent-hogwash-2018-09-01/sh.rnnn")
RNNOISE_MIN_BYTES = 100_000        # a failed download (an error page) is smaller
DOWNLOAD_TIMEOUT = 30
OFFLINE_ENV = "SVE_OFFLINE"        # set (1): nothing is downloaded; the FFT denoiser is used
FALLBACK = "afftdn=nr=25:nf=-28:tn=1"


def rnnoise_path() -> Path:
    return user_cache_dir() / "models" / RNNOISE_FILE


def rnnoise_model(download: bool = True) -> Path | None:
    """The RNNoise model in the user cache (downloaded once when missing and `download`), or None."""
    dest = rnnoise_path()
    if dest.is_file() and dest.stat().st_size >= RNNOISE_MIN_BYTES:
        return dest
    if not download or os.environ.get(OFFLINE_ENV, "").strip() not in ("", "0"):
        return None
    log(f"Voice: downloading the noise-reduction model ({RNNOISE_FILE}, ≈300 KB, once) to {dest.parent}…")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(RNNOISE_URL, timeout=DOWNLOAD_TIMEOUT) as r:     # noqa: S310 (fixed https URL)
            tmp.write_bytes(r.read())
        if tmp.stat().st_size < RNNOISE_MIN_BYTES:
            tmp.unlink(missing_ok=True)
            raise OSError("the file downloaded is too small")
        tmp.replace(dest)
        return dest
    except OSError as e:
        log(f"Warning: could not download the noise-reduction model ({e}); using FFmpeg's FFT denoiser instead.")
        return None


def clean_chain(model: Path | None, mix: float = 1.0, highpass: int = 80) -> str:
    """The cleaning filters: rumble out, then RNNoise (or the FFT denoiser without its model)."""
    f = ["aresample=48000"]
    if highpass:
        f.append(f"highpass=f={int(highpass)}")
    if model is not None:
        f.append(f"arnndn=m={filter_path(model)}:mix={min(max(float(mix), 0.0), 1.0):.2f}")
    else:
        f.append(FALLBACK)
    return ",".join(f)


def clean_voice(info: dict, out: Path, model: Path | None, mix: float = 1.0, highpass: int = 80) -> Path:
    """The source's whole audio track cleaned → out (48 kHz stereo WAV, same timing as the source)."""
    tmp = out.with_name(out.stem + ".part.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["ffmpeg", "-i", info["path"], "-map", f"0:{info['audio_index']}", "-af", clean_chain(model, mix, highpass),
             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", tmp])
        tmp.replace(out)
        return out
    finally:
        tmp.unlink(missing_ok=True)
