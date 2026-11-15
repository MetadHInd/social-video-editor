"""Which of the newer graphics a sentence asks for, and its ready spec — so the engine can choose them on
its own (the director calls these; nobody has to write the cue).

Each helper takes what the director has — the words of a sentence as (word, start second) pairs, or
data it already pulled out — and returns a graphic spec (with "at": the second it belongs to, when it
comes from words) or None when the graphic does not fit:

    line_chart_from_words(words)  figures tied to years / months / quarters, three or more, in order
    line_chart_spec(points)       (label, value) pairs whose labels are ordered times
    versus_from_words(words)      two named things set against each other (vs, contra, frente a…) with paired figures
    versus_spec(left, right, rows)
    code_from_words(words)        a command or code said in the sentence (`in backticks`, or a known command)
    code_spec(text)
    screen_spec(image, size)      a screenshot file → a framed screenshot zooming to where to look
    flow_from_words(words)        things that branch ("a B o a C") or loop back ("y vuelve a A")
    diagram_flow_spec(nodes, edges)
"""
from __future__ import annotations

import re

from .icons import find_icon
from .screens import KEYWORDS, code_lines

Words = list[tuple[str, float]]

_YEAR = re.compile(r"^(1[89]\d\d|20\d\d)$")
_NUMBER = re.compile(r"^[-+]?\d+(?:[.,]\d+)?%?$")
_STRIP = "¿?¡!.,;:«»\"'()…"
MONTHS = ["enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
          "january february march april may june july august september october november december".split(),
          "ene feb mar abr may jun jul ago sep oct nov dic".split(),
          "jan feb mar apr may jun jul aug sep oct nov dec".split()]
QUARTER = re.compile(r"^(?:q|t)([1-4])(?:\s*[-/ ]?\s*(\d{2,4}))?$", re.I)
ISO_MONTH = re.compile(r"^(\d{4})[-/](\d{1,2})$")
UNIT_SKIP = set("de del en el la los las y a al in the of and to millones millón million millions mil".split())
VERSUS = ("vs", "vs.", "versus", "contra", "against", "frente")             # "frente a", "comparado con", "compared to"
VERSUS_TWO = (("comparado", "con"), ("comparada", "con"), ("compared", "to"), ("compared", "with"))
NAME_STOP = set("el la los las un una unos unas y o de del que es son tiene tienen dura duran pesa pesan cuesta cuestan "
                "gasta gastan con en por para mientras pero a al the a an and or of is are has have lasts weighs costs "
                "uses with in for while but to".split())
COMMAND_ARGS = set("install uninstall run build init clone status push pull update upgrade add commit start test "
                   "list ls doctor serve deploy".split())
CODE_STOP = set("en y para que o in and to on or that then luego después despues".split())
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")
SCREENSHOT_WORDS = ("screenshot", "screen shot", "captura", "pantallazo", "screen", "pantalla")
ALTERNATIVE = set("o u or".split())
POSSESSIVE = set("tu tus su sus mi mis your my his her their our nuestro nuestra".split())   # "tu celular" is not "you"
RETURN = set("vuelve regresa devuelve responde back returns answers".split())


def _clean(word: str) -> str:
    return str(word).strip(_STRIP)


def _value(word: str) -> float | None:
    """The number a word says ("20", "0,2", "2.5", "14.900", "40%"); None for a year or a non-number."""
    w = _clean(word).rstrip("%")
    if not _NUMBER.match(w) or _YEAR.match(w):
        return None
    sep = "," if "," in w else "." if "." in w else ""
    if sep and len(w.rpartition(sep)[2]) == 3 and w.lstrip("+-").partition(sep)[0] not in ("", "0"):
        return float(w.replace(sep, ""))                    # 14.900 / 14,900: a thousands separator
    return float(w.replace(",", "."))


