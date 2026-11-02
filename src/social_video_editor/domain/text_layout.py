"""Timing of on-screen text (Composite pattern); the Pillow adapter draws the images.

    Layer                       composite: everything shown over the video
      ├── Caption               leaf: one auto-subtitle image (word highlighted), optional pop
      └── Card                  composite: a plan text, positioned on screen
            ├── TextElement     title units, revealed one by one ("typewriter") or all at once
            └── TextElement     optional description, typed after the title

Every node answers cuts() (instants where the picture changes) and state(t) (what is visible),
recursively. The layer turns the cuts into a list of still frames with their durations.
Also here: auto-subtitle grouping, text style resolution and the .srt format.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import NamedTuple

POP = [(0.0, 0.80), (1 / 30, 1.10), (3 / 30, 1.0)]   # pop-in animation: (offset, scale)
BLINK = 0.38                                          # cursor blink period after typing
TYPE_START = 0.04                                     # typing starts this long after the text appears
DESC_PAUSE = 0.14                                     # pause between title and description

# keys a plan text may use to override its style
STYLE_OVERRIDES = ("size", "color", "background", "x", "y", "uppercase", "max_width", "font",
                   "desc_font", "desc_color")


class CardState(NamedTuple):
    key: str
    title: int            # visible title units
    description: int      # visible description units
    cursor: str | None    # "t" (after the title), "d" (after the description) or None


class LayoutNode(ABC):
    def children(self) -> list[LayoutNode]:
        return []

    def walk(self) -> Iterator[LayoutNode]:
        yield self
        for c in self.children():
            yield from c.walk()

    def cuts(self) -> set[float]:
        out: set[float] = set()
        for c in self.children():
            out |= c.cuts()
        return out


class TextElement(LayoutNode):
    """A run of visible units; with reveal times it is typed one unit at a time."""

    def __init__(self, units: list[str], reveal_times: list[float] | None = None):
        self.units, self.reveal_times = list(units), reveal_times

    @property
    def n(self) -> int:
        return len(self.units)

    def state(self, t: float) -> int:
        """Number of visible units at time t."""
        if self.reveal_times is None:
            return self.n
        return sum(1 for x in self.reveal_times if x <= t)

    def cuts(self) -> set[float]:
        return set(self.reveal_times or [])

    def keystrokes(self) -> list[float]:
        """One keystroke per typed unit that is not a space."""
        if self.reveal_times is None:
            return []
        return [t for t, u in zip(self.reveal_times, self.units) if u != " "]


class OnScreen(LayoutNode):
    """Something shown between start and end, centered at (x, y) pixels, optionally popping in."""

    def __init__(self, key: str, start: float, end: float, x: float, y: float, pop: bool):
        self.key, self.start, self.end, self.x, self.y, self.pop = key, start, end, x, y, pop

    def cuts(self) -> set[float]:
        c = {self.start, self.end, *super().cuts()}
        if self.pop:
            c.update(self.start + dt for dt, _ in POP)
        return c

    def keystrokes(self) -> list[float]:
        return []

    def scale(self, t: float) -> float:
        s = 1.0
        if self.pop:
            for dt, k in POP:
                if t >= self.start + dt:
                    s = k
        return s

    @abstractmethod
    def state(self, t: float):
        """Hashable description of the picture at time t."""


class Caption(OnScreen):
    """A fixed image (one auto-subtitle step)."""

    def state(self, t: float):
        return (self.key,)


class Card(OnScreen):
    """A plan text: title + optional description, static or typed letter by letter."""

    def __init__(self, key, start, end, x, y, pop, title: TextElement,
                 description: TextElement | None = None, typing_end: float | None = None):
        super().__init__(key, start, end, x, y, pop)
        self.title, self.description, self.typing_end = title, description, typing_end

    @classmethod
    def static(cls, key, start, end, x, y, title_units, desc_units=None, pop=True) -> Card:
        return cls(key, start, end, x, y, pop, TextElement(title_units),
                   TextElement(desc_units) if desc_units is not None else None)

    @classmethod
    def typewriter(cls, key, start, end, x, y, title_units, desc_units=None, speed: float = 30,
                   pop=True) -> Card:
        """Title typed first, then the description; always done with time left to read (≤ 75 %)."""
        n_t, n_d = len(title_units), len(desc_units or [])
        available = max(end - start - 0.35, 0.3)
        v = max(float(speed), (n_t + n_d) / (0.75 * available))
        t0 = start + TYPE_START
        times_t = [t0 + (k + 1) / v for k in range(n_t)]
        has_desc = desc_units is not None
        t1 = (times_t[-1] if times_t else t0) + (DESC_PAUSE if has_desc else 0)
        times_d = [t1 + (k + 1) / (v * 1.05) for k in range(n_d)] if has_desc else []
        typing_end = (times_d or times_t or [t0])[-1]
        return cls(key, start, end, x, y, pop, TextElement(title_units, times_t),
                   TextElement(desc_units, times_d) if has_desc else None, typing_end)

    def children(self) -> list[LayoutNode]:
        return [self.title] + ([self.description] if self.description else [])

    def _blinks(self) -> list[float]:
        if self.typing_end is None:
            return []
        te = self.typing_end
        return [te + BLINK * (j + 1) for j in range(int((self.end - te) / BLINK))]

    def cuts(self) -> set[float]:
        return super().cuts() | set(self._blinks())

    def keystrokes(self) -> list[float]:
        return [t for c in self.children() for t in c.keystrokes()]

    def state(self, t: float) -> CardState:
        n_t = self.title.state(t)
        n_d = self.description.state(t) if self.description else 0
        if self.typing_end is None:
            return CardState(self.key, n_t, n_d, None)
        has_desc = self.description is not None
        if t < self.typing_end:
            cur = "t" if (n_t < self.title.n or not has_desc) else "d"
        else:
            cur = ("d" if has_desc else "t") if int((t - self.typing_end) / BLINK) % 2 == 1 else None
        return CardState(self.key, n_t, n_d, cur)


class Layer(LayoutNode):
    """Everything drawn over the video."""

    def __init__(self, elements: list[OnScreen]):
        self.elements = list(elements)

    def children(self) -> list[LayoutNode]:
        return self.elements

    def keystrokes(self) -> list[float]:
        return sorted(t for e in self.elements for t in e.keystrokes())

    def frames(self, total: float) -> list[tuple[float, float, list[tuple[OnScreen, object, float]]]]:
        """Still frames: (start, end, [(element, state, scale), …]) covering [0, total]."""
        cuts = sorted(c for c in ({0.0, total} | self.cuts()) if 0 <= c <= total)
        out = []
        for a, b in zip(cuts, cuts[1:]):
            if b - a < 1e-4:
                continue
            mid = (a + b) / 2
            active = [(e, e.state(mid), round(e.scale(mid), 2)) for e in self.elements if e.start <= mid < e.end]
            out.append((a, b, active))
        return out


# ── styles ───────────────────────────────────────────────────────────────────

def resolve_style(text: dict, styles: dict, scale: float = 1.0) -> dict:
    """Style of a plan text: its named style, the text's own overrides, sizes scaled to the output width."""
    st = dict(styles.get(text.get("style", "simple"), styles["simple"]))
    st.update({k: v for k, v in text.items() if k in STYLE_OVERRIDES})
    st["size"] = int(st["size"] * scale)
    if "desc_size" in st:
        st["desc_size"] = int(st["desc_size"] * scale)
    return st


