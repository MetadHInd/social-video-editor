"""Voice and music levels, measured from the voice itself (pure numbers, no I/O).

Everything works on a level track: the RMS level (dBFS) of the voice in frames of FRAME seconds,
which the adapters compute from the audio (adapters/ffmpeg/voice.py, voice_mix.py).

- speech_mask: which frames are speech. A frame is speech when it is less than `below_ref` dB under
  the local speech level (the loud part of the surrounding seconds, never below the whole
  recording's speech level − 12 dB, so a long quiet stretch does not become its own reference).
  Gaps shorter than a consonant closure are bridged; isolated blips (a mouth click) are not speech.
- breath_regions (audio.breaths): the pauses between sentences — everything that is not speech and not
  inside a word (word timings, when the transcription has them) — with margins around every word, so
  word starts and soft word ends are never touched. Breaths and mouth noise live there; the whole
  pause is lowered by `depth_db` with short fades INSIDE the pause.
- music_curve (music.relative_to_voice): the music's gain relative to its "pause" level: down by
  under_voice_db − under_pause_db while the voice speaks (gaps shorter than min_pause stay down),
  up again in real pauses, `intro_db` louder before the first word. The changes are slew-limited
  (attack / release in seconds for the full change), going down slightly BEFORE each onset.
"""
from __future__ import annotations

import numpy as np

FRAME = 0.01                # seconds per level frame

BREATHS = {                 # audio.breaths (true = these defaults; a dict overrides some)
    "depth_db": 12.0,       # how much quieter the pauses get
    "fade": 0.04,           # seconds of fade at each end (inside the pause)
    "before_word": 0.06,    # seconds kept untouched before speech starts…
    "after_word": 0.10,     # …and after it ends (soft word ends decay)
    "min_pause": 0.12,      # shorter gaps are left alone (closures inside words)
    "below_ref": 20.0,      # dB under the local speech level: quieter frames are not speech
}

MUSIC = {                   # music.* keys of the level relative to the voice
    "under_voice_db": 10.0,     # music this much under the voice (LUFS) while it speaks
    "under_pause_db": 4.0,      # … and this much in pauses
    "intro_db": 4.0,            # before the first word: this much louder than in pauses
    "min_pause": 0.6,           # a pause must last this long for the music to come up
    "attack": 0.25,             # seconds to go down (completed just before the word)
    "release": 0.6,             # seconds to come back up
    "lookahead": 0.05,          # down this long before the onset
    "hold": 0.15,               # stays down this long after the speech ends
}


def breath_settings(value) -> dict | None:
    """audio.breaths → settings dict, or None when off (false / null / 0)."""
    if not value:
        return None
    if isinstance(value, dict):
        return {**BREATHS, **{k: float(v) for k, v in value.items() if v is not None and k in BREATHS}}
    return dict(BREATHS)


def polish_settings(audio: dict, words: list[dict] | None = None) -> dict | None:
    """What the whole-recording voice pass does besides cleaning (audio.breaths, audio.voice_leveler),
    for encoder.clean_voice — None when nothing (the cleaned voice exactly as before). words: the
    transcription's words (source seconds); the breath pass never touches them."""
    breaths = breath_settings((audio or {}).get("breaths"))
    leveler = bool((audio or {}).get("voice_leveler"))
    if not breaths and not leveler:
        return None
    spans = []
    if breaths:
        for w in words or []:
            try:
                spans.append([round(float(w["start"]), 3), round(float(w["end"]), 3)])
            except (KeyError, TypeError, ValueError):
                continue
    return {"breaths": breaths, "leveler": leveler, "words": spans}


def music_settings(m: dict) -> dict:
    """The music section's relative-level keys (defaults for the missing ones)."""
    out = dict(MUSIC)
    for k in MUSIC:
        if m.get(k) is not None:
            out[k] = float(m[k])
    return out


# ── speech ───────────────────────────────────────────────────────────────────

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) frame runs where mask is True."""
    if not len(mask):
        return []
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def speech_level(db: np.ndarray, floor: float = -60.0) -> float:
    """The recording's speech level (dBFS): the 90th percentile of the frames above the floor."""
    loud = db[db > floor]
    return float(np.percentile(loud, 90)) if len(loud) else floor


