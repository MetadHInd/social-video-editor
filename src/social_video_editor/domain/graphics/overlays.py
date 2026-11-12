"""Graphics drawn OVER the picture (transparent background), for a presenter who stays on screen:

    callouts    small illustrated notes that pop up around the presenter as things are named
    bullets     short lines of text stacked at one side, one by one
    pip_frame   the paper frame of the presenter's window when a graphic takes the rest of the frame

They keep their size (no scaling to fill the safe zone: the presenter is the subject) and each item
appears at its own time (`times`), so a note shows up when its word is said.
"""
from __future__ import annotations

from ..models import PlanError
from . import kit, motion
from .canvas import Canvas, saved
from .icons import ICONS, draw_icon, find_icon
from .paths import rect, round_rect
from .scene import Scene, template
from .spec import number, required, split_list
from .theme import ink

POP = 0.3              # seconds an item takes to pop in
STAGGER = 0.6          # default delay between items without `times`
CLOSE = 0.2            # seconds everything takes to leave at the end
YES = {"1", "true", "yes", "y", "si", "sí", "on"}


def truthy(value) -> bool:
    return value is True or str(value).strip().lower() in YES


def _pop(p: float) -> float:
    """0 → 1 with a small overshoot (something stuck on the picture)."""
    q = motion.clamp01(p) - 1
    return 1 + 2.2 * q ** 3 + 1.2 * q ** 2


def item_times(spec: dict, n: int, dur: float, name: str) -> list[float]:
    """When each of the n items appears (seconds from the start): `times`, or one every STAGGER seconds.
    Times never go back and always leave the item a moment on screen."""
    given = spec.get("times")
    values = split_list(given) if not isinstance(given, (int, float)) else [given]
    out: list[float] = []
    for i in range(n):
        v = number(values[i]) if i < len(values) else None
        if i < len(values) and v is None:
            raise PlanError(f"Graphic '{name}': times must be seconds, not {values[i]!r}")
        t = v if v is not None else (out[-1] + STAGGER if out else 0.15)
        out.append(max(t, out[-1] if out else 0.0, 0.0))
    last = max(dur - CLOSE - POP - 0.2, 0.0)
    return [min(t, last) for t in out]


def _icon(label: str, asked: str | None) -> str | None:
    if asked is not None:
        name = asked.strip().lower()
        return None if name in ("", "-", "none") else (name if name in ICONS else find_icon(name))
    return find_icon(label)


