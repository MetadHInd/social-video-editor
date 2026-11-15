"""Shorts: vertical videos of their own cut from the same explainer project — a stretch of its
sentences with the cutaways that fall inside, the same framing, subtitles and sound.

Where they come from (plan["shorts"]):

    {"name": "memory", "from_sentence": 3, "to_sentence": 9}                 written in the script:
                                                                             [short memory] … [/short]
    {"name": "auto-1", "from_sentence": 2, "to_sentence": 8, "seconds": 34.5,
     "score": 6.1, "why": "starts with a question · has a bars graphic · ends on a statement · 35 s",
     "_auto": "shorts"}                                                      proposed (script.shorts: auto)

The long video ignores them. `sve render <project> --short NAME` renders one (short_plan), `--shorts`
all of them.

How a short is proposed (propose: simple, every point says why): every stretch of consecutive
sentences found in the recording that lasts `script.short_min`–`short_max` seconds and STARTS with
a question or a hook (a "did you know / imagine / the secret / nobody / why…" kind of opening, or a
figure) is a candidate. Its score:

    +3  starts with a question            +2  starts with a hook word / a figure
    −1  starts with a word that leans on what came before ("and", "but", "so"…)
    +2  has a full-frame graphic (a cue or a proposal) inside, else +1 when it says a figure
    +1  ends on a statement (. or !), or with the script's own last sentence
    +1  the sentence after it opens something new (a question or a hook): the stretch closes a thought
    +1 − |seconds − 40| / 40   (a length near 40 s is best)

The best candidates that do not overlap each other (nor a short written in the script) are kept, up
to `script.shorts_max`.
"""
from __future__ import annotations

import copy
import re

from .cutaways import is_overlay
from .models import PlanError
from .script import tokens

DEFAULTS = {"shorts": False,         # auto = propose shorts (they are recorded in the plan, shown in sve report)
            "shorts_max": 3,         # at most this many proposals
            "short_min": 20.0,       # seconds a proposed short lasts at least…
            "short_max": 60.0}       # …and at most
AUTO = "shorts"                      # "_auto" of a proposed short
IDEAL = 40.0                         # seconds: the length a proposal is scored best at
GAP = 0.35                           # seconds assumed between two sentences when a length is estimated
HOOK_WORDS = {               # first words that open something (a question is caught by its "?")
    "sabias", "imagina", "imaginate", "secreto", "nadie", "nunca", "error", "errores", "verdad", "mito", "truco",
    "pregunta", "atencion", "ojo", "olvida", "know", "imagine", "secret", "nobody", "never", "mistake", "mistakes",
    "truth", "myth", "trick", "stop", "forget", "question",
}
LEANING = {"y", "e", "pero", "entonces", "asi", "tambien", "ademas", "porque", "eso", "esto", "esta", "ese", "esa",
           "ahi", "luego", "and", "but", "so", "also", "because", "that", "this", "it", "then", "there"}
NUMBER_WORDS = {"dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "veinte", "treinta",
                "cien", "mil", "millon", "millones", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                "ten", "twenty", "thirty", "hundred", "thousand", "million", "millions"}
_DIGIT = re.compile(r"\d")


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **{k: v for k, v in ((cfg or {}).get("script") or {}).items() if k in DEFAULTS}}


def auto_on(cfg: dict) -> bool:
    return str(settings(cfg)["shorts"]).strip().lower() == "auto"


def from_marks(marks) -> list[dict]:
    """The shorts written in the script (domain/script.py ShortMark) as plan entries."""
    return [{"name": m.name, "from_sentence": m.first, "to_sentence": m.last} for m in marks]


# ── proposing ────────────────────────────────────────────────────────────────

def _found(sentences: list[dict]) -> list[dict]:
    return [s for s in sentences or [] if not s.get("missing") and s.get("start") is not None
            and s.get("end") is not None]


def estimate(sentences: list[dict], first: int, last: int) -> float:
    """Seconds a stretch of sentences lasts in the video (≈: the sentences found, a short pause between)."""
    said = [s for s in _found(sentences) if first <= int(s["n"]) <= last]
    return sum(float(s["end"]) - float(s["start"]) for s in said) + GAP * max(len(said) - 1, 0)


def _question(text: str) -> bool:
    t = text.strip()
    return t.endswith("?") or t.startswith("¿")