def time_rank(label: str) -> tuple | None:
    """Where a label falls in time (a year, a month name, a quarter, "2024-03"), to check the points are
    in order; None for a label that is not a time."""
    text = str(label).strip().lower().rstrip(".")
    if _YEAR.match(text):
        return (int(text), 0)
    m = ISO_MONTH.match(text)
    if m:
        return (int(m[1]), int(m[2]))
    m = QUARTER.match(text)
    if m:
        return (int(m[2]) if m[2] else 0, int(m[1]) * 3)
    parts = text.split()
    for names in MONTHS:
        if parts and parts[0] in names:
            year = int(parts[1]) if len(parts) > 1 and _YEAR.match(parts[1]) else 0
            return (year, names.index(parts[0]) + 1)
    return None


# ── line chart ───────────────────────────────────────────────────────────────
def line_chart_spec(points, title: str | None = None, unit: str | None = None, prefix: str | None = None,
                    source: str | None = None, min_points: int = 3) -> dict | None:
    """(label, value) pairs whose labels are times in order (years, months, quarters) → a line_chart
    spec; None when there are too few points, a label is not a time, the times are not in order or a
    value is not a number."""
    points = list(points)
    if not min_points <= len(points) <= 12:
        return None
    ranks = [time_rank(lb) for lb, _ in points]
    if any(r is None for r in ranks) or any(b <= a for a, b in zip(ranks, ranks[1:])):
        return None
    values = []
    for _, v in points:
        num = v if isinstance(v, (int, float)) and not isinstance(v, bool) else _value(str(v))
        if num is None:
            return None
        values.append(float(num))
    spec = {"template": "line_chart", "data": ", ".join(f"{str(lb).strip()}:{v:g}" for (lb, _), v in zip(points, values))}
    for key, val in (("title", title), ("unit", unit), ("prefix", prefix), ("source", source)):
        if val:
            spec[key] = val
    return spec


def line_chart_from_words(words: Words, min_points: int = 3) -> dict | None:
    """A sentence that gives a figure for each of several years ("12 millones en 2015, 30 en 2018 y 75
    en 2024") → a line_chart spec at the first figure; None otherwise. Each year takes the nearest
    figure in its clause that no other year took."""
    clean = [_clean(w) for w, _ in words]
    years = [i for i, w in enumerate(clean) if _YEAR.match(w)]
    if len(years) < min_points:
        return None
    figures = {i: _value(w) for i, (w, _) in enumerate(words) if _value(w) is not None}
    used, points, first = set(), [], None
    for y in years:
        best = None
        for i in figures:
            if i in used:
                continue
            lo, hi = min(i, y), max(i, y)
            if any(words[k][0].rstrip().endswith((",", ";", ".", ":")) for k in range(lo, hi)):
                continue                                    # not across a clause
            if best is None or abs(i - y) < abs(best - y) or (abs(i - y) == abs(best - y) and i < y):
                best = i
        if best is None:
            return None
        used.add(best)
        points.append((clean[y], figures[best]))
        first = best if first is None else min(first, best)
    unit = None
    for i in sorted(used):
        nxt = i + 1
        if nxt < len(clean) and clean[nxt] and clean[nxt].lower() not in UNIT_SKIP and not _YEAR.match(clean[nxt]) \
                and _value(clean[nxt]) is None:
            unit = clean[nxt]
            break
    if words[min(used)][0].strip().endswith("%") or "%" in {clean[i + 1] for i in used if i + 1 < len(clean)}:
        unit = "%"
    spec = line_chart_spec(points, unit=unit, min_points=min_points)
    if spec is not None:
        spec["at"] = words[first][1]
    return spec


