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
import subprocess
import urllib.request
from pathlib import Path

from ...domain import voice_levels as vl
from ...log import log
from ..system.tools import FFMPEG, user_cache_dir
from .escape import filter_path
from .runner import StderrDrain, popen, run

RNNOISE_FILE = "rnnoise_sh.rnnn"
RNNOISE_URL = ("https://raw.githubusercontent.com/GregorR/rnnoise-models/master/"
               "somnolent-hogwash-2018-09-01/sh.rnnn")
RNNOISE_MIN_BYTES = 100_000        # a failed download (an error page) is smaller
DOWNLOAD_TIMEOUT = 30
OFFLINE_ENV = "SVE_OFFLINE"        # set (1): nothing is downloaded; the FFT denoiser is used
FALLBACK = "afftdn=nr=25:nf=-28:tn=1"
LEVELER = "dynaudnorm=f=300:g=15:p=0.9:m=2.5:s=0"   # audio.voice_leveler: slow, gentle (≈15 frames of 0.3 s)


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


def clean_chain(model: Path | None, mix: float = 1.0, highpass: int = 80, leveler: bool = False) -> str:
    """The cleaning filters: rumble out, then RNNoise (or the FFT denoiser without its model), then
    (audio.voice_leveler) a slow leveler over the whole recording."""
    f = ["aresample=48000"]
    if highpass:
        f.append(f"highpass=f={int(highpass)}")
    if model is not None:
        f.append(f"arnndn=m={filter_path(model)}:mix={min(max(float(mix), 0.0), 1.0):.2f}")
    else:
        f.append(FALLBACK)
    if leveler:
        f.append(LEVELER)
    return ",".join(f)


def clean_voice(info: dict, out: Path, model: Path | None, mix: float = 1.0, highpass: int = 80,
                polish: dict | None = None) -> Path:
    """The source's whole audio track cleaned → out (48 kHz stereo WAV, same timing as the source).

    polish (domain.voice_levels.polish_settings): "leveler" adds the slow leveler; "breaths" lowers the
    pauses between sentences (breaths, mouth noise) of the cleaned voice, away from the speech and from
    the "words" ([start, end] source seconds) — also over the whole recording, so no cut restarts it.
    The breath counts are written next to out (out.json) and logged.
    """
    polish = polish or {}
    tmp = out.with_name(out.stem + ".part.wav")
    lowered = out.with_name(out.stem + ".breaths.wav")
    env = out.with_name(out.stem + ".env.f32")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["ffmpeg", "-i", info["path"], "-map", f"0:{info['audio_index']}",
             "-af", clean_chain(model, mix, highpass, bool(polish.get("leveler"))),
             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", tmp])
        if polish.get("breaths"):
            lower_breaths(tmp, lowered, env, polish["breaths"], polish.get("words"), out.with_suffix(".json"))
            lowered.replace(out)
        else:
            tmp.replace(out)
        return out
    finally:
        for f in (tmp, lowered, env):
            f.unlink(missing_ok=True)


def lower_breaths(src: Path, out: Path, env: Path, settings: dict, words: list | None = None,
                  stats_file: Path | None = None) -> dict:
    """The pauses of a voice track lowered (voice_levels.breath_regions) → out; returns the counts."""
    import json

    db = frame_levels(src)
    spans = [{"start": w[0], "end": w[1]} for w in words or []]
    regions = vl.breath_regions(db, settings, spans)
    stats = {**vl.breath_stats(db, regions), "depth_db": settings.get("depth_db"), "words": len(spans)}
    write_envelope(vl.gain_curve(regions, len(db), float(settings["depth_db"]), float(settings["fade"])), env)
    run(["ffmpeg", "-i", src, *envelope_input(env), "-filter_complex", multiply("[0:a]", "[1:a]", "v"),
         "-map", "[v]", "-t", f"{len(db) * vl.FRAME + 0.05:.3f}", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", out])
    if stats_file is not None:
        stats_file.write_text(json.dumps(stats), encoding="utf-8")
    log(f"Voice: {stats['pauses']} pauses lowered {float(settings['depth_db']):g} dB "
        f"({stats['with_sound']} with a breath or mouth noise, {stats['seconds']} s)")
    return stats


# ── levels and gain envelopes (breaths, music under the voice) ─────────────────

LEVEL_RATE = 16000          # Hz of the mono decode the frame levels are measured on
ENVELOPE_RATE = 1000        # Hz of a gain envelope (smooth: resampled to 48 kHz inside FFmpeg)


def frame_levels(path: Path | str, stream: str = "0:a:0", start: float = 0.0, duration: float | None = None):
    """RMS level (dBFS) of the audio, mono, every voice_levels.FRAME seconds (decoded in blocks:
    never the whole recording in memory as samples)."""
    import numpy as np

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{float(start):.3f}"]
    if duration is not None:
        cmd += ["-t", f"{float(duration):.3f}"]
    cmd += ["-i", str(path), "-map", stream, "-ac", "1", "-ar", str(LEVEL_RATE), "-f", "f32le", "-"]
    hop = int(round(LEVEL_RATE * vl.FRAME))
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    drain = StderrDrain(p.stderr)
    out, rest = [], np.zeros(0, np.float32)
    try:
        while buf := p.stdout.read(hop * 4 * 1000):
            x = np.concatenate([rest, np.frombuffer(buf[:len(buf) // 4 * 4], "<f4")])
            n = len(x) // hop
            if n:
                out.append(10 * np.log10(np.mean(x[:n * hop].reshape(n, hop).astype(np.float64) ** 2, axis=1) + 1e-12))
            rest = x[n * hop:]
    finally:
        p.stdout.close()
        code = p.wait()
    if code != 0:
        raise RuntimeError(f"could not measure the audio of {Path(path).name}: {drain.text()[-400:]}")
    return np.concatenate(out) if out else np.zeros(0)


def write_envelope(gain_db, out: Path, tail: float = 2.0) -> Path:
    """Gain per FRAME (dB) → raw mono float32 file at ENVELOPE_RATE (linear gains), the last value
    held `tail` seconds longer (so the envelope never ends before the audio it multiplies)."""
    import numpy as np

    g = np.asarray(gain_db, dtype=np.float64)
    if not len(g):
        g = np.zeros(1)
    g = np.concatenate([g, np.full(int(tail / vl.FRAME), g[-1])])
    t_frames = (np.arange(len(g)) + 0.5) * vl.FRAME
    t = np.arange(int(len(g) * vl.FRAME * ENVELOPE_RATE)) / ENVELOPE_RATE
    lin = (10 ** (np.interp(t, t_frames, g) / 20)).astype("<f4")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(lin.tobytes())
    return out


def envelope_input(env: Path) -> list:
    """FFmpeg input options of an envelope file written by write_envelope."""
    return ["-f", "f32le", "-ar", str(ENVELOPE_RATE), "-ac", "1", "-i", env]


def multiply(audio: str, env: str, label: str) -> str:
    """Filter: audio label × envelope label (48 kHz stereo) → label."""
    return (f"{env}aresample=48000,pan=stereo|c0=c0|c1=c0,aformat=sample_fmts=flt[{label}_g];"
            f"{audio}aformat=sample_fmts=flt:sample_rates=48000:channel_layouts=stereo[{label}_a];"
            f"[{label}_a][{label}_g]amultiply[{label}]")
