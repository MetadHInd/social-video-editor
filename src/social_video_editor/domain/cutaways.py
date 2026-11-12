"""Cutaways: pictures shown over the main track while its sound keeps playing — a graphic, a photo
or a clip covering the presenter, who goes on talking underneath.

Plan format (plan["cutaways"]), anchored to script sentences (plan["sentences"], written by the
explainer plan) or to seconds of the final video:

    {"type": "graphic", "template": "bars", "data": "A:1, B:2", "at_sentence": 2, "until_sentence": 3}
    {"type": "photo", "file": "photos/board.jpg", "at_sentence": 5, "seconds": 3}
    {"type": "graphic", "template": "lower_third", "text": "…", "start": 4.0, "end": 8.0}
    {"type": "broll", "query": "…", "at_sentence": 3, "from_word": [0, 12.4]}     (from a word: [source, seconds])

Sentences are anchors, not shot numbers: shortening pauses splits and renumbers the shots, a
sentence keeps its place in the source. A cutaway starts when its first sentence starts and ends
when its last sentence ends (or after "seconds", if that is sooner).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .graphics.scene import TEMPLATES
from .models import PlanError
from .timeline import Timeline

KINDS = ("graphic", "photo", "broll")
PLACEMENT_KEYS = ("type", "at_sentence", "until_sentence", "seconds", "start", "end", "note", "pip", "item_times",
                  "from_word", "fallback")
PIP_KINDS = ("graphic", "photo")   # full-frame cutaways the presenter's window can sit on
NO = {"0", "false", "no", "off", "n"}
READ_SECONDS = 0.8          # a cutaway stays at least its entrance + this long (time to read it)
DEFAULT_ENTRANCE = 1.0      # entrance assumed when it cannot be measured (no drawing library)
PHOTO_ENTRANCE = 0.4
MIN_VISIBLE = 0.6           # a cutaway that would be shorter than this is left out
MIN_BETWEEN = 0.8           # the presenter is not shown for less than this between two cutaways
FREE_SECONDS = 3.0          # a cutaway placed by "start" alone, without "end" or "seconds"


@dataclass
class Cutaway:
    index: int                           # position in plan["cutaways"]
    kind: str                            # graphic | photo | broll
    spec: dict                           # what it shows (without the keys that only place it)
    start: float                         # seconds of the final video
    end: float
    overlay: bool = False                # drawn over the picture (transparent) instead of replacing it
    sentences: tuple[int, int] | None = None
    pip: bool = False                    # the presenter stays in a window over it (domain/pip.py)
    auto: str | None = None              # who proposed it ("support"), None when it was written by hand

    @property
    def label(self) -> str:
        return str(self.spec.get("template") or self.spec.get("file") or self.spec.get("query") or self.kind)


def spec_of(cutaway: dict) -> dict:
    """What a cutaway shows: the plan entry without placement and private keys."""
    return {k: v for k, v in cutaway.items() if k not in PLACEMENT_KEYS and not k.startswith("_")}


def kind_of(cutaway: dict, index: int = 0) -> str:
    kind = str(cutaway.get("type") or "graphic")
    if kind not in KINDS:
        raise PlanError(f"Cutaway {index}: unknown type '{kind}'. Options: {', '.join(KINDS)}")
    return kind


def is_overlay(cutaway: dict) -> bool:
    """Graphics made to sit over the picture (a lower third) do not hide the presenter."""
    if str(cutaway.get("type") or "graphic") != "graphic":
        return False
    return bool(getattr(TEMPLATES.get(str(cutaway.get("template") or "").lower()), "overlay", False))


def sentence_spans(plan: dict, timeline: Timeline) -> dict[int, tuple[float, float]]:
    """{sentence number: (start, end)} in seconds of the final video, for the sentences that are in
    it. A sentence is where the shots play its piece of the source; a pause shortened inside it
    simply makes it shorter."""
    windows = []
    for clip in timeline:
        w = clip.piece.source_window()
        if w is not None:
            windows.append((clip, *w))
    out: dict[int, tuple[float, float]] = {}
    for s in plan.get("sentences") or []:
        if s.get("start") is None or s.get("end") is None or s.get("source") is None:
            continue
        a, b = float(s["start"]), float(s["end"])
        run = []
        for clip, source, start, end, speed in windows:
            if source == s["source"] and start < b and end > a:
                if run and clip.index != run[-1][0].index + 1:
                    break                                   # the same piece used again later: the first run counts
                run.append((clip, start, end, speed))
        if not run:
            continue
        first, last = run[0], run[-1]
        t0 = first[0].out_start + (max(a, first[1]) - first[1]) / first[3]
        t1 = last[0].out_start + (min(b, last[2]) - last[1]) / last[3]
        if t1 > t0:
            out[int(s["n"])] = (t0, t1)
    return out


def output_time(timeline: Timeline, source: int, t: float) -> float | None:
    """Second of the final video at which second `t` of a source is played (the first time). A moment
    that was cut out (a shortened pause) lands where the next piece of that source starts."""
    later = None
    for clip in timeline:
        w = clip.piece.source_window()
        if w is None or w[0] != source:
            continue
        _, start, end, speed = w
        if start <= t < end:
            return clip.out_start + (t - start) / speed
        if start > t and later is None:
            later = clip.out_start
    return later


def wants_pip(cutaway: dict, settings: dict | None) -> bool:
    """Whether the presenter's window goes over a cutaway: its own "pip" (true / false) wins, else
    the pip settings' `enabled`."""
    own = cutaway.get("pip")
    if own is not None:
        return own is not False and str(own).strip().lower() not in NO
    return bool((settings or {}).get("enabled"))