# ── versus ───────────────────────────────────────────────────────────────────
def versus_spec(left: str, right: str, rows, title: str | None = None, source: str | None = None,
                lower_better=()) -> dict | None:
    """Two named things and their paired figures, rows of (label, left value, right value) → a versus
    spec; `lower_better` names the rows where less wins (price, weight, time). None when a name is
    missing or the same, or there is no row."""
    left, right = str(left or "").strip(), str(right or "").strip()
    rows = [(str(a).strip(), str(b).strip(), str(c).strip()) for a, b, c in rows]
    if not left or not right or left.lower() == right.lower() or not 1 <= len(rows) <= 5:
        return None
    out = []
    for label, a, b in rows:
        if not a or not b:
            return None
        if label in lower_better and _value(a) is not None and _value(b) is not None and _value(a) != _value(b):
            a, b = (a + "*", b) if _value(a) < _value(b) else (a, b + "*")
        out.append(f"{label}:{a}|{b}")
    spec = {"template": "versus", "left": left, "right": right, "rows": "; ".join(out)}
    if title:
        spec["title"] = title
    if source:
        spec["source"] = source
    return spec


def _name(clean: list[str], begin: int, end: int, reverse: bool = False) -> str:
    """The name of a thing among words begin…end: the content words next to the marker (up to two)."""
    idx = range(end - 1, begin - 1, -1) if reverse else range(begin, end)
    picked = []
    for i in idx:
        w = clean[i]
        if not w or w.lower() in NAME_STOP or _value(w) is not None:
            if picked:
                break
            continue
        picked.append(w)
        if len(picked) == 2:
            break
    if reverse:
        picked.reverse()
    return " ".join(picked)


def versus_from_words(words: Words) -> dict | None:
    """A sentence that sets two things against each other with figures for both ("el celular dura 20
    horas frente a 9 horas del portátil", "phone: 20 hours vs laptop: 9 hours") → a versus spec at the
    marker; None otherwise. Figures before the marker are the left thing's, after it the right one's,
    paired in order; a row is named by its unit."""
    clean = [_clean(w) for w, _ in words]
    low = [w.lower() for w in clean]
    mark, width = None, 1
    for i, w in enumerate(low):
        if w in VERSUS:
            mark, width = i, (2 if w == "frente" and i + 1 < len(low) and low[i + 1] in ("a", "al") else 1)
            break
        if i + 1 < len(low) and (w, low[i + 1]) in VERSUS_TWO:
            mark, width = i, 2
            break
    if mark is None:
        return None
    left_figs = [i for i in range(mark) if _value(clean[i]) is not None]
    right_figs = [i for i in range(mark + width, len(clean)) if _value(clean[i]) is not None]
    if not left_figs or len(left_figs) != len(right_figs):
        return None
    left = _name(clean, 0, left_figs[0]) or _name(clean, 0, mark, reverse=True)
    right = _name(clean, mark + width, right_figs[0]) or _name(clean, right_figs[-1] + 1, len(clean))
    rows = []
    for a, b in zip(left_figs, right_figs):
        unit = clean[a + 1] if a + 1 < len(clean) and clean[a + 1].lower() not in UNIT_SKIP | NAME_STOP else ""
        unit_b = clean[b + 1] if b + 1 < len(clean) else ""
        if unit and unit_b and unit_b.lower() not in UNIT_SKIP | NAME_STOP and unit_b.lower() != unit.lower():
            return None                                     # different units: not the same measure
        rows.append((unit[:1].upper() + unit[1:] if unit else f"{len(rows) + 1}", clean[a], clean[b]))
    if right.lower() in {r[0].lower() for r in rows}:
        right = _name(clean, right_figs[-1] + 2, len(clean))
    spec = versus_spec(left, right, rows)
    if spec is not None:
        spec["at"] = words[mark][1]
    return spec


# ── code ─────────────────────────────────────────────────────────────────────
def code_spec(text: str, lang: str | None = None, output=None, title: str | None = None) -> dict | None:
    """A command or a few lines of code → a code spec (a terminal for commands, an editor otherwise);
    None when the text does not look like code."""
    lines = [line for line in code_lines(text) if line.strip()]
    if not lines or not all(looks_like_code(line) for line in lines):
        return None
    if lang is None:
        lang = "" if all(_command(line) for line in lines) else _guess_lang("\n".join(lines))
    spec = {"template": "code", "lines": lines if any(";" in line for line in lines) else "; ".join(lines)}
    if lang:
        spec["lang"] = lang
    if output:
        spec["output"] = output
    if title:
        spec["title"] = title
    return spec


