"""Drawing helpers every template shares, so all graphics look like one family.

What they draw depends on the theme: with the default theme shapes are flat; a theme with an ink
outline, a halftone shadow, registration offsets and "printed" motion gives a risograph look
(outlined shapes whose color prints slightly off, dotted shadows, text that prints from two color
ghosts). Sizes are given for a 1080 frame and scaled by the stage.
"""
from __future__ import annotations

import math
from contextlib import contextmanager

from . import motion
from .canvas import Canvas, saved
from .paths import Path, Point, circle, cut, dashes, oval, poly, rect, round_rect, smooth_points
from .scene import ELEMENTS, Stage, element
from .theme import ink as resolve_ink

GHOST_A = (12.0, -7.0)        # where the two color ghosts of a printing text start (px at 1080)
GHOST_B = (-10.0, 8.0)
EXTRUDE_STEP = (0.9, 1.0)     # each extrusion step of a poster moves this much
POSTER_LINE = 1.06            # line height of a poster, in letter sizes


# ── stage ────────────────────────────────────────────────────────────────────
def backdrop(cv: Canvas, st: Stage) -> None:
    """The stage: flat background and a soft grid."""
    c = st.theme["colors"]
    cv.fill(rect(0, 0, st.W, st.H), c["bg"])
    cell = st.px(float(st.theme["grid"].get("cell", 0)))
    if cell <= 0:
        return
    width = st.px(float(st.theme["grid"].get("width", 2)))
    x = (st.W % cell) / 2
    while x < st.W:
        cv.stroke(poly([(x, 0), (x, st.H)], closed=False), c["grid"], width, cap="butt")
        x += cell
    y = (st.H % cell) / 2
    while y < st.H:
        cv.stroke(poly([(0, y), (st.W, y)], closed=False), c["grid"], width, cap="butt")
        y += cell


# ── shapes ───────────────────────────────────────────────────────────────────
def registration(st: Stage, ink_name: str | None, shake: float = 0.0) -> Point:
    """How far the fill of an ink prints from its outline (grows with an impact's `shake`)."""
    off = st.theme["registration"].get(ink_name) if ink_name else None
    if not off:
        return 0.0, 0.0
    k = st.u * (1 + 2.2 * shake)
    return off[0] * k, off[1] * k


def shadow(cv: Canvas, st: Stage, path: Path, alpha: float = 1.0) -> None:
    """A dotted shadow of the shape, down and to the right (nothing when the theme has none)."""
    h = st.theme["halftone"]
    off = st.px(float(h.get("shadow_offset", 0)))
    if off > 0:
        cv.halftone(path.moved(off, off), st.theme["colors"]["shadow"], st.px(h["cell"]), h["angle"],
                    st.px(h["radius"]), alpha)


def shape(cv: Canvas, st: Stage, path: Path, fill: str | None, ink_name: str | None = None, outline: bool = True,
          halo: bool = True, alpha: float = 1.0, shake: float = 0.0, with_shadow: bool = False) -> None:
    """A flat shape in the house style: shadow, light halo, fill (off-register for an ink), ink outline."""
    c, s = st.theme["colors"], st.theme["stroke"]
    line = st.px(float(s["outline"])) if outline else 0.0
    if with_shadow:
        shadow(cv, st, path, alpha)
    if halo and line > 0 and float(s["halo"]) > 0:
        cv.stroke(path, c["paper"], line + 2 * st.px(float(s["halo"])), alpha)
    if fill:
        dx, dy = registration(st, ink_name, shake)
        cv.fill(path.moved(dx, dy) if (dx or dy) else path, fill, alpha)
    if line > 0:
        cv.stroke(path, c["ink"], line, alpha)


@contextmanager
def boiled(cv: Canvas, st: Stage, seed: int, t: float, cx: float, cy: float):
    """Draws what is inside with the stroke wobble of shape `seed` (when the theme has it on)."""
    with saved(cv):
        if st.theme["motion"].get("boil"):
            dx, dy, deg = motion.boil(seed, t, st.px(1.2))
            cv.translate(dx, dy)
            cv.rotate(deg, cx, cy)
        yield


