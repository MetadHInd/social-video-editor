"""Sound effects: <workspace>/sfx/<name>.wav, generated when missing (replace them with your own).

Names: whoosh, pop, flash (FFmpeg synthesis), keystroke, shutter (numpy synthesis).
Legacy Spanish files (tecla.wav, obturador.wav) are used if present.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...domain.models import UserError
from ..files.legacy_es import legacy_sfx_name
from .runner import run

SR = 48000
_FFMPEG_SFX = {
    "whoosh": ["-f", "lavfi", "-i", "anoisesrc=color=pink:d=0.45:a=0.6:r=48000",
               "-af", "bandpass=f=1400:w=1800,afade=t=in:d=0.18:curve=exp,afade=t=out:st=0.18:d=0.27,volume=1.6"],
    "pop": ["-f", "lavfi", "-i",
            "aevalsrc='0.7*sin(2*PI*(500+1600*exp(-t*45))*t)*exp(-t*28)':d=0.14:s=48000"],
    "flash": ["-f", "lavfi", "-i",
              "aevalsrc='0.35*sin(2*PI*1760*t)*exp(-t*7)+0.2*sin(2*PI*2637*t)*exp(-t*9)"
              "+0.12*sin(2*PI*3520*t)*exp(-t*12)':d=0.7:s=48000"],
}


def _synthesize(name: str, path: Path) -> bool:
    """Sounds made with numpy (more natural than with FFmpeg)."""
    import numpy as np
    import soundfile as sf

    rng = np.random.default_rng(7)
    if name == "keystroke":      # soft keyboard key
        n = int(SR * 0.045)
        t = np.arange(n) / SR
        noise = rng.standard_normal(n) * np.exp(-t * 160)
        tone = 0.5 * np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 260)
        x = np.convolve(noise, np.ones(6) / 6, mode="same") + tone
    elif name == "shutter":      # photo camera: two mechanical clicks + air
        n = int(SR * 0.32)
        t = np.arange(n) / SR
        x = np.zeros(n)
        for t0, a in ((0.0, 1.0), (0.11, 0.7)):
            m = t >= t0
            tt = t[m] - t0
            x[m] += a * (rng.standard_normal(m.sum()) * np.exp(-tt * 90)
                         + 0.6 * np.sin(2 * np.pi * 1800 * tt) * np.exp(-tt * 140))
        x += 0.15 * rng.standard_normal(n) * np.exp(-t * 18)
    else:
        return False
    x = 0.8 * x / (np.abs(x).max() + 1e-9)
    sf.write(path, np.stack([x, x], 1).astype("float32"), SR)
    return True


class FfmpegSoundEffects:
    def __init__(self, folder: Callable[[], Path]):
        self._folder = folder

    def path(self, name: str) -> Path:
        folder = self._folder()
        folder.mkdir(exist_ok=True)
        path = folder / f"{name}.wav"
        legacy = legacy_sfx_name(name)
        if not path.exists() and legacy and (folder / f"{legacy}.wav").exists():
            return folder / f"{legacy}.wav"
        if not path.exists() and not _synthesize(name, path):
            if name not in _FFMPEG_SFX:
                raise UserError(f"Sound effect sfx/{name}.wav does not exist")
            run(["ffmpeg", *_FFMPEG_SFX[name], "-ac", "2", "-ar", "48000", path])
        return path

    def mix_track(self, events: list[tuple[str, float, float]], total: float, out: Path) -> Path:
        """Mixes all sound effects (name, second, gain dB) into ONE track."""
        import numpy as np
        import soundfile as sf

        track = np.zeros((int((total + 1) * SR), 2), dtype="float32")
        loaded = {}
        for name, t, gain_db in events:
            if name not in loaded:
                x, r = sf.read(self.path(name), dtype="float32", always_2d=True)
                if r != SR:
                    import librosa
                    x = librosa.resample(x.T, orig_sr=r, target_sr=SR).T
                loaded[name] = x if x.shape[1] == 2 else np.repeat(x[:, :1], 2, 1)
            x = loaded[name] * (10 ** (gain_db / 20))
            i = int(max(t, 0) * SR)
            j = min(i + len(x), len(track))
            track[i:j] += x[: j - i]
        sf.write(out, track[: int(total * SR)], SR)
        return out