def resolve(plan: dict, timeline: Timeline, fps: int, entrance: Callable[[dict], float] | None = None,
            pip: dict | None = None) -> tuple[list[Cutaway], list[str]]:
    """Places every cutaway of the plan on the final video → (cutaways in time order, warnings).

    pip: the picture-in-picture settings (domain/pip.py): full-frame graphics and photos that last
    at least `min_seconds` get `pip = True` (the presenter stays in a window over them).
    An entry's "item_times" ([[source, seconds], …]: when each item of an overlay is said) become the
    graphic's `times`, in seconds from the moment the cutaway starts.

    entrance(plan entry) → seconds the picture takes to come in (a graphic's `settle`); a cutaway
    stays at least that + READ_SECONDS, growing up to the next cutaway when its sentence is shorter.
    Two cutaways of the same layer never overlap (the first one ends where the next starts) and the
    presenter is never flashed between two of them. What does not fit is left out with a warning."""
    total = timeline.total
    spans = sentence_spans(plan, timeline)
    warnings: list[str] = []
    placed: list[tuple[Cutaway, float, bool]] = []          # (cutaway, minimum seconds, end fixed by the plan)
    for i, c in enumerate(plan.get("cutaways") or []):
        kind = kind_of(c, i)
        item = Cutaway(i, kind, spec_of(c), 0.0, 0.0, is_overlay(c), auto=c.get("_auto"))
        seconds = _seconds(c, "seconds", i)
        fixed = False
        if c.get("start") is not None:
            start = _seconds(c, "start", i) or 0.0
            if c.get("end") is not None:
                end, fixed = _seconds(c, "end", i), True
            else:
                end = start + (seconds or FREE_SECONDS)
        elif c.get("at_sentence") is not None:
            a = int(c["at_sentence"])
            u = int(c.get("until_sentence", a))
            found = [spans[k] for k in range(a, max(u, a) + 1) if k in spans]
            item.sentences = (a, max(u, a))
            if not found:
                warnings.append(f"cutaway {i} ({item.label}): sentence {a}{f'–{u}' if u > a else ''} is not in "
                                "the video; left out")
                continue
            start, end = found[0][0], found[-1][1]
            if c.get("from_word"):                          # starts when that word is said, not with the sentence
                src, t = c["from_word"]
                at = output_time(timeline, int(src), float(t))
                if at is not None and start <= at < end:
                    start = at
            if seconds:
                end = min(end, start + seconds)
                fixed = kind == "broll"                     # third-party footage: "seconds" is a ceiling, never outgrown
        else:
            raise PlanError(f"Cutaway {i} ({item.label}) needs 'at_sentence' (a script sentence) or 'start' (seconds)")
        if start >= total:
            warnings.append(f"cutaway {i} ({item.label}): starts at {start:.1f} s, after the end of the video; left out")
            continue
        item.start, item.end = start, end
        come_in = float(entrance(c)) if entrance else (PHOTO_ENTRANCE if kind != "graphic" else DEFAULT_ENTRANCE)
        placed.append((item, come_in + READ_SECONDS, fixed))
    placed.sort(key=lambda p: (p[0].start, p[0].index))
    out: list[Cutaway] = []
    full_starts = sorted(p[0].start for p in placed if not p[0].overlay)
    for layer in (False, True):
        row = [p for p in placed if p[0].overlay is layer]
        for k, (item, minimum, fixed) in enumerate(row):
            limit = row[k + 1][0].start if k + 1 < len(row) else total
            if not fixed and item.end - item.start < minimum:
                grown = item.start + minimum                            # time to come in and be read
                if layer:                                               # an overlay does not grow onto a full-frame cutaway
                    grown = min([grown, *(s for s in full_starts if s >= item.end - 1e-6)])
                item.end = max(item.end, grown)
            if not layer and not fixed and 0 < limit - item.end < MIN_BETWEEN and k + 1 < len(row):
                item.end = limit                                        # no flash of the presenter in between
            item.end = min(item.end, limit, total)
            item.start, item.end = _frame(item.start, fps), _frame(item.end, fps)
            length = item.end - item.start
            if length < MIN_VISIBLE:
                warnings.append(f"cutaway {item.index} ({item.label}): only {max(length, 0):.1f} s before the next "
                                "one; left out")
                continue
            if length < minimum - 1 / fps:
                warnings.append(f"cutaway {item.index} ({item.label}): {length:.1f} s on screen, it needs "
                                f"{minimum:.1f} s to come in and be read")
            source = plan["cutaways"][item.index]
            item.pip = (not layer and item.kind in PIP_KINDS and wants_pip(source, pip)
                        and (source.get("pip") is not None or length >= float((pip or {}).get("min_seconds", 0))))
            if source.get("item_times"):
                item.spec["times"] = _item_times(source["item_times"], timeline, item.start, length)
            out.append(item)
    out.sort(key=lambda c: (c.start, c.overlay, c.index))
    return out, warnings