def _command(line: str) -> bool:
    first = line.strip().lstrip("$> ").split(" ")[0].lower() if line.strip() else ""
    return first in KEYWORDS and first not in _LANG_WORDS


_LANG_WORDS = set("def class return import from function const let var if for while print".split())


def looks_like_code(line: str) -> bool:
    """A command ("brew install ffmpeg", "$ ls") or a line of code ("def f(x):", "x = 2;", "print(x)")."""
    text = line.strip()
    if not text:
        return False
    if text.startswith(("$ ", "> ", ">>> ")):
        return True
    words = text.split()
    if _command(text) and (len(words) == 1 or words[1].lower() in COMMAND_ARGS or words[1].startswith("-")
                           or any(ch in words[1] for ch in "./-_=:")):
        return True
    return bool(re.search(r"[(){}\[\];=]|=>|::|\b(def|function|class|return|import)\b", text)) and \
        not re.search(r"[.!?¿¡]\s+[A-ZÁÉÍÓÚ]", text)


def _guess_lang(text: str) -> str:
    if re.search(r"\bdef\b|\bimport\b.*$|print\(|:\s*$", text, re.M):
        return "python"
    if re.search(r"\bfunction\b|=>|\bconst\b|\blet\b|console\.", text):
        return "js"
    return "code"


def code_from_words(words: Words) -> dict | None:
    """A command said in a sentence ("escribe `brew install ffmpeg` en la terminal", "run npm run build")
    → a code spec at the command; None otherwise. Text between backticks wins; else a known command
    followed by its arguments, up to a word like "en", "y", "to"."""
    text = " ".join(w for w, _ in words)
    ticks = re.findall(r"`([^`]+)`", text)
    if ticks:
        spec = code_spec("; ".join(t.strip() for t in ticks))
        if spec is not None:
            at = next((t for w, t in words if "`" in w), words[0][1] if words else 0.0)
            spec["at"] = at
        return spec
    clean = [_clean(w) for w, _ in words]
    for i, w in enumerate(clean):
        if w.lower() in KEYWORDS and w.lower() not in _LANG_WORDS and w.islower() and i + 1 < len(clean):
            end = i + 1
            while end < len(clean) and clean[end] and clean[end].lower() not in CODE_STOP and \
                    not words[end - 1][0].rstrip().endswith((",", ".", ";", ":")):
                end += 1
            command = " ".join(clean[i:end])
            if end - i >= 2 and looks_like_code(command):
                spec = code_spec(command, lang="")
                if spec is not None:
                    spec["at"] = words[i][1]
                    return spec
    return None


# ── screen ───────────────────────────────────────────────────────────────────
def is_screenshot(name: str) -> bool:
    """A file name that says it is a screenshot ("Screenshot 2026-…", "Captura de pantalla…")."""
    low = str(name).lower().replace("_", " ").replace("-", " ")
    return any(w in low for w in SCREENSHOT_WORDS)


def screen_spec(image: str, size: tuple[int, int] | None = None, focus: tuple[float, float] | None = None,
                label: str | None = None, title: str | None = None, zoom: float | None = None) -> dict | None:
    """An image file (path relative to the project, like a photo) → a screen spec: a phone frame for a
    tall picture, a browser for a wide one; `focus` (fractions) is where to look. None for a file that
    is not an image."""
    if not str(image).lower().endswith(IMAGE_EXT):
        return None
    spec = {"template": "screen", "image": str(image)}
    if size and size[1]:
        spec["frame"] = "phone" if size[0] / size[1] < 0.8 else "browser"
    if focus:
        spec["focus_x"], spec["focus_y"] = round(min(max(focus[0], 0.0), 1.0), 3), round(min(max(focus[1], 0.0), 1.0), 3)
    if zoom:
        spec["zoom"] = zoom
    if label:
        spec["label"] = label
    if title:
        spec["title"] = title
    return spec