def local_reference(db: np.ndarray, floor: float = -60.0, block: float = 0.5, span: float = 1.5) -> np.ndarray:
    """Per frame: the loud level of the surrounding ±span seconds (90th percentile of 0.5 s blocks,
    their maximum over the span), never below the whole recording's speech level − 12 dB."""
    n = len(db)
    g = speech_level(db, floor)
    if n == 0:
        return np.zeros(0)
    b = max(int(round(block / FRAME)), 1)
    nb = (n + b - 1) // b
    pad = np.full(nb * b, floor)
    pad[:n] = np.maximum(db, floor)
    blocks = np.percentile(pad.reshape(nb, b), 90, axis=1)
    k = max(int(round(span / block)), 0)
    wide = np.array([blocks[max(i - k, 0):i + k + 1].max() for i in range(nb)])
    return np.maximum(np.repeat(wide, b)[:n], g - 12.0)


def speech_mask(db: np.ndarray, below_ref: float = 20.0, floor: float = -60.0,
                bridge: float = 0.10, min_blip: float = 0.05, core_below: float = 12.0) -> np.ndarray:
    """True for the frames that are speech (see the module docstring). A run of frames less than
    `below_ref` dB under the local speech level is speech only if it also reaches `core_below` dB
    under it somewhere (a syllable): a breath hovering just over the threshold is not."""
    db = np.asarray(db, dtype=float)
    ref = local_reference(db, floor)
    mask = (db > ref - below_ref) & (db > floor)
    runs = _runs(mask)
    out = np.zeros(len(db), dtype=bool)
    gap_max, blip_min = int(round(bridge / FRAME)), int(round(min_blip / FRAME))
    merged: list[list[int]] = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= gap_max:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    for a, b in merged:
        if b - a >= blip_min and np.max(db[a:b] - ref[a:b]) > -core_below:
            out[a:b] = True
    return out


def intervals(mask: np.ndarray) -> list[tuple[float, float]]:
    """Seconds [start, end) of the True runs of a frame mask."""
    return [(round(a * FRAME, 3), round(b * FRAME, 3)) for a, b in _runs(mask)]


# ── breaths ──────────────────────────────────────────────────────────────────

def breath_regions(db: np.ndarray, settings: dict, words: list[dict] | None = None) -> list[tuple[float, float]]:
    """Pauses to lower (seconds): not speech, not in a word, away from both by the margins."""
    s = {**BREATHS, **(settings or {})}
    n = len(db)
    keep = speech_mask(db, s["below_ref"])
    for w in words or []:
        try:
            a, b = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        keep[max(int(a / FRAME) - 2, 0):min(int(np.ceil(b / FRAME)) + 2, n)] = True
    grown = keep.copy()
    pre, post = int(round(s["before_word"] / FRAME)), int(round(s["after_word"] / FRAME))
    for a, b in _runs(keep):
        grown[max(a - pre, 0):min(b + post, n)] = True
    min_len = int(round(s["min_pause"] / FRAME))
    return [(round(a * FRAME, 3), round(b * FRAME, 3)) for a, b in _runs(~grown) if b - a >= min_len]


def breath_stats(db: np.ndarray, regions: list[tuple[float, float]], floor: float = -60.0) -> dict:
    """How many pauses were lowered and how many of them held a sound (a breath / mouth noise:
    some frame more than 10 dB over the floor of that pause)."""
    sounds = 0
    for a, b in regions:
        seg = db[int(a / FRAME):int(b / FRAME)]
        if len(seg) and seg.max() > max(np.percentile(seg, 10), floor) + 10:
            sounds += 1
    return {"pauses": len(regions), "with_sound": sounds,
            "seconds": round(sum(b - a for a, b in regions), 2)}