@contextmanager
def tilted(cv: Canvas, degrees: float, cx: float, cy: float, scale: float = 1.0):
    with saved(cv):
        if degrees:
            cv.rotate(degrees, cx, cy)
        if scale != 1.0:
            cv.scale(scale, scale, cx, cy)
        yield


def box(cv: Canvas, st: Stage, x: float, y: float, w: float, h: float, fill: str | None = None,
        alpha: float = 1.0) -> None:
    """A text box: paper, outlined, with a dotted shadow."""
    path = round_rect(x, y, w, h, st.px(float(st.theme["box"].get("radius", 0))))
    shape(cv, st, path, fill or st.theme["colors"]["paper"], halo=False, alpha=alpha, with_shadow=True)


def box_tilt(st: Stage, index: int = 0) -> float:
    tilts = st.theme["box"].get("tilts") or [0.0]
    return float(tilts[index % len(tilts)])


# ── text ─────────────────────────────────────────────────────────────────────
def fit_size(st: Stage, text: str, font: str, size: float, max_w: float, min_size: float = 0.0) -> float:
    """`size` or the largest smaller size at which the line fits in `max_w`."""
    w = st.measure(text, font, size)
    return size if w <= max_w or w <= 0 else max(size * max_w / w, min_size)


def wrap(st: Stage, text: str, font: str, size: float, max_w: float) -> list[str]:
    """Greedy word wrap; "|" forces a line break."""
    out = []
    for chunk in str(text).split("|"):
        cur = ""
        for word in chunk.split():
            test = f"{cur} {word}".strip()
            if cur and st.measure(test, font, size) > max_w:
                out.append(cur)
                cur = word
            else:
                cur = test
        out.append(cur)
    return [line for line in out if line] or [""]


def balanced(st: Stage, text: str, font: str, size: float, max_w: float) -> list[str]:
    """Word wrap in as few lines as `wrap`, but with lines of similar length (for titles).
    A text with "|" keeps its own line breaks."""
    lines = wrap(st, text, font, size, max_w)
    if "|" in str(text) or len(lines) < 2:
        return lines
    words, n = str(text).split(), len(lines)
    best, best_w = lines, max(st.measure(line, font, size) for line in lines)

    def split(start: int, left: int, acc: list[str]):
        nonlocal best, best_w
        if left == 1:
            rows = acc + [" ".join(words[start:])]
            widest = max(st.measure(r, font, size) for r in rows)
            if widest < best_w:
                best, best_w = rows, widest
            return
        for end in range(start + 1, len(words) - left + 2):
            split(end, left - 1, acc + [" ".join(words[start:end])])

    if len(words) <= 14:
        split(0, n, [])
    return best


def text(cv: Canvas, st: Stage, txt: str, x: float, y: float, font: str, size: float, color: str,
         anchor: str = "left", alpha: float = 1.0, p: float | None = None) -> float:
    """One line at baseline (x, y), anchored left / center / right. `p` (0-1) is how far it has
    appeared: it prints from two color ghosts in a printed theme, fades in otherwise. → its width."""
    w = st.measure(txt, font, size)
    x0 = x - (w if anchor == "right" else w / 2 if anchor == "center" else 0)
    if p is not None and p <= 0:
        return w
    if p is None or p >= 1:
        cv.text(txt, x0, y, font, size, color, alpha)
        return w
    if st.theme["motion"].get("print_in"):
        c, k = st.theme["colors"], (1 - motion.ease_out(p)) * st.u
        cv.text(txt, x0 + GHOST_A[0] * k, y + GHOST_A[1] * k, font, size, c["ghost_a"], alpha * 0.85)
        cv.text(txt, x0 + GHOST_B[0] * k, y + GHOST_B[1] * k, font, size, c["ghost_b"], alpha * 0.85)
        cv.text(txt, x0, y, font, size, color, alpha * motion.ease_in_out(p))
    else:
        cv.text(txt, x0, y, font, size, color, alpha * motion.ease_out(p))
    return w


def printed(t: float, start: float) -> float:
    """Progress of a text that starts printing at `start`."""
    return motion.progress(t, start, motion.PRINT_SECONDS)