# ── diagram flow ─────────────────────────────────────────────────────────────
def diagram_flow_spec(nodes, edges, title: str | None = None) -> dict | None:
    """Things and arrows (from, to, label) that a chain cannot show — a thing with two arrows out to
    things after it, or an arrow back — → a diagram spec in the flow layout; None for a plain chain
    (the chain is the director's usual diagram), an unknown thing or too many things."""
    nodes = [str(n).strip() for n in nodes]
    if not 2 <= len(nodes) <= 6 or len(set(nodes)) != len(nodes):
        return None
    edges = [(str(a).strip(), str(b).strip(), str(lb or "").strip()) for a, b, *rest in edges for lb in (rest[:1] or [""])]
    if not edges or any(a not in nodes or b not in nodes or a == b for a, b, _ in edges):
        return None
    pos = {n: i for i, n in enumerate(nodes)}
    ahead: dict[str, set[str]] = {}
    for a, b, _ in edges:
        if pos[b] > pos[a]:
            ahead.setdefault(a, set()).add(b)
    branches = any(len(v) > 1 for v in ahead.values())
    loops = any(pos[b] < pos[a] for a, b, _ in edges)
    if not (branches or loops):
        return None
    spec = {"template": "diagram", "nodes": ", ".join(nodes), "layout": "flow",
            "edges": "; ".join(f"{a}>{b}" + (f":{lb}" if lb else "") for a, b, lb in edges)}
    if title:
        spec["title"] = title
    return spec


def flow_from_words(words: Words) -> dict | None:
    """A sentence where things branch or loop: the things are the words that have an illustration
    (phone, server, cloud…), in the order said. "A … a B o a C" (or/o/u between two things) is a
    branch from the thing before them; a thing said again after "vuelve/regresa/back" is a loop back.
    → a diagram spec in the flow layout at the first thing; None when nothing branches or loops."""
    clean = [_clean(w) for w, _ in words]
    seen: list[tuple[int, str, str]] = []                 # (word index, illustration, name as said)
    for i, w in enumerate(clean):
        icon = find_icon(w) if w and w.lower() not in NAME_STOP | POSSESSIVE else None
        if icon:
            seen.append((i, icon, w[:1].upper() + w[1:]))
    if len(seen) < 3:
        return None
    nodes: list[str] = []
    by_icon: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    prev = None                                            # the thing the next arrow leaves from
    source_of_branch = None
    for k, (i, icon, name) in enumerate(seen):
        if icon in by_icon:                                # said again: an arrow back to it
            back = any(clean[j].lower() in RETURN for j in range(seen[k - 1][0] + 1, i)) if k else False
            if prev is not None and back and by_icon[icon] != prev:
                edges.append((prev, by_icon[icon], ""))
            prev = by_icon[icon]
            continue
        by_icon[icon] = name
        nodes.append(name)
        between = {clean[j].lower() for j in range(seen[k - 1][0] + 1, i)} if k else set()
        if k and between & ALTERNATIVE and source_of_branch is not None:
            edges.append((source_of_branch, name, ""))     # "… a B o a C": C is the other way from where B came
        elif prev is not None:
            source_of_branch = prev
            edges.append((prev, name, ""))
        prev = name
    spec = diagram_flow_spec(nodes, edges)
    if spec is not None:
        spec["at"] = words[seen[0][0]][1]
    return spec


__all__ = ["code_from_words", "code_spec", "diagram_flow_spec", "flow_from_words", "is_screenshot", "line_chart_from_words",
           "line_chart_spec", "looks_like_code", "screen_spec", "time_rank", "versus_from_words", "versus_spec"]

