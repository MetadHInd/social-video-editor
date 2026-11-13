"""The recording's voice cleaned once, whole (audio.clean_voice; adapters/ffmpeg/voice.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import voice
from social_video_editor.adapters.files import legacy_es
from social_video_editor.adapters.system.tools import FFMPEG


def test_the_cleaning_chain():
    assert voice.clean_chain(None, highpass=80) == f"aresample=48000,highpass=f=80,{voice.FALLBACK}"
    chain = voice.clean_chain(Path("/models/a b.rnnn"), mix=1.4, highpass=0)
    assert chain.startswith("aresample=48000,arnndn=m=") and chain.endswith(":mix=1.00") and "highpass" not in chain
    assert legacy_es.config({"audio": {"voz_limpia": True, "mezcla_limpieza": 0.8}}) == {
        "audio": {"clean_voice": True, "clean_mix": 0.8}}


def test_offline_nothing_is_downloaded(monkeypatch):
    assert voice.rnnoise_model() is None                      # the tests run with SVE_OFFLINE=1
    monkeypatch.setattr(voice.urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded"))
    assert voice.rnnoise_model(download=True) is None


def _level(path: Path, a: float, b: float) -> float:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", str(path), "-ac", "1",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9))


@requires_ffmpeg
def test_the_whole_recording_is_cleaned_and_keeps_its_timing(tmp_path):
    # 4 s of steady noise with a tone ("the voice") from 1 to 3 s
    src = tmp_path / "rec.mov"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=4:c=pink:r=48000:a=0.03",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=2:sample_rate=48000",
                    "-filter_complex", "[1]adelay=1000|1000,volume=0.5[v];[0][v]amix=inputs=2:normalize=0",
                    "-c:a", "pcm_s16le", str(src)], check=True)
    out = voice.clean_voice({"path": str(src), "audio_index": 0}, tmp_path / "voice" / "clean.wav", None)
    assert out.is_file() and not list(out.parent.glob("*.part*"))
    before, after = _level(src, 0.2, 0.9), _level(out, 0.2, 0.9)     # the noise alone
    assert after < before - 3
    assert abs(_level(out, 1.3, 2.7) - _level(src, 1.3, 2.7)) < 3      # the "voice" stays
    probe = subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True).stderr
    assert "48000 Hz, stereo" in probe and "00:00:04.00" in probe