def poster(cv: Canvas, st: Stage, lines: list[str], cx: float, cy: float, size: float, ink_name: str | None = None,
           scale: float = 1.0, shake: float = 0.0, tilt: float | None = None) -> None:
    """A big title, centered on (cx, cy): paper letters; in a printed theme with an ink border and an
    extrusion in the ink's color."""
    c, po = st.theme["colors"], st.theme["poster"]
    color, name = resolve_ink(st.theme, ink_name)
    lh = size * POSTER_LINE
    paths = []
    for i, line in enumerate(lines):
        w = st.measure(line, "display", size)
        paths.append(cv.text_path(line, cx - w / 2, cy - lh * (len(lines) - 1) / 2 + i * lh + size * 0.36,
                                  "display", size))
    border, steps = st.px(float(po.get("outline", 0))), int(po.get("extrude", 0))
    with tilted(cv, float(po.get("tilt", 0)) if tilt is None else tilt, cx, cy, scale):
        if steps > 0:
            sx, sy = EXTRUDE_STEP[0] * st.u * size / 150, EXTRUDE_STEP[1] * st.u * size / 150
            rx, ry = registration(st, name, shake)
            for k in range(steps, 0, -1):
                for p in paths:
                    cv.stroke(p.moved(sx * k, sy * k), c["ink"], border or st.px(8))
            for k in range(steps, 0, -1):
                for p in paths:
                    cv.fill(p.moved(sx * k + rx * 0.5, sy * k + ry * 0.5), color)
        if border > 0:
            for p in paths:
                cv.stroke(p, c["ink"], border)
        for p in paths:
            cv.fill(p, c["paper"])


def source_line(cv: Canvas, st: Stage, txt: str, x: float, y: float, anchor: str = "left", p: float | None = None,
                size: float | None = None) -> None:
    """Where a figure comes from: small and quiet. `size` in pixels."""
    if txt:
        text(cv, st, txt, x, y, "label", size or st.px(27), st.theme["colors"]["muted"], anchor, p=p)


# ── connectors ───────────────────────────────────────────────────────────────
def thread(cv: Canvas, st: Stage, pts: list[Point], p: float = 1.0, alpha: float = 1.0) -> list[Point]:
    """A connector line along `pts`, drawn up to fraction `p` of its length. → the part drawn."""
    part = cut(pts, p)
    if len(part) < 2:
        return part
    c, s = st.theme["colors"], st.theme["stroke"]
    path, w, halo = poly(part, closed=False), st.px(float(s["thread"])), st.px(float(s.get("thread_halo", 0)))
    if halo > 0:
        cv.stroke(path, c["paper"], w + 2 * halo, alpha * 0.9)
        cv.stroke(path, c["ink"], w, alpha)
    else:
        cv.stroke(path, c["muted"], w, alpha)
    return part


def arrow_head(cv: Canvas, st: Stage, x: float, y: float, degrees: float, size: float | None = None) -> None:
    """An arrow tip at (x, y) pointing along `degrees`. `size` in pixels."""
    s = size or st.px(26)
    tip = poly([(0, 0), (-s, -s * 0.55), (-s * 0.78, 0), (-s, s * 0.55)])
    with saved(cv):
        cv.translate(x, y)
        cv.rotate(degrees)
        printed_look = float(st.theme["stroke"].get("thread_halo", 0)) > 0
        c = st.theme["colors"]
        if printed_look:
            cv.stroke(tip, c["ink"], st.px(float(st.theme["stroke"]["thread"])))
            cv.fill(tip, c["paper"])
        else:
            cv.fill(tip, c["muted"])


def draw_element(name: str, cv: Canvas, st: Stage, x: float, y: float, t: float = 0.0, **options) -> None:
    ELEMENTS[name](cv, st, x, y, t, **options)


@element("marker")
def marker(cv: Canvas, st: Stage, x: float, y: float, t: float = 0.0, color: str | None = None,
           scale: float = 1.0, **_) -> None:
    """The dot that rides the tip of a bar and travels along diagram arrows. Packs replace it with
    their own character."""
    c = st.theme["colors"]
    r = st.px(15) * scale
    cv.fill(circle(x, y, r + st.px(5)), c["bg"])
    cv.fill(circle(x, y, r), color or c["paper"])