def _hook(text: str) -> bool:
    words = tokens(text)
    return bool(words) and (words[0] in HOOK_WORDS or bool(_DIGIT.search(words[0])) or words[0] in NUMBER_WORDS)


def _opener(text: str) -> bool:
    return _question(text) or _hook(text)


def _figure(text: str) -> bool:
    return bool(_DIGIT.search(text)) or any(w in NUMBER_WORDS for w in tokens(text))


def _graphic_inside(cutaways: list[dict], first: int, last: int) -> str | None:
    """The first full-frame graphic shown during sentences first–last (its template), or None."""
    for c in cutaways or []:
        if str(c.get("type") or "graphic") != "graphic" or is_overlay(c) or c.get("at_sentence") is None:
            continue
        if first <= int(c["at_sentence"]) <= last:
            return str(c.get("template") or "graphic")
    for c in cutaways or []:                    # a drawn card that stands in for footage counts too
        fb = c.get("fallback") if isinstance(c.get("fallback"), dict) else None
        if fb and c.get("at_sentence") is not None and first <= int(c["at_sentence"]) <= last:
            return str(fb.get("template") or "card")
    return None


def score(sentences: list[dict], cutaways: list[dict], first: int, last: int) -> tuple[float, list[str]]:
    """(score, reasons) of the stretch first–last (see the module's docstring)."""
    by_n = {int(s["n"]): s for s in sentences or []}
    text = str(by_n[first]["text"])
    points, why = 0.0, []
    if _question(text):
        points += 3
        why.append("starts with a question")
    elif _hook(text):
        points += 2
        why.append("starts with a hook")
    words = tokens(text)
    if words and words[0] in LEANING:
        points -= 1
        why.append(f"leans on what came before ('{words[0]}')")
    graphic = _graphic_inside(cutaways, first, last)
    if graphic:
        points += 2
        why.append(f"has a {graphic} graphic")
    elif any(_figure(str(by_n[k]["text"])) for k in range(first, last + 1) if k in by_n):
        points += 1
        why.append("says a figure")
    end = str(by_n[last]["text"]).strip()
    is_last = last == max(by_n)
    if end.endswith((".", "!", "…")) or is_last:
        points += 1
        why.append("ends the script" if is_last else "ends on a statement")
    nxt = next((by_n[k] for k in sorted(by_n) if k > last and not by_n[k].get("missing")), None)
    if nxt is not None and _opener(str(nxt["text"])):
        points += 1
        why.append("a new question follows")
    seconds = estimate(sentences, first, last)
    points += 1 - abs(seconds - IDEAL) / IDEAL
    why.append(f"{seconds:.0f} s")
    return round(points, 2), why


def propose(sentences: list[dict], cutaways: list[dict], cfg: dict, taken: list[dict] | None = None) -> list[dict]:
    """Shorts the engine proposes by itself (see the module's docstring); `taken`: shorts already
    written in the script (a proposal never overlaps one)."""
    p = settings(cfg)
    lo, hi = float(p["short_min"]), float(p["short_max"])
    found = sorted(int(s["n"]) for s in _found(sentences))
    by_n = {int(s["n"]): s for s in sentences or []}
    candidates = []
    for a_i, first in enumerate(found):
        if not _opener(str(by_n[first]["text"])):
            continue
        for last in found[a_i:]:
            seconds = estimate(sentences, first, last)
            if seconds > hi:
                break
            if seconds < lo:
                continue
            points, why = score(sentences, cutaways, first, last)
            candidates.append((points, seconds, first, last, why))
    candidates.sort(key=lambda c: (-c[0], c[2], -c[1]))
    busy = [(int(t["from_sentence"]), int(t["to_sentence"])) for t in taken or []]
    out = []
    for points, seconds, first, last, why in candidates:
        if len(out) >= int(p["shorts_max"]):
            break
        if any(first <= b and last >= a for a, b in busy):
            continue
        busy.append((first, last))
        out.append({"name": "", "from_sentence": first, "to_sentence": last, "seconds": round(seconds, 1),
                    "score": points, "why": " · ".join(why), "_auto": AUTO})
    out.sort(key=lambda s: s["from_sentence"])
    names = {t.get("name") for t in taken or []}
    k = 0
    for s in out:
        k += 1
        while f"auto-{k}" in names:
            k += 1
        s["name"] = f"auto-{k}"
    return out


# ── rendering one ────────────────────────────────────────────────────────────

