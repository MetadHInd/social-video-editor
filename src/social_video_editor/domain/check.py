"""Automatic review of a rendered version (`sve check`): pure measurements and verdicts.

The adapters decode audio / run FFmpeg detectors; this module turns numbers into findings:
loudness, true peak, clipping, silences, loudness jumps at cuts, stem balance, typing clicks,
harshness, black / frozen frames, cuts vs beats, text boxes vs the safe zone and duration.
Thresholds: recipe section `check:` (DEFAULTS below are used for anything missing, e.g. old renders).
"""
from __future__ import annotations

import math

import numpy as np

DEFAULTS = {
    "loudness_tolerance_lu": 1.0,     # integrated loudness within ±1 LU of audio.loudness
    "silence_db": -50.0,              # below this (dBFS RMS, 50 ms windows) is silence…
    "silence_sec": 0.5,               # …reported when longer than this
    "cut_jump_lu": 8.0,               # momentary loudness jump across a cut
    "sfx_max_vs_mix_lu": -6.0,        # sound-effect stem at most mix − 6 LU
    "music_max_vs_original_lu": 0.0,  # ASMR mode: music no louder than the original sound
    "typing_max_per_sec": 20.0,       # keystroke clicks per second inside a typing burst
    "typing_max_vs_mix_lu": -6.0,     # typing clicks at most mix − 6 LU
    "harsh_ratio": 0.35,              # share of 2–5 kHz energy (60 Hz–16 kHz) of the whole mix
    "harsh_window_ratio": 0.55,       # …or of any 1 s window
    "black_min_sec": 0.1,
    "freeze_min_sec": 1.0,
    "beat_tolerance_sec": 0.05,       # median cut-to-beat deviation
    "safe_top": 0.06,                 # portrait / square: text boxes inside [safe_top, safe_bottom]·H…
    "safe_bottom": 0.78,
    "safe_right": 0.08,               # …and off the right 8 % where TikTok's buttons are…
    "right_rail": [0.35, 0.85],       # …(that column spans this part of the height)
    "duration_tolerance": 0.25,       # ±25 % of duration.target (over duration.max is an error)
    "listen_max": 6,                  # snippets of the worst moments
    "listen_sec": 3.0,
}
SILENT_LUFS = -70.0
# Landscape output (W > H): generic title-safe area, no app buttons. A `check:` section may override
# them with the same keys (they are not in DEFAULTS / base.yaml).
LANDSCAPE_SAFE = {
    "landscape_safe_top": 0.05,       # text boxes inside [top, bottom]·H …
    "landscape_safe_bottom": 0.90,
    "landscape_safe_side": 0.05,      # … and this far from the left and right edges
}


def thresholds(*layers: dict | None) -> dict:
    out = dict(DEFAULTS)
    for layer in layers:
        out.update({k: v for k, v in (layer or {}).items() if v is not None})
    return out