@element("check")
def check(cv: Canvas, st: Stage, x: float, y: float, t: float = 0.0, color: str | None = None,
          size: float | None = None, p: float = 1.0, **_) -> None:
    """A check mark centered on (x, y), `size` pixels wide, drawn up to fraction `p`."""
    size = size or st.px(44)
    s = size / 2
    pts = cut([(x - s, y), (x - s * 0.3, y + s * 0.68), (x + s, y - s * 0.7)], p)
    if len(pts) > 1:
        _mark(cv, st, pts, color or st.theme["colors"]["accent"], size)


@element("cross")
def cross(cv: Canvas, st: Stage, x: float, y: float, t: float = 0.0, color: str | None = None,
          size: float | None = None, p: float = 1.0, **_) -> None:
    size = size or st.px(44)
    s = size / 2 * 0.82
    col = color or st.theme["colors"]["alert"]
    a = cut([(x - s, y - s), (x + s, y + s)], min(p * 2, 1))
    b = cut([(x + s, y - s), (x - s, y + s)], max(p * 2 - 1, 0))
    for pts in (a, b):
        if len(pts) > 1:
            _mark(cv, st, pts, col, size)


def _mark(cv: Canvas, st: Stage, pts: list[Point], color: str, size: float) -> None:
    path, w = poly(pts, closed=False), size * 0.2
    if float(st.theme["stroke"]["outline"]) > 0:
        cv.stroke(path, st.theme["colors"]["ink"], w + st.px(float(st.theme["stroke"]["outline"])) * 1.4)
    cv.stroke(path, color, w)


# ── scenery and life ─────────────────────────────────────────────────────────
# Every graphic sits in a place, not on a blank page: the inside of a machine. Circuit traces run in
# from the edges, data pulses travel along them, small chips blink and loose bits twinkle. All of it
# is low contrast and stays out of the safe zone, so it never competes with the subject.
_SCENERY: dict = {}


def _traces(st: Stage, seed: int) -> dict:
    """Where the scenery goes for this frame size (computed once): traces as polylines along the
    grid in the bands outside the safe zone, chips at some of their ends, loose bits."""
    key = (st.W, st.H, seed, repr(st.theme["decor"]), st.theme["grid"].get("cell"))
    if key in _SCENERY:
        return _SCENERY[key]
    d = st.theme["decor"]
    cell = st.px(float(st.theme["grid"].get("cell") or 96))
    ox, oy = (st.W % cell) / 2, (st.H % cell) / 2
    x0, y0, x1, y1 = st.safe
    rows_top = [oy + k * cell for k in range(int(st.H / cell) + 1) if oy + k * cell < y0 - cell * 0.4]
    rows_bottom = [oy + k * cell for k in range(int(st.H / cell) + 1) if oy + k * cell > y1 + cell * 0.4]
    rows = rows_top + rows_bottom
    traces, chips = [], []
    n = int(d.get("traces", 0)) if rows else 0
    for i in range(n):
        r = motion.noise(seed * 3.1 + i * 17.3)
        y = rows[int(motion.noise(seed + i * 5.7) * len(rows)) % len(rows)]
        from_left = i % 2 == 0
        run = ox + cell * (1 + int(r * 3))             # turns on a grid line
        step = cell * (1 if motion.noise(seed + i * 2.9) > 0.5 else -1)
        if not (0 < y + step < st.H) or (y0 - cell * 0.4 <= y + step <= y1 + cell * 0.4):
            step = -step
        tail = cell * (1 + int(motion.noise(seed + i * 9.1) * 2))
        if from_left:
            pts = [(0, y), (run, y), (run + abs(step), y + step), (run + abs(step) + tail, y + step)]
        else:
            pts = [(st.W, y), (st.W - run, y), (st.W - run - abs(step), y + step), (st.W - run - abs(step) - tail, y + step)]
        traces.append(pts)
        if len(chips) < int(d.get("chips", 0)) and i % 3 == 1:
            chips.append((pts[-1], from_left))
    bits = []
    for i in range(int(d.get("bits", 0))):
        bx = motion.noise(seed * 1.7 + i * 13.1) * st.W
        band = rows_top if i % 2 == 0 and rows_top else rows_bottom or rows_top
        if not band:
            break
        lo, hi = (0, y0 - cell * 0.3) if band is rows_top else (y1 + cell * 0.3, st.H)
        bits.append((bx, lo + motion.noise(seed * 2.3 + i * 7.9) * (hi - lo), motion.noise(i * 3.3 + seed)))
    if len(_SCENERY) > 24:
        _SCENERY.clear()
    _SCENERY[key] = {"traces": traces, "chips": chips, "bits": bits}
    return _SCENERY[key]