# ── callouts ─────────────────────────────────────────────────────────────────
@template("callouts")
class Callouts(Scene):
    """Notes that pop up around the presenter: an illustration on a round paper stamp with its label,
    a figure on the accent color, or a word on paper underlined by hand.

    items="Phone; Memory; 2 min" · times="0.4, 1.6, 3" · icons="phone, ram, -" (default: by label) ·
    link=true (in a row, joined by arrows: a small diagram that builds up) · color
    """

    overlay = True
    default_dur = 4.0
    scales = (1.0,)
    SLOTS_VERTICAL = ((0.22, 0.14), (0.72, 0.18), (0.22, 0.48), (0.71, 0.53), (0.25, 0.655), (0.70, 0.69))
    SLOTS_LANDSCAPE = ((0.15, 0.25), (0.85, 0.29), (0.14, 0.55), (0.86, 0.59), (0.16, 0.80), (0.84, 0.82))
    ROW_Y = (0.585, 0.74)          # link=true: height of the row (vertical, landscape)

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.items = split_list(required(sp, "items", "callouts"))
        self.link = truthy(sp.get("link"))
        most = 4 if self.link else 6
        if not 1 <= len(self.items) <= most:
            raise PlanError(f"Graphic 'callouts' takes 1 to {most} items, not {len(self.items)}")
        n = len(self.items)
        asked = split_list(sp.get("icons")) if sp.get("icons") is not None else None
        self.icons = [_icon(label, asked[i] if asked and i < len(asked) else (None if asked is None else "-"))
                      for i, label in enumerate(self.items)]
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.times = item_times(sp, n, self.dur, "callouts")
        up = 1.0 if st.vertical else 0.92
        self.r = z(112) * up
        if self.link:                                          # a row: stamps shrink to leave room for the arrows
            self.r = min(self.r, st.safe_w / (n * 3.1))
        max_w = st.W * (0.32 if st.vertical else 0.20) if not self.link else self.r * 2.7
        self.sizes = [kit.fit_size(st, label, "label", (z(40) if icon else z(72)) * up, max_w * (1 if icon else 1.2), z(24) * up)
                      for label, icon in zip(self.items, self.icons)]
        if self.link:
            x0, x1 = st.safe[0] + self.r * 1.3, st.safe[2] - self.r * 1.3
            y = st.H * self.ROW_Y[0 if st.vertical else 1]
            self.at = [((x0 + x1) / 2 if n == 1 else x0 + (x1 - x0) * i / (n - 1), y) for i in range(n)]
        else:
            slots = self.SLOTS_VERTICAL if st.vertical else self.SLOTS_LANDSCAPE
            self.at = [(st.W * fx, st.H * fy) for fx, fy in slots[:n]]
        self.settle = self.times[-1] + POP + 0.15
        self.exit_at = max(self.dur - CLOSE, self.settle)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        out = 1 - motion.ease_in_out(motion.progress(t, self.exit_at, CLOSE))
        if self.link:
            for i in range(1, len(self.items)):
                p = motion.ease_out(motion.progress(t, self.times[i] - 0.05, 0.35)) * (1 if out >= 1 else 0)
                (ax, ay), (bx, by) = self.at[i - 1], self.at[i]
                pts = [(ax + self.r * 1.12, ay), (bx - self.r * 1.25, by)]
                if p > 0 and pts[1][0] > pts[0][0]:
                    part = kit.thread(cv, st, pts, p)
                    if p >= 1:
                        kit.arrow_head(cv, st, part[-1][0] + z(14), part[-1][1], 0, z(20))
        for i, (label, name, (cx, cy), size, start) in enumerate(zip(self.items, self.icons, self.at, self.sizes, self.times)):
            if t < start:
                continue
            p = motion.progress(t, start, POP)
            scale = _pop(p) * out
            if scale <= 0.02:
                continue
            cy += z(5) * (2 * motion.wave(t, i * 0.23) - 1) if p >= 1 else 0.0       # it floats
            tilt = kit.box_tilt(st, i) * (0.6 if i % 2 else -0.6)
            with kit.boiled(cv, st, 70 + i, t, cx, cy), kit.tilted(cv, tilt, cx, cy, scale):
                printed = kit.printed(t, start + 0.1)
                if name:
                    kit.badge(cv, st, cx, cy, self.r, c["paper"])
                    draw_icon(cv, st, name, cx, cy, self.r * 1.32, t=t, active=motion.wave(t, i * 0.31))
                    kit.tag(cv, st, label, cx, cy + self.r + size * 0.95, size, "center", p=printed, index=i)
                elif label[:1].isdigit():                       # a figure: on the accent color
                    kit.tag(cv, st, label, cx, cy, size, "center", p=printed, fill=self.color, index=i)
                else:                                           # a word: on paper, underlined by hand
                    w, h = kit.tag(cv, st, label, cx, cy, size, "center", p=printed, index=i)
                    kit.thread(cv, st, kit.squiggle(cx - w * 0.42, cx + w * 0.42, cy + h * 0.78, z(5)),
                               motion.ease_out(motion.progress(t, start + 0.15, 0.3)))
                if p >= 1:
                    reach = self.r if name else size * 1.5
                    kit.sparkle(cv, st, cx + reach * 0.95, cy - reach * 0.8, z(20), t, seed=i * 0.37)

    def sounds(self):
        return [("pop", s) for s in self.times]

    def lines(self):
        return list(self.items)