def gain_curve(regions: list[tuple[float, float]], n: int, depth_db: float, fade: float) -> np.ndarray:
    """Gain in dB per frame: 0, and −depth_db inside the regions with raised-cosine fades inside them."""
    g = np.zeros(n)
    f = max(int(round(fade / FRAME)), 1)
    for a, b in regions:
        i, j = int(round(a / FRAME)), min(int(round(b / FRAME)), n)
        if j <= i:
            continue
        r = min(f, (j - i) // 2)
        shape = np.ones(j - i)
        if r > 0:
            ramp = 0.5 - 0.5 * np.cos(np.pi * (np.arange(r) + 0.5) / r)
            shape[:r] = ramp
            shape[-r:] = ramp[::-1]
        g[i:j] = np.minimum(g[i:j], -depth_db * shape)
    return g


# ── music under the voice ────────────────────────────────────────────────────

def _slew(target: np.ndarray, up: float, down: float) -> np.ndarray:
    """target limited to rise at most `up` and fall at most `down` dB per frame. The falls are taken
    backwards in time, so a drop is finished when the target drops (it starts earlier)."""
    out = target.astype(float).copy()
    for i in range(1, len(out)):              # rises: forward
        out[i] = min(out[i], out[i - 1] + up)
    for i in range(len(out) - 2, -1, -1):     # falls: backward (anticipated)
        out[i] = min(out[i], out[i + 1] + down)
    return out


def music_curve(speech: list[tuple[float, float]], total: float, settings: dict) -> np.ndarray:
    """Music gain in dB per frame relative to its pause level (0): −(under_voice − under_pause) in
    speech, +intro_db before the first word, 0 in pauses of at least min_pause."""
    s = {**MUSIC, **(settings or {})}
    n = max(int(np.ceil(total / FRAME)), 1)
    dip = max(s["under_voice_db"] - s["under_pause_db"], 0.0)
    down = np.zeros(n, dtype=bool)
    for a, b in speech:
        i = max(int((a - s["lookahead"]) / FRAME), 0)
        j = min(int(np.ceil((b + s["hold"]) / FRAME)), n)
        down[i:j] = True
    gap = int(round(s["min_pause"] / FRAME))
    runs = _runs(down)
    for (_, e), (a, _) in zip(runs, runs[1:]):
        if a - e < gap:
            down[e:a] = True
    target = np.where(down, -dip, 0.0)
    if runs:
        target[:runs[0][0]] = np.where(down[:runs[0][0]], -dip, float(s["intro_db"]))
    step = max(dip, 1.0)                      # dB per frame: the full dip takes attack / release seconds
    up, fall = step / max(s["release"] / FRAME, 1.0), step / max(s["attack"] / FRAME, 1.0)
    return _slew(target, up, fall)


def music_base_gain(voice_lufs: float, music_lufs: float, settings: dict) -> float:
    """dB to apply to the music so that its pause level is under_pause_db under the voice."""
    s = {**MUSIC, **(settings or {})}
    return float(voice_lufs) - float(s["under_pause_db"]) - float(music_lufs)


def music_balance(voice_lufs: float, voice_m: list[float], music_m: list[float], speech: list[bool],
                  audible: float = -60.0) -> dict:
    """How far the music sits under the voice, from momentary loudness series (0.1 s apart):
    "speech_lu" / "pause_lu" = the voice's integrated loudness − the music's median momentary loudness
    while the voice speaks / in pauses; "worst_lu" = the smallest voice − music moment while it speaks
    (both momentary), at index "worst_i"."""
    v, m = np.asarray(voice_m, float), np.asarray(music_m, float)
    n = min(len(v), len(m), len(speech))
    v, m, sp = v[:n], m[:n], np.asarray(speech[:n], bool)
    ok = (m > audible) & np.isfinite(m)
    spk, pau = ok & sp & np.isfinite(v) & (v > audible), ok & ~sp
    out: dict = {"speech_lu": None, "pause_lu": None, "worst_lu": None, "worst_i": None}
    if spk.any():
        out["speech_lu"] = round(float(voice_lufs - np.median(m[spk])), 1)
        d = v[spk] - m[spk]
        k = int(np.argmin(d))
        out["worst_lu"], out["worst_i"] = round(float(d[k]), 1), int(np.flatnonzero(spk)[k])
    if pau.any():
        out["pause_lu"] = round(float(voice_lufs - np.median(m[pau])), 1)
    return out