def scenery(cv: Canvas, st: Stage, t: float = 0.0, seed: int = 0) -> None:
    """The stage with its scenery: background, grid, circuit traces with travelling pulses, chips
    with a blinking light and twinkling bits."""
    backdrop(cv, st)
    c = st.theme["colors"]
    lay = _traces(st, seed)
    alive = st.theme["motion"].get("ambient", True)
    accent, _ = resolve_ink(st.theme, None)
    w = st.px(4)
    for i, pts in enumerate(lay["traces"]):
        cv.stroke(poly(pts, closed=False), c["trace"], w)
        ex, ey = pts[-1]
        cv.fill(circle(ex, ey, st.px(9)), c["trace"])
        cv.fill(circle(ex, ey, st.px(4)), c["bg"])
        if alive and i % 2 == 0:                    # a pulse of data runs along every other trace
            p = motion.loop(t, i * 0.37)
            for k, alpha in ((0, 0.55), (1, 0.28), (2, 0.14)):       # copies instead of blur
                q = p - k * 0.035
                if 0 < q < 1:
                    px, py = cut(pts, q)[-1]
                    cv.fill(circle(px, py, st.px(5.5 - k)), accent, alpha)
    for (cx, cy), from_left in lay["chips"]:
        s = st.px(34)
        body = round_rect(cx - s, cy - s * 0.7, 2 * s, 1.4 * s, st.px(6))
        for k in range(4):                           # pins
            px = cx - s * 0.66 + k * s * 0.44
            cv.stroke(poly([(px, cy - s * 0.7), (px, cy - s * 0.95)], closed=False), c["trace"], st.px(4), cap="butt")
            cv.stroke(poly([(px, cy + s * 0.7), (px, cy + s * 0.95)], closed=False), c["trace"], st.px(4), cap="butt")
        cv.fill(body, c["trace"])
        cv.fill(round_rect(cx - s * 0.6, cy - s * 0.36, s * 1.2, s * 0.72, st.px(4)), c["bg"])
        on = not alive or motion.loop(t, 0.2 if from_left else 0.7) < 0.5
        cv.fill(circle(cx + s * 0.66, cy - s * 0.42, st.px(4)), accent, 0.7 if on else 0.15)
    for bx, by, r in lay["bits"]:
        tw = motion.wave(t, r, 2) if alive else 0.5
        s = st.px(5 + 5 * r)
        cv.fill(rect(bx - s / 2, by - s / 2, s, s), c["paper"] if r > 0.6 else accent, 0.07 + 0.2 * tw)


def glow(cv: Canvas, st: Stage, cx: float, cy: float, r: float, color: str | None = None, t: float = 0.0) -> None:
    """Printed light behind a subject: flat concentric circles and a halftone that opens outwards
    (no blur, no gradient). It breathes slowly."""
    if not st.theme["decor"].get("glow", True):
        return
    col = color or resolve_ink(st.theme, None)[0]
    r *= 1 + 0.03 * (motion.wave(t) if st.theme["motion"].get("ambient", True) else 0)
    h = st.theme["halftone"]
    for f, dot in ((1.0, 1.3), (0.8, 2.0), (0.6, 2.8)):
        cv.halftone(circle(cx, cy, r * f), col, st.px(h["cell"] * 1.3), 15, st.px(dot), 0.2)
    for f in (0.62, 0.42):
        cv.fill(circle(cx, cy, r * f), col, 0.06)


def star_path(cx: float, cy: float, r: float, waist: float = 0.26) -> Path:
    """A four-point sparkle."""
    w = r * waist
    return poly([(cx, cy - r), (cx + w, cy - w), (cx + r, cy), (cx + w, cy + w),
                 (cx, cy + r), (cx - w, cy + w), (cx - r, cy), (cx - w, cy - w)])