def finite(x, default=None):
    """float(x) unless it is missing / -inf / nan."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def energy_mean_db(values) -> float:
    """Mean of loudness values (dB / LUFS) in the energy domain."""
    v = np.asarray([x for x in values if x is not None and x > -120], float)
    if not len(v):
        return SILENT_LUFS
    return float(10 * np.log10(np.mean(10 ** (v / 10))))


# ── audio signal ─────────────────────────────────────────────────────────────

def rms_db(x: np.ndarray, sr: int, win: float = 0.05) -> tuple[np.ndarray, float]:
    """RMS level (dBFS) in consecutive windows → (levels, window seconds)."""
    hop = max(int(sr * win), 1)
    n = len(x) // hop
    if n == 0:
        return np.zeros(0), win
    frames = x[: n * hop].reshape(n, hop)
    return 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-10), hop / sr


def silences(x: np.ndarray, sr: int, threshold_db: float, min_sec: float) -> list[tuple[float, float]]:
    """Stretches quieter than threshold_db for longer than min_sec → [(start, end)]."""
    db, step = rms_db(x, sr)
    return silences_from_db(db, step, threshold_db, min_sec)


def silences_from_db(db: np.ndarray, step: float, threshold_db: float, min_sec: float) -> list[tuple[float, float]]:
    """silences() from the RMS levels of consecutive windows of `step` seconds."""
    out, start = [], None
    for i, quiet in enumerate(list(db < threshold_db) + [False]):
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            if (i - start) * step > min_sec:
                out.append((round(start * step, 2), round(i * step, 2)))
            start = None
    return out


def clipping(x: np.ndarray, sr: int, level: float = 0.999, min_run: int = 2) -> list[tuple[float, int]]:
    """Runs of ≥ min_run consecutive samples at full scale → [(second, length)]."""
    hot = np.abs(x) >= level
    if not hot.any():
        return []
    d = np.diff(np.concatenate([[0], hot.astype(np.int8), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return [(round(s / sr, 3), int(e - s)) for s, e in zip(starts, ends) if e - s >= min_run]


def band_ratio(x: np.ndarray, sr: int, lo: float = 2000, hi: float = 5000,
               full: tuple[float, float] = (60, 16000), n_fft: int = 4096) -> float:
    """Share of the energy between lo and hi Hz (Welch average, Hann window)."""
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    hop = n_fft // 2
    n = 1 + (len(x) - n_fft) // hop
    win = np.hanning(n_fft)
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    spec = (np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2).mean(axis=0)
    f = np.fft.rfftfreq(n_fft, 1 / sr)
    total = spec[(f >= full[0]) & (f < full[1])].sum()
    return float(spec[(f >= lo) & (f < hi)].sum() / total) if total > 0 else 0.0


def harshness(x: np.ndarray, sr: int, win: float = 1.0, floor_db: float = -45) -> dict:
    """2–5 kHz energy share: whole mix + worst 1 s window (ignoring near-silent windows)."""
    out = {"ratio": round(band_ratio(x, sr), 3), "worst_t": None, "worst_ratio": None}
    n = int(sr * win)
    best = (-1.0, None)
    for i in range(0, max(len(x) - n, 0) + 1, n // 2):
        seg = x[i:i + n]
        if 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-10) < floor_db:
            continue
        r = band_ratio(seg, sr, n_fft=2048)
        if r > best[0]:
            best = (r, i / sr)
    if best[1] is not None:
        out.update(worst_t=round(best[1] + win / 2, 2), worst_ratio=round(best[0], 3))
    return out


class AudioScan:
    """The whole-track measurements of `sve check` (clipping per channel, silences, harshness) fed
    block by block, so a long render is never decoded into memory at once. Same windows as
    clipping / silences / harshness on the whole signal; only the summation order differs.

    keep: [(first, last)] sample ranges kept in full (stereo), e.g. the AutoMix splices.
    """

    N_FFT, WIN_FFT = 4096, 2048

    def __init__(self, sr: int, keep: list[tuple[int, int]] = (), clip_level: float = 0.999,
                 win: float = 1.0, floor_db: float = -45):
        self.sr, self.clip_level, self.floor_db = sr, clip_level, floor_db
        self.n = 0                                          # samples seen
        self.mono = np.zeros(0)                             # unprocessed mono tail …
        self.base = 0                                       # … starting at this sample
        self.hop = max(int(sr * 0.05), 1)                   # rms_db windows
        self.db: list[np.ndarray] = []
        self.rms_pos = self.f_pos = self.h_pos = 0
        self.spec = np.zeros(self.N_FFT // 2 + 1)           # whole-mix Welch sum
        self.frames = 0
        self.hn = int(sr * win)                             # harshness windows (hop = half)
        self.best = (-1.0, None)
        self.hot: list[np.ndarray] = []
        self.keep = {(int(a), int(b)): [] for a, b in keep}
        self._win = np.hanning(self.N_FFT)

    def feed(self, stereo: np.ndarray) -> None:
        stereo = np.asarray(stereo)
        if not len(stereo):
            return
        start, end = self.n, self.n + len(stereo)
        hot = np.where(np.abs(stereo).max(axis=1) >= self.clip_level)[0]
        if len(hot):
            self.hot.append(hot + start)
        for (a, b), parts in self.keep.items():
            if a < end and b > start:
                parts.append(stereo[max(a - start, 0):min(b, end) - start])
        self.mono = np.concatenate([self.mono, stereo.mean(axis=1)])
        self.n = end
        self._process()

    def _seg(self, a: int, b: int) -> np.ndarray:
        return self.mono[a - self.base:b - self.base]

    def _process(self) -> None:
        end = self.n
        k = (end - self.rms_pos) // self.hop
        if k > 0:
            x = self._seg(self.rms_pos, self.rms_pos + k * self.hop).reshape(k, self.hop)
            self.db.append(20 * np.log10(np.sqrt(np.mean(x ** 2, axis=1)) + 1e-10))
            self.rms_pos += k * self.hop
        hop = self.N_FFT // 2
        k = 0 if end - self.f_pos < self.N_FFT else 1 + (end - self.f_pos - self.N_FFT) // hop
        if k > 0:
            x = self._seg(self.f_pos, self.f_pos + (k - 1) * hop + self.N_FFT)
            idx = np.arange(self.N_FFT)[None, :] + hop * np.arange(k)[:, None]
            self.spec += (np.abs(np.fft.rfft(x[idx] * self._win, axis=1)) ** 2).sum(axis=0)
            self.frames += k
            self.f_pos += k * hop
        while self.h_pos + self.hn <= end:
            self._harsh_window(self._seg(self.h_pos, self.h_pos + self.hn), self.h_pos)
            self.h_pos += self.hn // 2
        drop = min(self.rms_pos, self.f_pos, self.h_pos) - self.base
        if drop > 0:
            self.mono = self.mono[drop:]
            self.base += drop

    def _harsh_window(self, seg: np.ndarray, at: int) -> None:
        if 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-10) < self.floor_db:
            return
        r = band_ratio(seg, self.sr, n_fft=2048)
        if r > self.best[0]:
            self.best = (r, at / self.sr)

    def clipping(self, min_run: int = 2) -> list[tuple[float, int]]:
        if not self.hot:
            return []
        idx = np.concatenate(self.hot)
        breaks = np.where(np.diff(idx) != 1)[0]
        starts = np.concatenate([[0], breaks + 1])
        ends = np.concatenate([breaks + 1, [len(idx)]])
        return [(round(idx[a] / self.sr, 3), int(b - a)) for a, b in zip(starts, ends) if b - a >= min_run]

    def silences(self, threshold_db: float, min_sec: float) -> list[tuple[float, float]]:
        db = np.concatenate(self.db) if self.db else np.zeros(0)
        return silences_from_db(db, self.hop / self.sr, threshold_db, min_sec)

    def harshness(self, win: float = 1.0) -> dict:
        if self.frames:
            spec = self.spec
        else:                                               # shorter than one FFT: padded, as band_ratio
            x = np.pad(self._seg(self.base, self.n), (0, max(self.N_FFT - (self.n - self.base), 0)))
            spec = np.abs(np.fft.rfft(x[:self.N_FFT] * self._win)) ** 2
        f = np.fft.rfftfreq(self.N_FFT, 1 / self.sr)
        total = spec[(f >= 60) & (f < 16000)].sum()
        ratio = float(spec[(f >= 2000) & (f < 5000)].sum() / total) if total > 0 else 0.0
        if self.h_pos == 0 and self.n < self.hn and self.n:  # shorter than one window: the whole track
            self._harsh_window(self._seg(0, self.n), 0)
        out = {"ratio": round(ratio, 3), "worst_t": None, "worst_ratio": None}
        if self.best[1] is not None:
            out.update(worst_t=round(self.best[1] + win / 2, 2), worst_ratio=round(self.best[0], 3))
        return out

    @property
    def seconds(self) -> float:
        return self.n / self.sr

    def excerpt(self, a: int, b: int) -> np.ndarray:
        """Samples [a, b) of a kept range (empty if none contains it)."""
        for (k0, k1), parts in self.keep.items():
            if k0 <= a and b <= k1:
                x = np.concatenate(parts) if parts else np.zeros((0, 2), np.float32)
                return x[a - k0:b - k0]
        return np.zeros((0, 2), np.float32)


# ── timeline facts ───────────────────────────────────────────────────────────

def momentary_at(t: list[float], m: list[float], at: float) -> float | None:
    """Momentary loudness (400 ms window ending at `at`) from an ebur128 series."""
    if not t:
        return None
    i = int(np.argmin(np.abs(np.asarray(t) - at)))
    return finite(m[i])


def cut_jumps(t: list[float], momentary: list[float], cuts: list[float], floor: float = -50) -> list[dict]:
    """Loudness right before vs right after every cut (400 ms each side)."""
    out = []
    for c in cuts:
        before, after = momentary_at(t, momentary, c), momentary_at(t, momentary, c + 0.4)
        if before is None or after is None or max(before, after) < floor:
            continue
        before, after = max(before, floor), max(after, floor)
        out.append({"t": round(c, 3), "jump_lu": round(after - before, 1),
                    "before": round(before, 1), "after": round(after, 1)})
    return out


PAUSE_LUFS = -30.0          # momentary loudness of a pause in speech (the room, no voice)


def pause_to_voice(jump: dict, integrated: float | None, pause: float = PAUSE_LUFS) -> bool:
    """A cut between a pause and the voice at its normal level (≤ integrated + 3 LU): speech starting or
    stopping, not a loudness jump — with a clean voice the pauses are near silence."""
    quiet, loud = min(jump["before"], jump["after"]), max(jump["before"], jump["after"])
    return quiet < pause and integrated is not None and loud <= integrated + 3


def window_level(t: list[float], m: list[float], a: float, b: float) -> float:
    """Energy-mean momentary loudness of the windows ending inside [a + 0.2, b + 0.4]."""
    tt = np.asarray(t)
    sel = (tt >= a + 0.2) & (tt <= b + 0.4)
    return energy_mean_db([finite(m[i], -120) for i in np.where(sel)[0]])


def typing_bursts(keystrokes: list[float], gap: float = 0.25) -> list[list]:
    """Keystrokes grouped into bursts (pause > gap splits) → [[start, end, count]]."""
    out = []
    for k in sorted(keystrokes):
        if out and k - out[-1][1] <= gap:
            out[-1][1] = k
            out[-1][2] += 1
        else:
            out.append([k, k, 1])
    return [[round(a, 3), round(b, 3), n] for a, b, n in out]


def thin_keystrokes(keystrokes: list[float], max_per_sec: float, gap: float = 0.25) -> list[float]:
    """The keystrokes that get a click SOUND: at most `max_per_sec` clicks per second in every typing
    burst as `sve check` measures it (typing_bursts + burst_rate); the letters still appear at their
    own speed. Deterministic: an evenly spaced subset (by index) of each too-dense burst."""
    keys = sorted(keystrokes)
    if not max_per_sec or max_per_sec <= 0:
        return keys
    for _ in range(8):           # thinning may split a burst at a long pause: re-check the pieces
        out, dense = [], False
        i = 0
        while i < len(keys):     # the same bursts as typing_bursts (a pause > gap splits)
            j = i
            while j + 1 < len(keys) and keys[j + 1] - keys[j] <= gap:
                j += 1
            burst = keys[i:j + 1]
            n, span = len(burst), burst[-1] - burst[0] - 0.001     # 1 ms: typing_bursts rounds to ms
            if n > 2 and (span <= 0 or (n - 1) / span > max_per_sec):
                dense = True
                m = max(int(max(span, 0) * max_per_sec) + 1, 2)
                burst = [burst[round(k * (n - 1) / (m - 1))] for k in range(m)]
            out += burst
            i = j + 1
        keys = out
        if not dense:
            break
    return keys


def burst_rate(burst) -> float:
    a, b, n = burst
    return (n - 1) / (b - a) if n > 2 and b > a else 0.0


def beat_deviation(cuts: list[float], beats: list[float], half_beats: bool = False) -> list[float]:
    """Distance of every cut to the nearest beat (or half beat)."""
    if not beats:
        return []
    grid = np.asarray(sorted(beats), float)
    if half_beats and len(grid) > 1:
        grid = np.sort(np.concatenate([grid, (grid[:-1] + grid[1:]) / 2]))
    return [round(float(np.min(np.abs(grid - c))), 3) for c in cuts]


def outside_output_fades(black: list, output: dict, total: float | None, min_sec: float,
                         margin: float = 0.05) -> list:
    """Black stretches minus the intended fades of the whole video (output.fade_in from 0,
    output.fade_out up to the end); what is left counts only if still at least min_sec long."""
    fi = max(finite(output.get("fade_in"), 0.0), 0.0)
    fo = max(finite(output.get("fade_out"), 0.0), 0.0)
    if fi <= 0 and fo <= 0:
        return black
    out = []
    for a, b in black:
        if fi > 0 and a <= fi + margin:
            a = max(a, fi + margin)
        if fo > 0 and total and b >= total - fo - margin:
            b = min(b, total - fo - margin)
        if b - a >= min_sec:
            out.append([round(a, 3), round(b, 3)])
    return out


def overlaps(a: tuple[float, float], spans: list[tuple[float, float]], margin: float = 0.05) -> bool:
    return any(a[0] < e + margin and a[1] > s - margin for s, e in spans)


def safe_zone(W: int, H: int, th: dict) -> dict:
    """Safe area for the output's orientation (W > H = landscape), as fractions of W / H.

    Portrait and square: TikTok / Reels layout (caption and buttons at the bottom, button column on
    the right). Landscape: generic title-safe margins on every side; there is no right rail.
    """
    if W > H:
        ls = {k: float(th.get(k, v)) for k, v in LANDSCAPE_SAFE.items()}
        side = ls["landscape_safe_side"]
        return {"orientation": "landscape", "top": ls["landscape_safe_top"],
                "bottom": ls["landscape_safe_bottom"], "left": side, "right": side, "rail": None}
    return {"orientation": "portrait" if H > W else "square", "top": th["safe_top"],
            "bottom": th["safe_bottom"], "left": None, "right": th["safe_right"],
            "rail": (th.get("right_rail") or [0, 1])[:2]}


def box_issues(boxes: list[dict], W: int, H: int, th: dict) -> list[dict]:
    """Text boxes outside the safe zone: too high, too low, under the right-hand buttons (portrait) or
    too close to the side edges (landscape)."""
    z = safe_zone(W, H, th)
    top, bottom = z["top"] * H, z["bottom"] * H
    right = (1 - z["right"]) * W
    out = []
    for b in boxes:
        y0, y1 = b["y"], b["y"] + b["h"]
        where = []
        if y0 < top:
            where.append(f"top {y0 / H:.2f}·H < {z['top']}")
        if y1 > bottom:
            where.append(f"bottom {y1 / H:.2f}·H > {z['bottom']}")
        if b.get("w") is not None:
            x1 = b["x"] + b["w"]
            if z["rail"] is not None:
                r0, r1 = z["rail"]
                if x1 > right and y1 > r0 * H and y0 < r1 * H:
                    where.append(f"right edge {x1 / W:.2f}·W under the buttons")
            else:
                if b["x"] < z["left"] * W:
                    where.append(f"left edge {b['x'] / W:.2f}·W < {z['left']}")
                if x1 > right:
                    where.append(f"right edge {x1 / W:.2f}·W > {1 - z['right']:.2f}")
        if where:
            out.append({"text": b.get("text", ""), "kind": b.get("kind", "text"), "start": b["start"],
                        "end": b["end"], "issues": where, "estimated": bool(b.get("estimated"))})
    return out


def estimated_boxes(texts: list[dict], styles: dict, W: int, H: int) -> list[dict]:
    """Old renders (no recorded boxes): approximate boxes from each text's style (center, size)."""
    from .text_layout import resolve_style, text_scale

    out = []
    for tx in texts:
        st = resolve_style(tx, styles, text_scale(W, H))
        size = float(st.get("size", 60))
        lines = 1 + (1 if tx.get("description") else 0) + len(str(tx.get("text", ""))) // 28
        h = size * 1.25 * lines + size * 0.56
        cy = float(st.get("y", 0.5)) * H
        out.append({"kind": "text", "text": tx.get("text", ""), "x": 0, "y": cy - h / 2, "w": None, "h": h,
                    "start": tx.get("start", 0), "end": tx.get("end", 0), "estimated": True})
    return out