def text_animation(text: dict, styles: dict) -> str | None:
    return text.get("animation", styles.get(text.get("style", "simple"), {}).get("animation"))


# ── auto subtitles ───────────────────────────────────────────────────────────

def subtitle_groups(analyses: list[dict], timeline, cfg: dict) -> list[dict]:
    """Words of every source → groups of 2-3 words in final-video time.

    Returns [{"words": [{"word", "start", "end", "shot"}, …], "start", "end"}, …].
    """
    from .text_units import clean

    s = cfg["subtitles"]
    words = []
    for clip in timeline:     # every word belongs to ONE shot and must fit inside it
        window = clip.piece.source_window()
        if window is None:
            continue
        source, start, end, v = window
        for w in analyses[source].get("words", []):
            if w["start"] < start - 0.02 or w["end"] > end + 0.08:
                continue
            t0 = clip.out_start + (w["start"] - start) / v
            if clip.index > 0 and t0 < clip.out_start + clip.overlap:
                continue                                    # inside the entry transition
            t1 = clip.out_start + (min(w["end"], w["start"] + 1.5) - start) / v
            words.append({"word": clean(w["word"]), "start": t0, "end": max(t1, t0 + 0.12), "shot": clip.index})
    words.sort(key=lambda w: w["start"])
    groups, current = [], []
    for w in words:
        if not w["word"]:
            continue
        txt = " ".join(x["word"] for x in current + [w])
        split = current and (len(current) >= s["max_words"] or len(txt) > s["max_chars"]
                             or w["shot"] != current[-1]["shot"]
                             or w["start"] - current[-1]["end"] > 0.5 or current[-1]["word"][-1:] in ".?!,")
        if split:
            groups.append(current)
            current = []
        current.append(w)
    if current:
        groups.append(current)
    res = []
    for i, g in enumerate(groups):
        nxt = groups[i + 1][0]["start"] if i + 1 < len(groups) else g[-1]["end"] + 0.4
        res.append({"words": g, "start": g[0]["start"], "end": min(g[-1]["end"] + 0.15, nxt)})
    return res


def _srt_time(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def srt_text(groups: list[dict], texts: list[dict]) -> str:
    """Subtitles in .srt format (auto subtitles, or the plan texts if there are none)."""
    items = [(g["start"], g["end"], " ".join(w["word"] for w in g["words"])) for g in groups]
    if not items:
        items = [(t["start"], t["end"], t["text"]) for t in texts]
    items.sort()
    return "\n".join(f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{txt}\n" for i, (a, b, txt) in enumerate(items, 1))