def sparkle(cv: Canvas, st: Stage, x: float, y: float, r: float, t: float = 0.0, seed: float = 0.0) -> None:
    """A four-point sparkle in paper with a thin outline; it twinkles."""
    alive = st.theme["motion"].get("ambient", True)
    k = 0.55 + 0.45 * (motion.wave(t, seed, 2) if alive else 1.0)
    path = star_path(x, y, r * k)
    line = st.px(float(st.theme["stroke"]["outline"])) * 0.55
    if line > 0:
        cv.stroke(path, st.theme["colors"]["ink"], line * 2)
    cv.fill(path, st.theme["colors"]["paper"])


def sparkles(cv: Canvas, st: Stage, box: tuple[float, float, float, float], t: float = 0.0, seed: int = 0,
             size: float | None = None, appear: float = 1.0) -> None:
    """A few sparkles around a box (x, y, w, h): just outside its corners and sides."""
    n = int(st.theme["decor"].get("sparkles", 0))
    if n <= 0 or appear <= 0:
        return
    x, y, w, h = box
    size = size or st.px(26)
    spots = [(-0.07, 0.1), (1.07, 0.02), (1.05, 0.9), (-0.08, 0.84), (1.1, 0.45), (-0.11, 0.48)]
    for i, (fx, fy) in enumerate(spots[:n]):
        r = size * (0.55 + 0.75 * motion.noise(seed + i * 4.7)) * motion.clamp01(appear * 1.4 - i * 0.1)
        if r > 1:
            sparkle(cv, st, x + fx * w + (motion.noise(seed + i) - 0.5) * size, y + fy * h, r, t, seed=i * 0.23)


def tag(cv: Canvas, st: Stage, txt: str, x: float, y: float, size: float, anchor: str = "left", tilt: float | None = None,
        p: float | None = None, fill: str | None = None, color: str | None = None, index: int = 0) -> tuple[float, float]:
    """A small paper label with its text: (x, y) is the left / center / right of its middle line.
    → (width, height)."""
    c = st.theme["colors"]
    pad_x, pad_y = size * 0.5, size * 0.3
    w, h = st.measure(txt, "label", size) + 2 * pad_x, size + 2 * pad_y
    x0 = x - (w if anchor == "right" else w / 2 if anchor == "center" else 0)
    grow = 1.0 if p is None else motion.ease_out(motion.clamp01(p * 1.6))
    if grow <= 0:
        return w, h
    with tilted(cv, box_tilt(st, index) if tilt is None else tilt, x0 + w / 2, y, 0.7 + 0.3 * grow):
        box(cv, st, x0, y - h / 2, w, h, fill=fill)
        text(cv, st, txt, x0 + pad_x, y + size * 0.35, "label", size, color or c["ink"], p=p)
    return w, h


def badge(cv: Canvas, st: Stage, cx: float, cy: float, r: float, fill: str | None = None, scale: float = 1.0) -> None:
    """A round paper stamp (for a check, a number, a symbol) with outline and dotted shadow."""
    path = circle(cx, cy, r * scale)
    shape(cv, st, path, fill or st.theme["colors"]["paper"], halo=False, with_shadow=True)


def rail(cv: Canvas, st: Stage, pts: list[Point], t: float = 0.0, color: str | None = None, width: float | None = None,
         alpha: float = 1.0) -> None:
    """Dashes that march along a line (energy, flow, a connection in use)."""
    dash, gap = st.px(16), st.px(14)
    alive = st.theme["motion"].get("ambient", True)
    off = -(motion.loop(t) * 4 * (dash + gap)) if alive else 0.0
    for d in dashes(pts, dash, gap, off):
        cv.stroke(poly(d, closed=False), color or resolve_ink(st.theme, None)[0], width or st.px(5), alpha, cap="butt")


def ground(cv: Canvas, st: Stage, cx: float, y: float, w: float) -> None:
    """The dotted shadow an object casts on the floor under it."""
    h = st.theme["halftone"]
    cv.halftone(oval(cx, y, w / 2, w * 0.09), st.theme["colors"]["shadow"], st.px(h["cell"]), h["angle"],
                st.px(float(h["radius"]) + 0.6), 0.9)


