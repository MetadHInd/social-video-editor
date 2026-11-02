"""AudioAnalyzer with soundfile + scipy (levels) and librosa (music beats)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ...domain.music import analyze_music

LEVEL_STEP = 0.1        # seconds between audio level measurements


class LibrosaAudioAnalyzer:
    def levels(self, wav: str) -> dict:
        """Loudness (dB) every 0.1 s and peaks (sudden loud sounds: tearing, opening, laughing)."""
        import soundfile as sf
        from scipy.ndimage import median_filter

        x, sr = sf.read(wav, dtype="float32")
        if x.ndim > 1:
            x = x.mean(1)
        hop = int(sr * LEVEL_STEP)
        n = len(x) // hop
        rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
        db = 20 * np.log10(rms)
        base = median_filter(db, size=21)          # peaks: jumps over the ~2 s local median
        peaks = [round(i * LEVEL_STEP, 2) for i in range(1, n - 1)
                 if db[i] - base[i] > 9 and db[i] >= db[i - 1] and db[i] >= db[i + 1] and db[i] > -45]
        return {"step": LEVEL_STEP, "db": [round(float(v), 1) for v in db], "peaks": peaks,
                "median_db": round(float(np.median(db)), 1)}

    def music(self, path: Path) -> dict:
        """Tempo, regular beat grid and drop of a song (see domain.music)."""
        import librosa

        y, sr = librosa.load(str(path), sr=22050, mono=True)
        dur = len(y) / sr
        _, bt = librosa.beat.beat_track(y=y, sr=sr, units="time")
        rms = librosa.feature.rms(y=y, hop_length=512)[0]
        rms_t = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)
        return analyze_music(dur, bt, rms, rms_t)