def _item_times(marks: list, timeline: Timeline, start: float, length: float) -> list[float]:
    """Source moments → seconds from the cutaway's start (never going back, never outside it)."""
    times: list[float] = []
    for source, t in marks:
        at = output_time(timeline, int(source), float(t))
        rel = 0.0 if at is None else min(max(at - start, 0.0), max(length - 0.5, 0.0))
        times.append(round(max(rel, times[-1] if times else 0.0), 3))
    return times


def covers(cutaways: list[Cutaway], t: float, margin: float = 0.05) -> bool:
    """True when a full-frame cutaway hides the main track at second `t` (one without the presenter's
    window: in a window the cut is still seen), or begins / ends right there:
    a cut of the main track at that moment is not seen as a jump (the picture before or after it is
    the cutaway)."""
    return any(not c.overlay and not c.pip and c.start - margin <= t <= c.end + margin for c in cutaways)


def _seconds(cutaway: dict, key: str, index: int) -> float | None:
    v = cutaway.get(key)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = -1.0
    if v < 0 or (key == "seconds" and v == 0):
        raise PlanError(f"Cutaway {index}: '{key}' must be a number of seconds, not {cutaway.get(key)!r}")
    return v


def _frame(t: float, fps: int) -> float:
    return round(round(t * fps) / fps, 4)
