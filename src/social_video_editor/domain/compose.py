"""An original instrumental track, composed as data (the "score"): no samples, no loops, no audio here.

From a style preset, a seed, a tempo and the video's sections the composer writes every note and drum
hit as an event; `adapters/synth/` turns the score into audio with numpy. The same preset + seed +
tempo + sections always give the same score (seeded `random.Random`, stable across platforms), so a
track can be regenerated and its origin proven (`origin()`).

Score:
    {"v", "preset", "seed", "bpm", "key", "mode", "duration",
     "sections": [{"kind", "start", "end", "hit", "why", "chords": [...]}, …],
     "beats": [s, …], "downbeats": [s, …],         (exact: the composer knows its own grid)
     "gates": [[start, end, fade_in, fade_out], …], (where the music sounds; silence elsewhere)
     "events": [{"t", "d", "i", "r", "n", "v", "p"}, …]}
    event: t start (s), d length (s), i instrument/sound, r role (drums | bass | pad | arp | lead | fx),
           n MIDI note (pitched sounds), v velocity 0–1, p pan −1…1

Sections ("kind"): intro (energetic, a hit on its first frame), verse (lighter), chorus (the best
part: full drums and the melody), break (drums out), silence (nothing: the music is cut dead
before it), outro (emotive and lower, a 1 s fade at the very end). How a video's moments become
sections is `arc_sections()` (documented there); a track with no video uses `template_sections()`.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
import zlib

COMPOSER_VERSION = 1
MUSIC_KINDS = ("intro", "verse", "chorus", "break", "outro")
KINDS = (*MUSIC_KINDS, "silence")
ENERGY = {"intro": 0.9, "verse": 0.62, "chorus": 1.0, "break": 0.4, "outro": 0.55, "silence": 0.0}
ENTRY_FADE = 0.02           # a section that starts after a silence comes in on a hit (20 ms)
DEAD_CUT = 0.06             # the music is cut dead before a silence (60 ms)
FINAL_FADE = 1.0            # the last second fades out
MIN_INTRO = 2.0             # the intro lasts at least this long before the music may stop
MIN_SECTION = 2.0           # a music section shorter than this joins the previous one
MAX_SILENCE = 8.0           # a silence never lasts longer than this
MAX_HEADING_WORDS = 6       # a comment with more words is a note, not an arc heading
ACCENT_GAP = 4.0            # at most one accent (a full-screen graphic coming in) every 4 s

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SCALES = {"major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10),
          "dorian": (0, 2, 3, 5, 7, 9, 10), "mixolydian": (0, 2, 4, 5, 7, 9, 10)}
ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}

# ── drum patterns: 16 steps a bar; X loud, x normal, o soft, - ghost, . rest; a list = bars in turn ──
VELOCITY = {"X": 1.0, "x": 0.75, "o": 0.48, "-": 0.28}
DRUMS = {
    "pop": {"kick": ["X...X...X...X..."], "clap": ["....X.......X..."],
            "hat": ["x.o.x.o.x.o.x.o."], "ohat": ["..............o."]},
    "pop_light": {"kick": ["X.......X.......", "X.......X.x....."], "rim": ["....o.......o..."],
                  "hat": ["o.-.o.-.o.-.o.-."]},
    "trap": {"kick": ["X.....x...X.....", "X..x....x..x...."], "clap": ["....X.......X..."],
             "hat": ["xoxoxoxoxoxoxoxo"], "rolls": True},
    "trap_light": {"kick": ["X.........x.....", "X.......x......."], "clap": ["........X......."],
                   "hat": ["x.o.x.o.x.o.x.o."]},
    "lofi": {"kick": ["X......x..X.....", "X.........X..x.."], "snare": ["....x.......x..."],
             "hat": ["o.-.o.-.o.-.o.--"], "swing": 0.14},
    "lofi_light": {"kick": ["X.........x....."], "rim": ["....o.......o..."], "hat": ["o...o...o...o..."],
                   "swing": 0.14},
    "tense": {"kick": ["X..X..X...X..X..", "X..X..X...X....."], "snare": ["........X......."],
              "hat": ["x.x.x.x.x.x.x.x."], "tom": [".............o.o"]},
    "tense_light": {"kick": ["X.....X.........", "X.....X...X....."], "hat": ["o.o.o.o.o.o.o.o."],
                    "tom": ["...............o"]},
    "brush": {"kick": ["X.......x.......", "X.....x.x......."], "rim": ["....x.......x..."],
              "shaker": ["o-xoo-xoo-xoo-xo"]},
    "brush_light": {"kick": ["X..............."], "shaker": ["o-o-o-o-o-o-o-o-"]},
    "soft": {"kick": ["X.........X.....", "X..............."], "rim": ["........o......."],
             "hat": ["o...o...o...o..."]},
    "none": {},
}

# ── style presets (generic). A pack may add its own: music: {presets: {name: {base: trap, …}}} ──
PRESETS: dict[str, dict] = {
    "upbeat": {
        "description": "curious and bright: pop drums, plucked arpeggio, soft bell melody",
        "bpm": 108, "key": "D", "mode": "major",
        "progressions": {"main": ["I", "V", "vi", "IV"], "chorus": ["IV", "I", "V", "vi"],
                         "outro": ["vi", "IV", "I", "V"]},
        "drums": {"intro": "pop", "verse": "pop_light", "chorus": "pop", "break": "none", "outro": "soft"},
        "bass": {"sound": "synth", "pattern": "offbeat", "outro_pattern": "half"},
        "pad": {"sound": "warm", "cutoff": 1900},
        "arp": {"sound": "pluck", "rate": 2, "shape": "updown", "octave": 1},
        "lead": {"sound": "bell", "low": 69, "high": 81, "density": 0.55},
        "outro": {"arp": "keys", "arp_rate": 4, "lead": "keys", "lead_octave": -12, "density": 0.35},
        "sevenths": False,
        "mix": {"bass": -2.0, "pad": -7.0, "arp": -5.0, "lead": -5.0, "fx": -8.0},
    },
    "calm": {
        "description": "calm lo-fi: swung soft drums, warm seventh chords, electric piano",
        "bpm": 82, "key": "F", "mode": "major",
        "progressions": {"main": ["ii", "V", "I", "vi"], "chorus": ["IV", "iii", "ii", "V"],
                         "outro": ["IV", "I", "ii", "V"]},
        "drums": {"intro": "lofi", "verse": "lofi_light", "chorus": "lofi", "break": "none", "outro": "lofi_light"},
        "bass": {"sound": "round", "pattern": "half", "outro_pattern": "whole"},
        "pad": {"sound": "soft", "cutoff": 2000},
        "arp": {"sound": "keys", "rate": 4, "shape": "up", "octave": 1},
        "lead": {"sound": "keys", "low": 65, "high": 77, "density": 0.35},
        "outro": {"arp": "keys", "arp_rate": 8, "lead": "keys", "lead_octave": 0, "density": 0.25},
        "sevenths": True,
        "mix": {"bass": -2.0, "pad": -5.0, "arp": -3.0, "lead": -4.0, "fx": -10.0},
    },
    "tense": {
        "description": "tense and driving: minor key, eighth-note bass ostinato, toms, dark pad",
        "bpm": 112, "key": "E", "mode": "minor",
        "progressions": {"main": ["i", "VI", "iv", "v"], "chorus": ["i", "VII", "VI", "v"],
                         "outro": ["VI", "iv", "i", "v"]},
        "drums": {"intro": "tense", "verse": "tense_light", "chorus": "tense", "break": "none", "outro": "soft"},
        "bass": {"sound": "synth", "pattern": "eighths", "outro_pattern": "half"},
        "pad": {"sound": "dark", "cutoff": 1500},
        "arp": {"sound": "pluck", "rate": 1, "shape": "up", "octave": 1},
        "lead": {"sound": "soft", "low": 64, "high": 76, "density": 0.3},
        "outro": {"arp": "keys", "arp_rate": 4, "lead": "keys", "lead_octave": 0, "density": 0.3},
        "sevenths": False,
        "mix": {"bass": -1.0, "pad": -6.0, "arp": -6.0, "lead": -6.0, "fx": -7.0},
    },
    "warm": {
        "description": "warm and acoustic: plucked strings, brushes and shaker, soft flute-like melody",
        "bpm": 92, "key": "G", "mode": "major",
        "progressions": {"main": ["I", "iii", "vi", "IV"], "chorus": ["IV", "V", "iii", "vi"],
                         "outro": ["IV", "I", "V", "I"]},
        "drums": {"intro": "brush", "verse": "brush_light", "chorus": "brush", "break": "none", "outro": "brush_light"},
        "bass": {"sound": "round", "pattern": "half", "outro_pattern": "whole"},
        "pad": {"sound": "soft", "cutoff": 2000},
        "arp": {"sound": "string", "rate": 2, "shape": "finger", "octave": 1},
        "lead": {"sound": "soft", "low": 67, "high": 79, "density": 0.45},
        "outro": {"arp": "string", "arp_rate": 4, "lead": "keys", "lead_octave": -12, "density": 0.3},
        "sevenths": False,
        "mix": {"bass": -2.0, "pad": -6.0, "arp": -3.0, "lead": -6.0, "fx": -10.0},
    },
    "trap": {
        "description": "melodic trap: double-time hats with rolls, 808 with a mid layer, fast arpeggio, bell lead",
        "bpm": 96, "key": "F", "mode": "minor",
        "progressions": {"main": ["i", "VI", "III", "VII"], "chorus": ["i", "VI", "III", "VII"],
                         "outro": ["VI", "VII", "v", "i"]},
        "drums": {"intro": "trap", "verse": "trap_light", "chorus": "trap", "break": "none", "outro": "soft"},
        "bass": {"sound": "808", "pattern": "kick", "outro_pattern": "half"},
        "pad": {"sound": "warm", "cutoff": 1700},
        "arp": {"sound": "pluck", "rate": 1, "shape": "up", "octave": 1},
        "lead": {"sound": "bell", "low": 72, "high": 84, "density": 0.5},
        "outro": {"arp": "keys", "arp_rate": 4, "lead": "keys", "lead_octave": -12, "density": 0.35},
        "sevenths": False,
        "mix": {"bass": 0.0, "pad": -8.0, "arp": -9.0, "lead": -6.0, "fx": -7.0},
    },
}
DEFAULT_PRESET = "upbeat"

# one-bar melody rhythms (16 steps; the length of each note runs to the next onset)
RHYTHMS = ("x..x..x.x...x...", "x...x.x...x.x...", "x.x...x.x.x.....", "x..x..x...x.x...",
           "x.....x.x...x.x.", "x...x...x.x.x...", "x..x...xx...x...", "x.x.x...x...x...")
SPARSE_RHYTHMS = ("x.......x.......", "x.....x.........", "x...........x...", "x.......x...x...")


# music.compose settings (recipes/base.yaml) and their defaults
SETTINGS = {"preset": None, "seed": None, "bpm": None, "silences": True, "accents": True, "lufs": -16.0}


def settings(music_cfg: dict | None) -> dict:
    """music.compose with its defaults (a missing or empty section = all defaults)."""
    own = (music_cfg or {}).get("compose")
    return {**SETTINGS, **(own if isinstance(own, dict) else {})}


def is_compose(music_cfg: dict | None) -> bool:
    """music.file: compose — the engine composes the track itself."""
    return str((music_cfg or {}).get("file") or "").strip().lower() == "compose"


# ── presets ──────────────────────────────────────────────────────────────────

def resolve_preset(name: str | None, extra: dict | None = None) -> tuple[str, dict]:
    """(name, full preset dict). extra: presets a pack ships ({name: {"base": "trap", …overrides}});
    a pack preset may also override a built-in one by name. Unknown name → ValueError."""
    extra = extra or {}
    name = str(name or DEFAULT_PRESET).strip().lower()
    if name in extra:
        spec = dict(extra[name] or {})
        base_name = str(spec.pop("base", None) or (name if name in PRESETS else DEFAULT_PRESET)).lower()
        if base_name not in PRESETS:
            raise ValueError(f"music preset '{name}': unknown base '{base_name}'. Options: {', '.join(PRESETS)}")
        return name, _merge(PRESETS[base_name], spec)
    if name not in PRESETS:
        raise ValueError(f"Unknown music preset '{name}'. Options: {', '.join([*PRESETS, *extra])}")
    return name, _merge(PRESETS[name], {})


def _merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def seed_of(value, fallback: str = "") -> int:
    """A seed as a 32-bit int: an int as it is, any other value (or the fallback, e.g. the project
    name) through CRC-32 — stable across machines and Python versions."""
    if isinstance(value, bool):
        value = None
    if isinstance(value, int):
        return value & 0xFFFFFFFF
    if value is not None and str(value).strip().lstrip("-").isdigit():
        return int(str(value).strip()) & 0xFFFFFFFF
    text = str(value if value not in (None, "") else fallback)
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def _rng(seed: int, *parts) -> random.Random:
    return random.Random(":".join(str(p) for p in (seed, *parts)))


# ── harmony ──────────────────────────────────────────────────────────────────

def key_root(key: str) -> int:
    """MIDI pitch class of a key name (C, F#, Bb…)."""
    k = str(key).strip().capitalize().replace("♯", "#").replace("♭", "b")
    flats = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E"}
    k = flats.get(k, k)
    if k not in NOTE_NAMES:
        raise ValueError(f"Unknown key '{key}' (C, C#, D … B)")
    return NOTE_NAMES.index(k)


def chord(numeral: str, root: int, mode: str, sevenths: bool = False) -> list[int]:
    """Diatonic chord of a Roman numeral (case is ignored: the scale decides major/minor) as MIDI
    notes around C3–C4: [bass root, triad (or seventh) notes…]."""
    scale = SCALES.get(mode, SCALES["major"])
    deg = ROMAN.get(str(numeral).strip().lower().rstrip("7"))
    if deg is None:
        raise ValueError(f"Unknown chord numeral '{numeral}' (I … VII)")
    tones = [scale[(deg + 2 * j) % 7] + 12 * ((deg + 2 * j) // 7) for j in range(4 if sevenths else 3)]
    base = 48 + root + (-12 if root > 6 else 0)          # C3…F#3 for the chord root
    notes = [base + t for t in tones]
    while notes[0] > 55:                                  # keep the voicing around the middle
        notes = [n - 12 for n in notes]
    return notes


def scale_notes(root: int, mode: str, low: int, high: int) -> list[int]:
    scale = SCALES.get(mode, SCALES["major"])
    return [n for n in range(low, high + 1) if (n - root) % 12 in scale]


# ── sections of a track ──────────────────────────────────────────────────────

def template_sections(seconds: float, bpm: float) -> list[dict]:
    """A whole track with no video: intro → verse → (cut dead) silence → chorus → (cut dead) short
    silence → emotive outro, in whole bars (the arc of an explainer video, for listening)."""
    bar = 240.0 / bpm
    total = float(seconds)
    nbars = max(int(total / bar), 1)
    if nbars < 8:                                          # too short for an arc: intro + chorus + outro
        cuts = [0, max(nbars // 4, 1), max(nbars * 3 // 4, 2)]
        kinds = ["intro", "chorus", "outro"]
    else:
        intro = max(round(nbars * 0.11), 2)
        verse = max(round(nbars * 0.17), 2)
        chorus = max(round(nbars * 0.38), 4)
        rest = nbars - intro - verse - 1 - chorus
        if rest < 3:
            chorus = max(chorus - (3 - rest), 2)
        cuts = [0, intro, intro + verse, intro + verse + 1, intro + verse + 1 + chorus]
        kinds = ["intro", "verse", "silence", "chorus", "silence"]
        cuts.append(cuts[-1])                             # the short silence: half a bar (set below)
        kinds.append("outro")
    out = []
    for i, kind in enumerate(kinds):
        start = cuts[i] * bar
        end = cuts[i + 1] * bar if i + 1 < len(cuts) else total
        out.append({"kind": kind, "start": start, "end": end, "why": "track template"})
    if len(kinds) > 3:                                    # half-bar silence before the outro
        out[4]["end"] = out[4]["start"] + bar / 2
        out[5]["start"] = out[4]["end"]
    for s in out:
        s["start"], s["end"] = round(min(s["start"], total), 4), round(min(s["end"], total), 4)
    return [s for s in out if s["end"] > s["start"]]


# arc words of the script's comment headings ("# 2 · Conflict"), English and Spanish, accents ignored
ARC_WORDS = {
    "scene": "intro", "hook": "intro", "intro": "intro", "opening": "intro",
    "escena": "intro", "gancho": "intro", "inicio": "intro",
    "conflict": "silence", "grief": "silence", "problem": "silence",
    "conflicto": "silence", "duelo": "silence", "problema": "silence",
    "question": "chorus", "explanation": "chorus", "wow": "chorus", "amazement": "chorus", "data": "chorus",
    "pregunta": "chorus", "explicacion": "chorus", "asombro": "chorus", "dato": "chorus",
    "twist": "twist", "giro": "twist",
    "solution": "outro", "close": "outro", "closing": "outro", "ending": "outro", "outro": "outro",
    "solucion": "outro", "cierre": "outro", "final": "outro", "despedida": "outro",
}


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def arc_marks(script_text: str, split: callable) -> dict[int, str]:
    """{sentence number: arc kind} from the script's comment headings: a comment line holding an
    arc word (scene, conflict, question, explanation, wow, twist, solution, close — or the Spanish
    ones) marks the sentences after it. "twist and solution" in one heading: the first sentence is
    the twist, the next ones the solution. `split` is the script's own sentence splitter, so the
    numbers are the plan's sentence numbers (cue lines and blank lines are skipped the same way)."""
    marks: dict[int, str] = {}
    n = 0
    pending: list[str] = []
    for raw in str(script_text).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            words = re.findall(r"[a-z]+", _plain(line))
            if len(words) > MAX_HEADING_WORDS:              # a note, not a heading
                continue
            kinds = []
            for w in words:
                k = ARC_WORDS.get(w)
                if k and k not in kinds:
                    kinds.append(k)
            if kinds:
                pending = kinds
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        for _ in split(line):
            if pending:
                marks[n] = pending[0]
                if pending[0] == "twist" and len(pending) > 1:
                    marks[n] = "twist"
                    pending = pending[1:]                   # the next sentence: the solution
                else:
                    pending = []
            n += 1
    return marks


def is_question(text: str) -> bool:
    t = str(text or "").strip().rstrip("\"'»”)]")
    return t.endswith("?") or t.startswith("¿")


def arc_sections(total: float, sentences: list[dict], fullscreen: list[tuple[float, float]] | None = None,
                 marks: dict[int, str] | None = None, cuts: list[float] | None = None,
                 silences: bool = True) -> list[dict]:
    """The video's moments → music sections (the emotional arc of an explainer).

    sentences: [{"n", "start", "end", "text"}] in seconds of the final video ("start" = the first
    word), in time order. fullscreen: (start, end) of the full-screen cutaways. marks: arc kinds by
    sentence number (`arc_marks`). cuts: shot changes (used when there are no sentences).

    Rules (each section records why it is there):
      1. The music starts with the video: an energetic intro with a hit on frame 0.
      2. The chorus (the best part) starts on the first word of the question — the "question" mark,
         else the first sentence ending in "?" between MIN_INTRO and half the video, else the first
         full-screen graphic in the first half, else the sentence nearest 25 % of the video.
      3. Silence before it: the "conflict" sentences, or (no marks) the one sentence before the
         question; the music is cut dead on its first word. Never longer than MAX_SILENCE, never
         before MIN_INTRO.
      4. The emotive outro starts on the "solution" mark, else on the sentence after the twist
         (the first sentence after the last full-screen graphic, between 55 % and 90 % of the
         video), else on the sentence nearest 72 %; the twist sentence is silent (cut dead).
      5. Without sentences: chorus from the cut nearest 30 %, outro from the cut nearest 72 %.
      6. A music section shorter than MIN_SECTION joins the one before it; the last one fades out
         over the final second.
    `silences: False` keeps the music going through conflict and twist (no dead cuts).
    """
    total = float(total)
    marks = marks or {}
    sents = sorted((s for s in sentences if s.get("start") is not None and float(s["start"]) < total),
                   key=lambda s: float(s["start"]))
    if not sents:
        return _cut_sections(total, sorted(cuts or []))
    starts = [float(s["start"]) for s in sents]
    labels: list[str | None] = [marks.get(int(s["n"])) if s.get("n") is not None else None for s in sents]
    why: dict[int, str] = {}
    if any(labels):
        cur = "intro"
        for i, lab in enumerate(labels):
            if lab:
                cur = lab
                why[i] = f"script heading: {lab}"
            labels[i] = cur
    else:
        labels = _guess_labels(total, sents, fullscreen or [], why)
    music = "intro"
    for i, lab in enumerate(labels):
        if lab in ("silence", "twist"):
            labels[i] = "silence" if silences else music
        else:
            music = lab
    # runs of the same label → sections, each starting on its first sentence's first word
    out: list[dict] = []
    for i, lab in enumerate(labels):
        t = 0.0 if not out else starts[i]
        if out and out[-1]["kind"] == lab:
            continue
        if lab == "silence" and t < MIN_INTRO:
            lab = out[-1]["kind"] if out else "intro"
            if out:
                continue
        out.append({"kind": lab, "start": round(t, 4), "why": why.get(i, _default_why(lab, i == 0))})
    if out[0]["start"] > 0 or out[0]["kind"] == "silence":
        out[0]["start"] = 0.0
        if out[0]["kind"] == "silence":
            out[0]["kind"], out[0]["why"] = "intro", "the video starts"
    for a, b in zip(out, out[1:]):
        a["end"] = b["start"]
    out[-1]["end"] = round(total, 4)
    return _tidy(out, starts)


def _default_why(kind: str, first: bool) -> str:
    if first:
        return "the video starts"
    return {"chorus": "the question", "silence": "conflict: cut dead", "outro": "the solution",
            "intro": "the scene", "verse": "a verse"}.get(kind, kind)


def _guess_labels(total: float, sents: list[dict], fullscreen: list[tuple[float, float]], why: dict) -> list[str]:
    starts = [float(s["start"]) for s in sents]
    n = len(sents)
    q = next((i for i, s in enumerate(sents) if is_question(s.get("text"))
              and MIN_INTRO <= starts[i] <= total * 0.5), None)
    if q is not None:
        why[q] = "first question"
    else:
        g = next((a for a, _ in sorted(fullscreen) if MIN_INTRO <= a <= total * 0.5), None)
        if g is not None:
            q = max((i for i in range(n) if starts[i] <= g + 0.05), default=0)
            why[q] = "first full-screen graphic"
        else:
            q = min(range(n), key=lambda i: abs(starts[i] - total * 0.25))
            why[q] = "a quarter of the video"
    labels = ["intro"] * n
    for i in range(q, n):
        labels[i] = "chorus"
    if q >= 1 and starts[q - 1] >= MIN_INTRO and starts[q] - starts[q - 1] <= MAX_SILENCE:
        labels[q - 1] = "silence"
        why[q - 1] = "the sentence before the question: cut dead"
    later = [b for a, b in sorted(fullscreen) if b > starts[q] + MIN_SECTION]
    o = None
    if later:
        after = max(later)
        cand = next((i for i in range(q + 1, n) if starts[i] >= after - 0.05), None)
        if cand is not None and total * 0.55 <= starts[cand] <= total * 0.9:
            if cand + 1 < n and starts[cand + 1] - starts[cand] <= MAX_SILENCE:
                labels[cand] = "twist"
                why[cand] = "first sentence after the last full-screen graphic: the twist"
                o = cand + 1
            else:
                o = cand
    if o is None:
        cands = [i for i in range(q + 1, n) if starts[i] >= starts[q] + MIN_SECTION * 2]
        if cands:
            o = min(cands, key=lambda i: abs(starts[i] - total * 0.72))
    if o is not None:
        for i in range(o, n):
            labels[i] = "outro"
        why.setdefault(o, "the solution")
    return labels


def _cut_sections(total: float, cuts: list[float]) -> list[dict]:
    if total < 3 * MIN_SECTION:
        return [{"kind": "chorus", "start": 0.0, "end": round(total, 4), "why": "a short video: one part"}]

    def near(frac):
        inside = [c for c in cuts if MIN_SECTION <= c <= total - MIN_SECTION]
        return min(inside, key=lambda c: abs(c - total * frac)) if inside else total * frac

    a, b = near(0.30), near(0.72)
    if b - a < MIN_SECTION:
        b = max(a + MIN_SECTION, total * 0.72)
    out = [{"kind": "intro", "start": 0.0, "end": round(a, 4), "why": "the video starts"},
           {"kind": "chorus", "start": round(a, 4), "end": round(b, 4), "why": "the cut nearest 30 %"},
           {"kind": "outro", "start": round(b, 4), "end": round(total, 4), "why": "the cut nearest 72 %"}]
    return _tidy(out, [])


def _tidy(out: list[dict], starts: list[float]) -> list[dict]:
    """Silences at most MAX_SILENCE (they start later, on a sentence start when there is one);
    music sections shorter than MIN_SECTION join the previous one."""
    for i, s in enumerate(out):
        if s["kind"] == "silence" and s["end"] - s["start"] > MAX_SILENCE:
            later = [t for t in starts if s["end"] - MAX_SILENCE <= t < s["end"] - 0.3]
            new = later[0] if later else s["end"] - MAX_SILENCE
            if i:
                out[i - 1]["end"] = round(new, 4)
            s["start"] = round(new, 4)
    merged: list[dict] = []
    carry = None
    for idx, s in enumerate(out):
        s = dict(s)
        if carry is not None:
            s["start"], carry = carry, None
        if s["end"] - s["start"] <= 1e-6:
            continue
        short = s["kind"] != "silence" and s["end"] - s["start"] < MIN_SECTION and idx + 1 < len(out)
        if short and merged and merged[-1]["kind"] != "silence":
            merged[-1]["end"] = s["end"]                     # joins the music before it
            continue
        if short and merged:
            carry = s["start"]                               # after a silence: the next section starts here
            continue
        if merged and merged[-1]["kind"] == s["kind"]:
            merged[-1]["end"] = s["end"]
            continue
        merged.append(s)
    if (len(merged) > 1 and merged[-1]["kind"] != "silence" and merged[-2]["kind"] != "silence"
            and merged[-1]["end"] - merged[-1]["start"] < MIN_SECTION):
        merged[-2]["end"] = merged.pop()["end"]
    if merged and merged[-1]["kind"] == "silence":           # never end on a silence: it becomes the outro
        merged[-1]["kind"], merged[-1]["why"] = "outro", "the close"
    for i, s in enumerate(merged):
        prev = merged[i - 1]["kind"] if i else None
        s["hit"] = i == 0 or prev == "silence" or s["kind"] in ("chorus", "outro")
    return merged


# ── the score ────────────────────────────────────────────────────────────────

def compose(preset: dict, seed: int, sections: list[dict], duration: float, bpm: float | None = None,
            preset_name: str = "", accents: list[float] | None = None) -> dict:
    """The score of a track (module docstring). Sections in seconds; music sections that follow
    each other keep one beat grid (a boundary moves to the nearest beat); a section after a silence
    starts exactly on its time with a hit (a new grid from there). accents: seconds where a
    full-screen graphic comes in: a soft cymbal and a low thump right there (at most one every
    ACCENT_GAP s)."""
    bpm = float(bpm or preset["bpm"])
    if not 40 <= bpm <= 220:
        raise ValueError(f"music tempo {bpm:g} BPM is out of range (40–220)")
    T = 60.0 / bpm
    step = T / 4
    duration = float(duration)
    root, mode = key_root(preset["key"]), str(preset.get("mode", "major"))
    secs = _snap(sections, T, duration)
    events: list[dict] = []
    beats: list[float] = []
    downbeats: list[float] = []
    gates: list[list[float]] = []
    motif = _motif(seed, preset)
    occurrence: dict[str, int] = {}
    for i, s in enumerate(secs):
        kind = s["kind"]
        s["chords"] = []
        if kind == "silence":
            continue
        nxt = secs[i + 1] if i + 1 < len(secs) else None
        fade_out = FINAL_FADE if nxt is None else (DEAD_CUT if nxt["kind"] == "silence" else 0.0)
        fade_in = ENTRY_FADE if (i and secs[i - 1]["kind"] == "silence") else 0.0
        if gates and abs(gates[-1][1] - s["start"]) < 1e-6 and not fade_in:
            gates[-1][1], gates[-1][3] = s["end"], fade_out
        else:
            gates.append([s["start"], s["end"], fade_in, fade_out])
        k = occurrence.get(kind, 0)
        occurrence[kind] = k + 1
        _section_events(events, s, nxt, preset, seed, i, k, root, mode, T, step, motif, beats, downbeats)
    _accent_events(events, accents or [], secs)
    events = [e for e in events if e["t"] < duration]
    events.sort(key=lambda e: (e["t"], e["r"], e["i"], e.get("n") or 0))
    return {"v": COMPOSER_VERSION, "preset": preset_name, "seed": int(seed), "bpm": round(bpm, 3),
            "key": NOTE_NAMES[root], "mode": mode, "duration": round(duration, 4),
            "sections": secs,
            "beats": _unique(beats, duration),
            "downbeats": _unique(downbeats, duration),
            "gates": [[round(x, 4) for x in g] for g in gates], "events": events}


def _unique(times: list[float], duration: float) -> list[float]:
    out: list[float] = []
    for t in sorted(times):
        if t < duration and (not out or t - out[-1] > 1e-3):
            out.append(round(t, 4))
    return out


def _snap(sections: list[dict], T: float, duration: float) -> list[dict]:
    """Section boundaries inside a run of music go to the nearest beat of the run's grid."""
    out = [dict(s) for s in sections if s["end"] > s["start"]]
    anchor = None
    for i, s in enumerate(out):
        if s["kind"] == "silence":
            anchor = None
            continue
        if anchor is None or i == 0 or out[i - 1]["kind"] == "silence":
            anchor = float(s["start"])
        else:
            snapped = anchor + round((float(s["start"]) - anchor) / T) * T
            if snapped <= out[i - 1]["start"] + T:          # never swallow the previous section
                snapped = anchor + (int((float(s["start"]) - anchor) / T) + 1) * T
            if snapped < s["end"] - T:
                out[i - 1]["end"] = s["start"] = round(snapped, 4)
    for i, s in enumerate(out):
        s.setdefault("hit", i == 0 or out[i - 1]["kind"] == "silence" or s["kind"] in ("chorus", "outro"))
    out[-1]["end"] = round(duration, 4)
    return out


def _motif(seed: int, preset: dict) -> list[list[tuple[int, int, int]]]:
    """The chorus melody as four one-bar phrases A A' B A'' of (step, length in steps, contour
    degree): rhythm from RHYTHMS, a contour that moves mostly by step (realized on each bar's
    chord later)."""
    r = _rng(seed, "motif")
    density = float(preset.get("lead", {}).get("density", 0.5))
    pool = RHYTHMS if density >= 0.4 else SPARSE_RHYTHMS + RHYTHMS[:3]
    ra, rb = r.choice(pool), r.choice(pool)

    def phrase(rhythm: str, start_deg: int, end_deg: int | None) -> list[tuple[int, int, int]]:
        onsets = [i for i, c in enumerate(rhythm) if c == "x"]
        deg, notes = start_deg, []
        for j, o in enumerate(onsets):
            length = (onsets[j + 1] if j + 1 < len(onsets) else 16) - o
            if j:
                deg += r.choice((-2, -1, -1, 1, 1, 2, 0, 3, -3))
                deg = max(-3, min(7, deg))
            notes.append((o, max(min(length, 6), 1), deg))
        if end_deg is not None and notes:
            o, length, _ = notes[-1]
            notes[-1] = (o, length, end_deg)
        return notes

    a = phrase(ra, r.choice((0, 2, 4)), None)
    return [a, phrase(ra, a[0][2] if a else 0, 4), phrase(rb, r.choice((2, 4, 5)), None), phrase(ra, a[0][2] if a else 0, 0)]


def _ev(events, t, d, inst, role, vel, note=None, pan=0.0):
    e = {"t": round(max(t, 0.0), 5), "d": round(max(d, 0.01), 5), "i": inst, "r": role, "v": round(max(min(vel, 1.0), 0.0), 3),
         "p": round(pan, 3)}
    if note is not None:
        e["n"] = int(note)
    events.append(e)


def _section_events(events, s, nxt, preset, seed, index, occurrence, root, mode, T, step, motif, beats, downbeats):
    kind, start, end = s["kind"], float(s["start"]), float(s["end"])
    energy = ENERGY[kind]
    r = _rng(seed, "section", index, kind)
    outro = kind == "outro"
    prog_name = "chorus" if kind == "chorus" else ("outro" if outro else "main")
    prog = preset["progressions"].get(prog_name) or preset["progressions"]["main"]
    sevenths = bool(preset.get("sevenths"))
    chords = [chord(c, root, mode, sevenths) for c in prog]
    drums = DRUMS.get(str(preset["drums"].get(kind, "none")), {})
    swing = float(drums.get("swing", preset.get("swing", 0.0)))
    bass = preset.get("bass", {})
    bass_pattern = bass.get("outro_pattern", "half") if outro else bass.get("pattern", "half")
    arp = preset.get("arp", {})
    arp_sound = preset.get("outro", {}).get("arp", arp.get("sound")) if outro else arp.get("sound")
    arp_rate = int(preset.get("outro", {}).get("arp_rate", 4) if outro else arp.get("rate", 2))
    lead = preset.get("lead", {})
    lead_sound = preset.get("outro", {}).get("lead", "keys") if outro else lead.get("sound", "bell")
    bar = 4 * T
    nbars = max(int((end - start) / bar - 1e-6) + 1, 1)
    for b in range(nbars):
        t0 = start + b * bar
        if t0 >= end - 1e-3:
            break
        bar_end = min(t0 + bar, end)
        last_bar = t0 + bar >= end - 1e-3
        ch = chords[b % len(chords)]
        if b < 16:
            s["chords"].append(prog[b % len(prog)])
        downbeats.append(t0)
        for k in range(4):
            if t0 + k * T < end - 1e-3:
                beats.append(t0 + k * T)
        steps = [k for k in range(16) if t0 + k * step < end - 1e-3]

        def at(k, swing_on=True):
            return t0 + k * step + (swing * step if swing_on and k % 2 else 0.0) + r.uniform(-0.003, 0.003)

        # fill on the last (part of a) bar before a music section that continues the grid
        fill = last_bar and nxt is not None and nxt["kind"] != "silence" and kind != "break"
        kicks = []
        for inst, pats in drums.items():
            if inst in ("swing", "rolls"):
                continue
            pat = pats[b % len(pats)]
            for k in steps:
                c = pat[k]
                if c == "." or (fill and k >= 8 and inst in ("hat", "ohat", "shaker")):
                    continue
                v = VELOCITY[c] * (0.55 + 0.45 * energy) * r.uniform(0.9, 1.0)
                length = 0.3 if inst == "ohat" else step
                if drums.get("rolls") and inst == "hat" and ((b % 2 == 1 and k >= 12) or (kind == "chorus" and b % 4 == 3 and 8 <= k < 12)):
                    n = 3 if k % 2 else 2
                    for j in range(n):
                        _ev(events, at(k, False) + j * step / n, step / n, "hat", "drums", v * 0.8, pan=0.18)
                    continue
                if inst == "kick":
                    kicks.append(k)
                _ev(events, at(k), length, inst, "drums", v,
                    pan={"hat": 0.18, "ohat": 0.22, "shaker": -0.25, "tom": -0.15, "rim": 0.08}.get(inst, 0.0))
        if fill:
            first = 8 if len(steps) >= 12 else max(len(steps) - 4, 0)
            fill_steps = [k for k in steps if k >= first]
            for j, k in enumerate(fill_steps):
                v = (0.35 + 0.6 * j / max(len(fill_steps) - 1, 1)) * (0.6 + 0.4 * energy)
                inst = "tom" if (kind in ("verse", "intro") and k % 4 == 2 and drums.get("tom")) else "snare"
                _ev(events, at(k, False), step, inst, "drums", v, pan=-0.1 if inst == "tom" else 0.0)
            if nxt and nxt["kind"] == "chorus":
                _ev(events, t0, bar_end - t0, "riser", "fx", 0.7)
        # bass
        bass_note = ch[0] - 12                             # 55–98 Hz (an 808 adds a mid layer)
        if bass_pattern == "kick" and kicks:
            for j, k in enumerate(kicks):
                nk = kicks[j + 1] if j + 1 < len(kicks) else 16
                glide = ch[0] if (j == len(kicks) - 1 and b % 2 == 1 and r.random() < 0.6) else None
                e_d = (min(nk, len(steps)) - k) * step
                _ev(events, t0 + k * step, e_d, bass.get("sound", "808"), "bass", 0.85 * (0.7 + 0.3 * energy), bass_note)
                if glide is not None:
                    events[-1]["g"] = int(glide)
        else:
            pattern = {"offbeat": [(2, 2), (6, 2), (10, 2), (14, 2)], "eighths": [(k, 2) for k in range(0, 16, 2)],
                       "half": [(0, 7), (8, 7)], "whole": [(0, 15)], "kick": [(0, 7), (8, 7)]}.get(bass_pattern, [(0, 7), (8, 7)])
            for k, n in pattern:
                if t0 + k * step >= end - 1e-3:
                    continue
                note = bass_note + (12 if bass_pattern == "eighths" and k in (6, 14) else 0)
                _ev(events, t0 + k * step, n * step, bass.get("sound", "synth"), "bass", 0.8 * (0.75 + 0.25 * energy), note)
        # pad
        if preset.get("pad", {}).get("sound", "none") != "none":
            _ev(events, t0, bar_end - t0 + (0.25 if not last_bar else 0.0), preset["pad"]["sound"], "pad",
                0.55 + 0.35 * energy, ch[0] + 12)
            events[-1]["c"] = [int(n) + 12 for n in ch[1:]] if len(ch) > 3 else [int(n) + 12 for n in ch]
        # arpeggio
        if arp_sound and arp_sound != "none":
            tones = sorted(ch) if len(ch) <= 3 else sorted(ch[:4])
            octave = 12 * (1 + int(arp.get("octave", 0)))
            seq = [n + octave for n in tones] + [n + octave + 12 for n in tones]
            shape = arp.get("shape", "up")
            if shape == "updown":
                seq = seq + seq[-2:0:-1]
            elif shape == "finger":
                seq = [seq[0], seq[2], seq[1], seq[3 % len(seq)], seq[2], seq[1]] if len(seq) >= 4 else seq
            vel = (0.5 + 0.4 * energy) * (0.6 if kind in ("break", "verse") else 1.0)
            for j, k in enumerate(range(0, 16, max(arp_rate, 1))):
                if t0 + k * step >= end - 1e-3:
                    break
                pan = (-0.35 if j % 2 else 0.35) if arp_rate <= 2 else 0.0
                _ev(events, at(k), max(arp_rate, 1) * step * (1.6 if arp_sound in ("keys", "string") else 0.9),
                    arp_sound, "arp", vel * r.uniform(0.85, 1.0), seq[j % len(seq)], pan)
        # melody: the chorus motif (the same every time the chorus comes back); in the outro the
        # same contour, slower and lower
        if kind == "chorus" or outro:
            phrase = motif[b % 4]
            low, high = int(lead.get("low", 69)), int(lead.get("high", 81))
            if outro:
                shift = int(preset.get("outro", {}).get("lead_octave", -12))
                low, high = low + shift, high + shift
                phrase = [(o, min(L * 2, 8), d) for (o, L, d) in phrase if o % 8 == 0 or o == phrase[0][0]]
            pool = scale_notes(root, mode, low, high)
            tones = {n % 12 for n in ch}
            density = float(preset.get("outro", {}).get("density", 0.3) if outro else lead.get("density", 0.5))
            if density < 0.3 and b % 2:
                continue
            for o, L, d in phrase:
                if t0 + o * step >= end - 1e-3:
                    continue
                idx = max(0, min(len(pool) - 1, len(pool) // 3 + d))
                note = pool[idx]
                if o % 4 == 0:                             # strong steps: a chord tone
                    cands = [n for n in pool if n % 12 in tones]
                    if cands:
                        note = min(cands, key=lambda n: (abs(n - note), n))
                _ev(events, at(o), L * step * 1.1, lead_sound, "lead", (0.55 + 0.35 * energy) * r.uniform(0.85, 1.0),
                    note, 0.08)
        # chorus: a cymbal every 8 bars
        if kind == "chorus" and b and b % 8 == 0:
            _ev(events, t0, 2.0, "crash", "fx", 0.45)
    if s.get("hit"):
        _ev(events, start, 2.2, "crash", "fx", 0.8 if kind != "outro" else 0.5)
        _ev(events, start, 1.0, "impact", "fx", 0.9 if kind != "outro" else 0.6)


def _accent_events(events, accents, secs):
    last = -1e9
    hits = [float(s["start"]) for s in secs if s.get("hit")]
    for t in sorted(accents):
        sec = next((s for s in secs if s["start"] <= t < s["end"]), None)
        if sec is None or sec["kind"] == "silence" or t - last < ACCENT_GAP:
            continue
        if any(abs(t - h) < 1.0 for h in hits):
            continue
        _ev(events, t, 1.6, "crash", "fx", 0.35)
        _ev(events, t, 0.6, "impact", "fx", 0.5)
        last = t


# ── proof of origin ──────────────────────────────────────────────────────────

def score_hash(score: dict) -> str:
    """SHA-256 of the score (every note and hit): the same parameters always give the same hash."""
    return hashlib.sha256(json.dumps(score, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def cache_key(preset: dict, seed: int, sections: list[dict], duration: float, bpm: float | None,
              accents: list[float] | None = None) -> str:
    """Short hash of everything a composed track depends on (a new track when the timing changes)."""
    data = {"v": COMPOSER_VERSION, "preset": preset, "seed": int(seed), "bpm": bpm, "d": round(float(duration), 3),
            "s": [[s["kind"], round(float(s["start"]), 3), round(float(s["end"]), 3)] for s in sections],
            "a": [round(float(a), 3) for a in accents or []]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:12]


STATEMENTS = {
    "en": ("This instrumental track was generated by the program social-video-editor {version} "
           "(composer v{composer}) from the parameters below: preset \"{preset}\", seed {seed}, {bpm:g} BPM, "
           "key of {key} {mode}. It uses no samples, loops or recordings by third parties: every sound is "
           "synthesized from these numbers, and running the same version with the same parameters "
           "produces the same samples (SHA-256 below)."),
    "es": ("Esta pista instrumental la generó el programa social-video-editor {version} "
           "(compositor v{composer}) a partir de los parámetros de abajo: estilo \"{preset}\", semilla {seed}, "
           "{bpm:g} BPM, tonalidad {key} {mode}. No usa muestras, loops ni grabaciones de terceros: cada sonido "
           "se sintetiza a partir de estos números, y la misma versión con los mismos parámetros produce las "
           "mismas muestras (SHA-256 abajo)."),
}
MODE_NAMES = {"es": {"major": "mayor", "minor": "menor", "dorian": "dórico", "mixolydian": "mixolidio"}}


def origin(score: dict, version: str, audio: dict, preset: dict, language: str = "en",
           context: dict | None = None) -> dict:
    """The proof-of-origin record written next to a composed track."""
    lang = language if language in STATEMENTS else "en"
    mode = MODE_NAMES.get(lang, {}).get(score["mode"], score["mode"])
    return {
        "statement": STATEMENTS[lang].format(version=version, composer=score["v"], preset=score["preset"],
                                             seed=score["seed"], bpm=score["bpm"], key=score["key"], mode=mode),
        "engine": "social-video-editor", "engine_version": version, "composer_version": score["v"],
        "preset": score["preset"], "preset_settings": preset, "seed": score["seed"], "bpm": score["bpm"],
        "key": f"{score['key']} {score['mode']}", "duration": score["duration"],
        "sections": [{"kind": s["kind"], "start": s["start"], "end": s["end"], "why": s.get("why", ""),
                      "chords": s.get("chords", [])} for s in score["sections"]],
        "events": len(score["events"]), "score_sha256": score_hash(score),
        "audio": audio, **({"context": context} if context else {}),
    }


def section_lines(score: dict) -> list[str]:
    """Human summary of a composed track (render log, sve music)."""
    out = [f"Composed: preset {score['preset']}, seed {score['seed']}, {score['bpm']:g} BPM, "
           f"{score['key']} {score['mode']}, {score['duration']:.1f} s"]
    for s in score["sections"]:
        out.append(f"  {s['start']:6.2f}–{s['end']:6.2f} s  {s['kind']:<8}{'  hit' if s.get('hit') else '     '}"
                   f"  {s.get('why', '')}")
    return out