# ── bullets ──────────────────────────────────────────────────────────────────
@template("bullets")
class Bullets(Scene):
    """Short lines stacked at the side of the presenter, each on its paper strip, one by one.

    items="No extra battery; Back in an instant" · times="0.5, 2.4" · color
    """

    overlay = True
    default_dur = 4.5
    scales = (1.0,)
    TOP = (0.455, 0.30)            # where the stack starts (share of the height: vertical, landscape)

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.items = split_list(required(sp, "items", "bullets"))
        if not 1 <= len(self.items) <= 4:
            raise PlanError(f"Graphic 'bullets' takes 1 to 4 items, not {len(self.items)}")
        self.color, _ = ink(st.theme, sp.get("color"))
        self.times = item_times(sp, len(self.items), self.dur, "bullets")
        up = 1.2 if st.vertical else 1.0
        self.size = z(46) * up
        self.strip, self.pad_x, self.pad_y = z(14) * up, z(26) * up, z(19) * up
        max_w = st.W * (0.66 if st.vertical else 0.36)
        self.rows = [kit.wrap(st, item, "label", self.size, max_w)[:2] for item in self.items]
        self.lh = self.size * 1.16
        self.widths = [max(st.measure(r, "label", self.size) for r in rows) + self.strip + 2 * self.pad_x for rows in self.rows]
        self.heights = [len(rows) * self.lh + 2 * self.pad_y - z(6) for rows in self.rows]
        self.x = st.safe[0]
        y = st.H * self.TOP[0 if st.vertical else 1]
        self.ys = []
        for h in self.heights:
            self.ys.append(y)
            y += h + z(26) * up
        self.settle = self.times[-1] + 0.2 + motion.PRINT_SECONDS + 0.15
        self.exit_at = max(self.dur - CLOSE, self.settle)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        out = 1 - motion.ease_in_out(motion.progress(t, self.exit_at, CLOSE))
        newest = max((i for i, s in enumerate(self.times) if t >= s + 0.2), default=None)
        for i, (rows, y, w, h, start) in enumerate(zip(self.rows, self.ys, self.widths, self.heights, self.times)):
            if t < start:
                continue
            grown = w * motion.ease_out(motion.progress(t, start, 0.2)) * out
            if grown < 2:
                continue
            with kit.boiled(cv, st, 90 + i, t, self.x, y + h / 2), kit.tilted(cv, kit.box_tilt(st, i) * 0.6, self.x, y + h / 2):
                kit.box(cv, st, self.x, y, grown, h)
                cv.fill(rect(self.x, y, min(self.strip, grown), h), self.color)
                with saved(cv):
                    cv.clip(rect(self.x, y, grown, h))
                    for k, row in enumerate(rows):
                        kit.text(cv, st, row, self.x + self.strip + self.pad_x, y + self.pad_y + k * self.lh + self.size * 0.8,
                                 "label", self.size, c["ink"], p=kit.printed(t, start + 0.12))
                if i == newest and out >= 1:                    # the marker sits on the line being said
                    bob = z(3) * (2 * motion.wave(t, 0.0, 2) - 1)
                    kit.draw_element("marker", cv, st, self.x + w - z(34), y - z(10) + bob, t, scale=1.1)

    def sounds(self):
        return [("pop", s) for s in self.times]

    def lines(self):
        return list(self.items)


# ── the presenter's window ───────────────────────────────────────────────────
@template("pip_frame")
class PipFrame(Scene):
    """The frame around the presenter's window (picture-in-picture): a paper border with tape, on a
    transparent background. The engine puts the presenter's picture inside `rect` and this on top.

    rect="x, y, w, h" (shares of the frame) · label (a small tag on the frame) · color
    """

    overlay = True
    default_dur = 3.0
    scales = (1.0,)
    BORDER = 18             # paper border, px at 1080

    def layout(self) -> None:
        st, sp = self.st, self.spec
        values = [number(v) for v in split_list(required(sp, "rect", "pip_frame"))]
        if len(values) != 4 or any(v is None for v in values) or not (values[2] > 0 and values[3] > 0):
            raise PlanError(f"Graphic 'pip_frame': rect must be \"x, y, w, h\" (shares of the frame), not {sp.get('rect')!r}")
        self.x, self.y, self.w, self.h = values[0] * st.W, values[1] * st.H, values[2] * st.W, values[3] * st.H
        self.label = str(sp.get("label") or "")
        self.color, _ = ink(st.theme, sp.get("color"))
        self.b = st.px(self.BORDER)
        self.settle = 0.1

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, b = self.st, self.st.theme["colors"], self.b
        x, y, w, h = self.x, self.y, self.w, self.h
        line = max(st.px(float(st.theme["stroke"]["outline"])), st.px(3))
        depth = st.px(10)
        with kit.boiled(cv, st, 120, t, x + w / 2, y + h / 2):
            # the card's thickness: an ink edge down and to the right, outside the window
            cv.fill(rect(x + w + b, y + depth, depth, h + b), c["ink"])
            cv.fill(rect(x + depth, y + h + b, w + b, depth), c["ink"])
            cv.stroke(round_rect(x - b / 2, y - b / 2, w + b, h + b, b), c["paper"], b, join="miter")
            cv.stroke(round_rect(x - b, y - b, w + 2 * b, h + 2 * b, b * 1.5), c["ink"], line)
            cv.stroke(round_rect(x, y, w, h, b / 2), c["ink"], line)
        tape_w, tape_h = st.px(110), st.px(34)
        for k, (tx, deg) in enumerate(((x + w * 0.16, -7.0), (x + w * 0.84, 6.0))):
            with kit.boiled(cv, st, 121 + k, t, tx, y - b), saved(cv):
                cv.rotate(deg, tx, y - b)
                cv.fill(rect(tx - tape_w / 2, y - b - tape_h / 2, tape_w, tape_h), self.color, 0.9)
        if self.label:
            kit.tag(cv, st, self.label, x + st.px(18), y + h + b * 0.5, st.px(30), index=1)
        bob = st.px(3) * (2 * motion.wave(t, 0.0, 2) - 1)
        kit.draw_element("marker", cv, st, x + w - st.px(48), y - b - st.px(12) + bob, t, scale=1.15)

    def sounds(self):
        return []