def shade(cv: Canvas, st: Stage, path: Path, alpha: float = 0.35) -> None:
    """Dotted shading over part of a shape (its darker side)."""
    h = st.theme["halftone"]
    cv.halftone(path, st.theme["colors"]["ink"], st.px(h["cell"]), h["angle"], st.px(float(h["radius"]) * 0.85), alpha)


def squiggle(x0: float, x1: float, y: float, amp: float, waves: int = 3) -> list[Point]:
    """A hand-drawn wavy underline from x0 to x1, as points to draw with thread()."""
    n = max(waves * 2, 2)
    pts = [(x0 + (x1 - x0) * i / n, y + (amp if i % 2 else -amp) * (0.5 if i in (0, n) else 1.0)) for i in range(n + 1)]
    return smooth_points(pts, per=8)


def burst(cv: Canvas, st: Stage, cx: float, cy: float, rx: float, ry: float, length: float, color: str,
          appear: float = 1.0, t: float = 0.0, spread: float = 46.0, rays: int = 5) -> None:
    """Short rays to the left and right of a subject (an impact): they pop out and keep pulsing."""
    if appear <= 0:
        return
    alive = st.theme["motion"].get("ambient", True)
    w = st.px(7)
    line = st.px(float(st.theme["stroke"]["outline"]))
    for side in (0.0, 180.0):
        for i in range(rays):
            a = math.radians(side - spread + 2 * spread * i / (rays - 1))
            pulse = 0.82 + 0.18 * (motion.wave(t, i * 0.21 + side / 360, 2) if alive else 1.0)
            ln = length * (0.6 + 0.4 * (i % 2)) * motion.clamp01(appear) * pulse
            ax, ay = cx + rx * math.cos(a), cy + ry * math.sin(a)
            bx, by = cx + (rx + ln) * math.cos(a), cy + (ry + ln * 0.8) * math.sin(a)
            ray = poly([(ax, ay), (bx, by)], closed=False)
            if line > 0:
                cv.stroke(ray, st.theme["colors"]["ink"], w + line * 1.2)
            cv.stroke(ray, color, w)


def split_lines(st: Stage, text: str, font: str, size: float, n: int) -> list[str] | None:
    """The text in exactly `n` lines of similar length (None when it has fewer words than lines)."""
    words = str(text).split()
    if len(words) < n:
        return None
    if n == 1:
        return [" ".join(words)]
    best: list[str] | None = None
    best_w = float("inf")

    def split(start: int, left: int, acc: list[str]):
        nonlocal best, best_w
        if left == 1:
            rows = acc + [" ".join(words[start:])]
            widest = max(st.measure(r, font, size) for r in rows)
            if widest < best_w:
                best, best_w = rows, widest
            return
        for end in range(start + 1, len(words) - left + 2):
            split(end, left - 1, acc + [" ".join(words[start:end])])

    if len(words) <= 14:
        split(0, n, [])
    return best


def caption(cv: Canvas, st: Stage, txt: str, t: float = 0.0, index: int = 0) -> None:
    """The narration box: what is being said, on a paper box low in the frame (below the safe zone),
    so a graphic and its subtitle are composed together."""
    c = st.theme["colors"]
    size = st.px(58 if st.vertical else 46)
    pad = size * 0.62
    max_w = st.W * (0.76 if st.vertical else 0.6) - 2 * pad
    rows = wrap(st, txt, "label", size, max_w)[:2]
    lh = size * 1.24
    w = max(st.measure(r, "label", size) for r in rows) + 2 * pad
    h = len(rows) * lh + 2 * pad - size * 0.28
    if st.vertical:
        x, y = st.px(72), st.H * 0.792
    else:
        x, y = (st.W - w) / 2, st.H * 0.975 - h
    with tilted(cv, box_tilt(st, index), x, y + h / 2):
        box(cv, st, x, y, w, h)
        for k, row in enumerate(rows):
            text(cv, st, row, x + pad, y + pad + k * lh + size * 0.72, "label", size, c["ink"])