def shorts_of(plan: dict) -> list[dict]:
    return [s for s in (plan or {}).get("shorts") or [] if isinstance(s, dict) and s.get("name")]


def find(plan: dict, name: str) -> dict:
    """The short called `name` (the name is compared like the script writes it: case and accents aside)."""
    from .script import short_name

    wanted = short_name(name)
    shorts = shorts_of(plan)
    for s in shorts:
        if short_name(s["name"]) == wanted:
            return s
    options = ", ".join(s["name"] for s in shorts) or "none (write [short name] … [/short] in the script, " \
                                                       "or set script.shorts: auto and run sve plan again)"
    raise PlanError(f"The plan has no short '{name}'. Shorts: {options}")


def short_plan(plan: dict, short: dict) -> dict:
    """The plan of one short: the camera shots that play its sentences (a shot that also plays a
    sentence outside it is trimmed halfway between the two), the cutaways anchored inside it (one
    that starts earlier or ends later is clamped to it) and no texts. Cutaways placed by seconds of
    the long video are left out (those seconds mean nothing in the short)."""
    first, last = int(short["from_sentence"]), int(short["to_sentence"])
    spans = {int(s["n"]): (s["source"], float(s["start"]), float(s["end"])) for s in _found(plan.get("sentences"))}
    inside = {n for n in spans if first <= n <= last}
    if not inside:
        raise PlanError(f"Short '{short.get('name')}': none of its sentences ({first}–{last}) is in the recording")
    shots = []
    for shot in plan.get("shots") or []:
        source = shot.get("source")
        if not isinstance(source, int) or shot.get("type") not in (None, "video"):
            continue
        a, b = float(shot["start"]), float(shot["end"])
        over = [n for n, (src, s0, s1) in spans.items() if src == source and s0 < b and s1 > a]
        mine = [n for n in over if n in inside]
        if not mine:
            continue
        in_a, in_b = min(spans[n][1] for n in mine), max(spans[n][2] for n in mine)
        before = [spans[n][2] for n in over if n not in inside and spans[n][2] <= in_a + 0.05]
        after = [spans[n][1] for n in over if n not in inside and spans[n][1] >= in_b - 0.05]
        new = copy.deepcopy(shot)
        if before:
            new["start"] = round(max(a, (max(before) + in_a) / 2), 3)
        if after:
            new["end"] = round(min(b, (min(after) + in_b) / 2), 3)
        if new["end"] - new["start"] > 0.05:
            shots.append(new)
    if not shots:
        raise PlanError(f"Short '{short.get('name')}': no shot of the plan plays its sentences")
    shots[0]["transition"] = "cut"
    shots[0].pop("continues", None)
    shots[0].pop("pause_cut", None)
    cutaways = []
    for c in plan.get("cutaways") or []:
        if c.get("at_sentence") is None:
            continue
        a, u = int(c["at_sentence"]), int(c.get("until_sentence", c["at_sentence"]))
        if u < first or a > last:
            continue
        new = copy.deepcopy(c)
        if a < first:                       # it began before the short: from the short's first sentence
            new["at_sentence"] = first
            new.pop("from_word", None)
            new.pop("item_times", None)
        if u > last:
            new["until_sentence"] = last
        cutaways.append(new)
    out = {k: copy.deepcopy(v) for k, v in plan.items() if k not in ("shots", "cutaways", "texts", "shorts")}
    out.update(note=f"Short {short['name']}: sentences {first}–{last} of the long video", shots=shots,
               cutaways=cutaways, texts=[], short=dict(short))
    return out


def lines(plan: dict, seconds: dict[str, float] | None = None) -> list[str]:
    """One line per short: name, sentences, length (measured when `seconds` has it, else estimated)
    and, for a proposal, why."""
    out = []
    for s in shorts_of(plan):
        a, b = int(s["from_sentence"]), int(s["to_sentence"])
        sec = (seconds or {}).get(s["name"])
        length = f"{sec:.1f} s" if sec is not None else f"≈{estimate(plan.get('sentences') or [], a, b):.0f} s"
        mark = "~" if s.get("_auto") == AUTO else " "
        out.append(f"{mark} {s['name']}: sentences {a}–{b}, {length}"
                   + (f" (score {s['score']}: {s['why']})" if s.get("_auto") == AUTO and s.get("why") else ""))
    return out