# ── listening snippets ───────────────────────────────────────────────────────

def pick_moments(candidates: list[dict], n: int, spacing: float = 2.0) -> list[dict]:
    """The n most severe moments, at most 2 of each kind, not closer than `spacing` seconds."""
    chosen, per_kind = [], {}
    for c in sorted(candidates, key=lambda c: -c["severity"]):
        if len(chosen) >= n:
            break
        if per_kind.get(c["kind"], 0) >= 2 or any(abs(c["t"] - x["t"]) < spacing for x in chosen):
            continue
        chosen.append(c)
        per_kind[c["kind"]] = per_kind.get(c["kind"], 0) + 1
    return sorted(chosen, key=lambda c: -c["severity"])


def snippet_window(t: float, length: float, total: float) -> tuple[float, float]:
    """A ≤ length window centered on t, kept inside the video."""
    length = min(length, total)
    start = min(max(t - length / 2, 0.0), max(total - length, 0.0))
    return round(start, 3), round(length, 3)


def report_lines(result: dict, notes: list[str]) -> list[str]:
    """Compact text report."""
    m = result["metrics"]
    lines = [f"Check {result['version']}: {'OK' if result['ok'] else 'PROBLEMS'} — "
             f"{len(result['errors'])} errors, {len(result['warnings'])} warnings"]
    a = m.get("audio", {})
    if a:
        lines.append(f"  loudness {a.get('integrated_lufs')} LUFS (target {a.get('target_lufs')}), "
                     f"true peak {a.get('true_peak_dbtp')} dBTP, harshness {a.get('harsh_ratio')}")
    st = m.get("stems")
    if st:
        lines.append("  stems (LUFS, master gain applied): "
                     + ", ".join(f"{k} {v}" for k, v in st.items() if k in ("original", "music", "sfx")))
    lines += [f"  ERROR  {e}" for e in result["errors"]]
    lines += [f"  warn   {w}" for w in result["warnings"]]
    lines += [f"  note   {x}" for x in notes]
    if result["listen"]:
        lines.append("  listen: " + ", ".join(result["listen"]))
    return lines
