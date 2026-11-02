"""Mastering measured like `sve check`, robust mastering, drawn-shot audio, raw-frame encoding,
typing click density (synthetic media generated on the fly)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
from social_video_editor.adapters.ffmpeg.inspector import FfmpegRenderInspector
from social_video_editor.adapters.ffmpeg.loudness import ebur128
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe
from social_video_editor.adapters.files import legacy_es
from social_video_editor.adapters.system.tools import FFMPEG
from social_video_editor.domain.check import burst_rate, thin_keystrokes, typing_bursts


def ff(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def premaster(tmp_path: Path, name: str, expr: str, dur: float = 6) -> Path:
    """A premaster-like mkv: small video + stereo PCM audio from an aevalsrc expression."""
    out = tmp_path / f"{name}.mkv"
    ff("-f", "lavfi", "-i", f"color=c=gray:s=64x64:d={dur}:r=30",
       "-f", "lavfi", "-i", f"aevalsrc='{expr}':d={dur}:s=48000:c=stereo",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", "-shortest", out)
    return out


# loud first half, quieter second half: loudnorm's input_i (over-weights the last seconds of a short
# file) measured −30.3 here while BS.1770 says −29.1 → the old mastering ended at −12.8 LUFS
SHORT = "0.05*sin(2*PI*440*t)*lt(t,3)+0.02*sin(2*PI*660*t)*gte(t,3)"
# ASMR-like clicks over a quiet bed (before: −18.1 LUFS, very high crest factor)
CLICKS = "0.9*sin(2*PI*2500*t)*exp(-mod(t,0.25)*200)+0.01*sin(2*PI*150*t)"
# square-wave clicks: lots of high-frequency energy → AAC overshoots the true peak
SQUARE_CLICKS = "0.99*(2*gt(sin(2*PI*1500*t),0)-1)*exp(-mod(t,0.33)*90)"


@requires_ffmpeg
def test_mastering_and_check_measure_the_same_on_short_files(tmp_path, cfg):
    src = premaster(tmp_path, "short", SHORT)
    res = FfmpegEncoder().master_audio(src, tmp_path / "short.mp4", cfg)
    checked = FfmpegRenderInspector().loudness(tmp_path / "short.mp4")
    assert res["converged"] is True and "warning" not in res
    assert res["after_lufs"] == pytest.approx(checked["integrated"], abs=0.05)     # one shared measurement
    assert checked["integrated"] == pytest.approx(-14, abs=0.3)
    assert checked["true_peak"] <= -1.0
    assert not list(tmp_path.glob("short_*.wav"))                                   # temporaries removed


@requires_ffmpeg
@pytest.mark.parametrize("expr", [CLICKS, SQUARE_CLICKS], ids=["clicks", "square_clicks"])
def test_mastering_click_like_audio_reaches_target_below_true_peak(tmp_path, cfg, expr):
    src = premaster(tmp_path, "clicks", expr)
    res = FfmpegEncoder().master_audio(src, tmp_path / "clicks.mp4", cfg)
    final = ebur128(tmp_path / "clicks.mp4")
    assert res["converged"] is True
    assert final["integrated"] == pytest.approx(-14, abs=0.5)
    assert final["true_peak"] <= -1.0                                  # verified after the AAC encoding


@requires_ffmpeg
def test_mastering_always_limits_even_when_loudness_is_on_target(tmp_path, cfg):
    # already ≈ −14 LUFS but with a full-scale spike: the old code skipped the limiter
    src = premaster(tmp_path, "spike", "0.214*sin(2*PI*440*t)+0.75*sin(2*PI*3000*t)*between(t,2,2.004)")
    before = ebur128(src)
    assert abs(before["integrated"] + 14) < 0.5 and before["true_peak"] > -1
    res = FfmpegEncoder().master_audio(src, tmp_path / "spike.mp4", cfg)
    assert res["true_peak_dbtp"] <= -1.0 and ebur128(tmp_path / "spike.mp4")["true_peak"] <= -1.0
    assert res["converged"] is True


@requires_ffmpeg
def test_mastering_gain_cap_warns_when_it_cannot_converge(tmp_path, cfg):
    cfg["audio"]["master_max_gain_db"] = 6
    src = premaster(tmp_path, "quiet", "0.01*sin(2*PI*440*t)")          # ≈ −41 LUFS: needs +27 dB
    res = FfmpegEncoder().master_audio(src, tmp_path / "quiet.mp4", cfg)
    assert res["gain_db"] <= 6
    assert res["converged"] is False and "did not converge" in res["warning"]
    assert res["after_lufs"] < -30 and res["true_peak_dbtp"] <= -1.0


# ── raw-frame segments (polaroid / collage) ──────────────────────────────────

def _frames(n, W=64, H=64):
    for i in range(n):
        yield bytes([i % 256]) * (W * H * 3)


@requires_ffmpeg
def test_encode_frames_with_continuation_audio(tmp_path, cfg):
    src = tmp_path / "src.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=64x64:d=4:r=30", "-f", "lavfi", "-i", "sine=f=440:d=4:sample_rate=48000",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src)
    info = FfprobeMediaProbe().probe(src)
    cfg["audio"]["denoise"] = 0
    audio = {"info": info, "start": 2.0, "shot": {"speed": 1.0}, "voice": False, "cfg": cfg,
             "gain_db": -6, "fade": 0.12}
    enc = FfmpegEncoder()
    with_audio = enc.encode_frames(_frames(30), 30, 64, 64, 30, tmp_path / "a.mkv", audio)
    silent = enc.encode_frames(_frames(30), 30, 64, 64, 30, tmp_path / "s.mkv")
    insp = FfmpegRenderInspector()
    x, s = insp.audio(with_audio), insp.audio(silent)
    assert len(x) == pytest.approx(48000, abs=480)                        # the timeline stays identical
    assert np.abs(s).max() == 0
    mid = np.sqrt(np.mean(x[12000:36000] ** 2))
    assert mid > 0.01                                                      # it is the source's sound…
    assert np.abs(x[:48]).max() < mid                                      # … faded in
    assert np.abs(x[-48:]).max() < mid                                     # … and out
    # a source too short to continue → silence padded to the full length (never shorter)
    late = enc.encode_frames(_frames(30), 30, 64, 64, 30, tmp_path / "l.mkv", {**audio, "start": 3.8})
    assert len(insp.audio(late)) == pytest.approx(48000, abs=480)


@requires_ffmpeg
def test_encode_frames_surfaces_ffmpeg_errors_and_cleans_up(tmp_path):
    enc = FfmpegEncoder()
    with pytest.raises(RuntimeError, match="FFmpeg failed"):              # wrong frame size → broken pipe
        enc.encode_frames(_frames(5, 7, 7), 5, 64, 64, 30, tmp_path / "missing_dir" / "x.mkv")

    def boom():
        yield bytes(64 * 64 * 3)
        raise ValueError("composer failed")
    with pytest.raises(ValueError, match="composer failed"):              # FFmpeg killed, no leftovers
        enc.encode_frames(boom(), 10, 64, 64, 30, tmp_path / "y.mkv")
    assert not list(tmp_path.glob("y*"))


# ── typing click density ─────────────────────────────────────────────────────

def test_typing_sounds_thinned_to_the_max_rate():
    keys = [1 + i / 27 for i in range(40)] + [4 + i / 25 for i in range(12)] + [6 + i / 10 for i in range(5)]
    thin = thin_keystrokes(keys, 16)
    assert set(thin) <= set(keys) and thin == thin_keystrokes(keys, 16)           # subset, deterministic
    bursts = typing_bursts(thin)
    assert len(bursts) == 3 and all(burst_rate(b) <= 16 for b in bursts)
    assert bursts[0][0] == 1 and bursts[0][1] == pytest.approx(keys[39], abs=1e-3)  # whole burst still clicks
    assert burst_rate(bursts[0]) > 14                                              # evenly thinned, not halved
    assert thin[-5:] == keys[-5:]                                                  # slow typing untouched
    rng = np.random.default_rng(3)
    for _ in range(300):
        ks = sorted(rng.uniform(0, 5, rng.integers(0, 120)).tolist())
        assert all(burst_rate(b) <= 16 for b in typing_bursts(thin_keystrokes(ks, 16)))
    assert thin_keystrokes(keys, 0) == keys


def test_typing_max_per_sec_config_and_legacy_key(cfg):
    assert cfg["sfx"]["typing_max_per_sec"] == 16
    assert cfg["sfx"]["typing_max_per_sec"] < cfg["check"]["typing_max_per_sec"]
    es = legacy_es.config({"sfx": {"tecleo_max_por_seg": 12},
                           "audio": {"ganancia_max_master_db": 20, "dibujadas_db": -8}})
    assert es["sfx"]["typing_max_per_sec"] == 12
    assert es["audio"] == {"master_max_gain_db": 20, "drawn_shot_db": -8}
