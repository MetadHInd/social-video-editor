"""Charts and comparisons: `line_chart` (values over time) and `versus` (two things face to face).

Same family as the built-in templates (templates.py): titles and labels are there from the first
frame, the values come in, one thing is the point, and the picture keeps living once it has settled.
"""
from __future__ import annotations

import math
import re

from ..models import PlanError
from . import kit, motion
from .canvas import Canvas
from .icons import ICONS, draw_icon, find_icon, icon_ink
from .paths import circle, cut, poly, rect
from .scene import Scene, template
from .spec import decimals_of, format_number, number, pairs, required, split_list
from .templates import _baseline, _bob, _icon_name
from .theme import ink

_PLAIN_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    """Round values for an axis that covers lo…hi in about `count` steps (0, 25, 50, 75, 100)."""
    span = hi - lo if hi > lo else (abs(hi) or 1.0)
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw * (1 - 1e-9))
    start = math.floor(lo / step + 1e-9) * step
    end = math.ceil(hi / step - 1e-9) * step
    if end <= start:
        end = start + step
    return [start + i * step for i in range(int(round((end - start) / step)) + 1)]


def _step_decimals(step: float) -> int:
    text = f"{step:.6f}".rstrip("0").rstrip(".")
    return len(text.partition(".")[2])


def _pick(value, labels: list[str], template_name: str, default: int) -> int:
    """A highlighted item given as one of the labels or as a 1-based number."""
    if value in (None, ""):
        return default
    if str(value) in labels:
        return labels.index(str(value))
    idx = number(value)
    if idx is None or not 1 <= int(idx) <= len(labels):
        raise PlanError(f"Graphic '{template_name}': highlight '{value}' is not a label or a point number")
    return int(idx) - 1


def _title_rows(scene: Scene, size: float) -> list[str]:
    sp = scene.spec
    return kit.balanced(scene.st, str(sp["title"]), "display", size, scene.st.safe_w) if sp.get("title") else []


def _draw_title(scene: Scene, cv: Canvas, t: float, rows: list[str], x: float, y: float, size: float) -> None:
    st, z = scene.st, scene.z
    for k, row in enumerate(rows):
        kit.text(cv, st, row, x, y + k * size * 1.12, "display", size, st.theme["colors"]["paper"])
    if rows:
        uy = y + (len(rows) - 1) * size * 1.12 + z(26)
        kit.thread(cv, st, kit.squiggle(x, x + z(190), uy, z(5)), motion.ease_out(motion.progress(t, 0.05, 0.4)))


def _line(cv: Canvas, st, pts, color: str, width: float, ink_name: str | None = None, alpha: float = 1.0) -> None:
    """A thick data line in the house style: an ink outline in a printed theme, the color on top."""
    if len(pts) < 2:
        return
    path = poly(pts, closed=False)
    line = st.px(float(st.theme["stroke"]["outline"]))
    if line > 0:
        cv.stroke(path, st.theme["colors"]["ink"], width + 2 * line, alpha)
    dx, dy = kit.registration(st, ink_name)
    cv.stroke(path.moved(dx, dy) if (dx or dy) else path, color, width, alpha)


# ── line chart ───────────────────────────────────────────────────────────────
@template("line_chart")
class LineChart(Scene):
    """Values over time: the line draws itself, each point pops as the line reaches it and the
    highlighted value (default the last) is stamped above its point. One or two series.

    data="2019:12, 2021:30, 2024:75" · data2 (a second series, same labels) · series="Phones, Laptops"
    (names of the series) · title · unit · prefix · source · highlight (label or point number; default
    the last) · min · max · color
    """

    default_dur = 4.0

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        rows = pairs(required(sp, "data", "line_chart"))
        if not 2 <= len(rows) <= 12:
            raise PlanError(f"Graphic 'line_chart' takes 2 to 12 points, not {len(rows)}")
        self.labels = [label for label, _ in rows]
        if not all(self.labels):
            raise PlanError(f"Graphic 'line_chart': every point needs a label, like \"2019:12, 2024:75\" "
                            f"(data={sp.get('data')!r})")
        written = [v for _, v in rows]
        series = [[number(v) for v in written]]
        if sp.get("data2") not in (None, "", []):
            rows2 = pairs(sp["data2"])
            if [label for label, _ in rows2] != self.labels:
                raise PlanError("Graphic 'line_chart': 'data2' needs the same labels as 'data', in the same order")
            written += [v for _, v in rows2]
            series.append([number(v) for _, v in rows2])
        for s in series:
            if any(v is None for v in s):
                raise PlanError(f"Graphic 'line_chart': every value needs a number (data={sp.get('data')!r})")
        self.series = series
        self.names = split_list(sp.get("series"))
        if self.names and len(self.names) != len(series):
            raise PlanError(f"Graphic 'line_chart': 'series' names {len(self.names)} lines but the chart has {len(series)}")
        self.decimals = max(decimals_of(v) for v in written)
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.unit, self.prefix = str(sp.get("unit") or ""), str(sp.get("prefix") or "")
        self.source = str(sp.get("source") or "")
        n = len(self.labels)
        self.hi = _pick(sp.get("highlight"), self.labels, "line_chart", n - 1)
        values = [v for s in series for v in s]
        lo = number(sp.get("min")) if sp.get("min") not in (None, "") else min(0.0, min(values))
        top = number(sp.get("max")) if sp.get("max") not in (None, "") else max(values)
        if top <= lo:
            top = lo + (abs(lo) or 1.0)
        self.ticks = nice_ticks(lo, top, 4)
        self.lo, self.top = self.ticks[0], self.ticks[-1]
        self.tick_decimals = _step_decimals(self.ticks[1] - self.ticks[0]) if len(self.ticks) > 1 else 0
        # numeric labels (years) are placed by their value; any other label is evenly spaced
        nums = [float(lb) if _PLAIN_NUMBER.fullmatch(lb.strip()) else None for lb in self.labels]
        if all(v is not None for v in nums) and all(b > a for a, b in zip(nums, nums[1:])):
            self.fx = [(v - nums[0]) / (nums[-1] - nums[0]) for v in nums]
        else:
            self.fx = [i / (n - 1) for i in range(n)]

        x0, y0, x1, y1 = st.safe
        self.title_size = z(46)
        self.title_rows = _title_rows(self, self.title_size)
        self.tick_size, self.x_size, self.value_size, self.small_size = z(28), z(32), z(66), z(28)
        self.legend_size = z(32)
        tick_w = max(st.measure(self._tick(v), "label", self.tick_size) for v in self.ticks)
        self.gutter = tick_w + z(22)
        label_w = max(st.measure(lb, "label", self.x_size) for lb in self.labels)
        self.every = 1                                        # show every k-th x label when they would touch
        plot_w = st.safe_w - self.gutter
        while self.every < n and (n - 1) / self.every * (label_w + z(24)) > plot_w - label_w:
            self.every += 1
        self.pad = max(label_w / 2, z(34))                    # the first and last labels stay inside the plot
        title_h = len(self.title_rows) * self.title_size * 1.12 + z(52) if self.title_rows else 0
        legend_h = self.legend_size * 1.9 if len(series) > 1 else 0
        self.head = self.value_size * 1.75                    # room for the stamp above the highest point
        xl_h = self.x_size * 2.0
        source_h = z(64) if self.source else 0
        min_plot = z(430 if st.vertical else 300)
        fixed = title_h + legend_h + self.head + xl_h + source_h
        self.block = (st.safe_w if self.every < n else st.safe_w * 2, fixed + min_plot)
        plot_h = max(st.safe_h * self.fill - fixed, min_plot)  # the plot takes all the height that is left
        top_y = (y0 + y1) / 2 - (fixed + plot_h) / 2
        self.title_y = top_y + self.title_size * 0.9
        self.legend_y = top_y + title_h + self.legend_size * 0.6
        self.plot = (x0 + self.gutter, top_y + title_h + legend_h + self.head, plot_w, plot_h)
        self.xl_y = self.plot[1] + plot_h + self.x_size * 1.35
        self.source_y = top_y + fixed + plot_h
        px, py, pw, ph = self.plot
        self.pts = [[(px + self.pad + f * (pw - 2 * self.pad), self._y(v)) for f, v in zip(self.fx, s)] for s in series]
        # timing: the line draws at an even pace; a point pops when the line reaches it
        self.t_line = 0.3
        self.draw_dur = min(max(0.26 * (n - 1), 0.9), 1.7, self.dur * 0.42)
        self.delay = [0.0, 0.18]
        self.pops = [[self._reach(s, i) + self.delay[k] for i in range(n)] for k, s in enumerate(self.pts)]
        self.stamp = max(self.pops[0][self.hi], self.t_line + self.draw_dur) + 0.05
        self.settle = self.stamp + 0.55

    def _y(self, v: float) -> float:
        px, py, pw, ph = self.plot
        return py + ph * (1 - (v - self.lo) / (self.top - self.lo))

    def _reach(self, pts, i: int) -> float:
        """When the drawing line reaches point i (the line is drawn with ease_out)."""
        total = sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) or 1.0
        frac = sum(math.dist(a, b) for a, b in zip(pts[:i], pts[1:i + 1])) / total
        return self.t_line + self.draw_dur * (1 - (1 - min(frac, 1.0)) ** (1 / 3))

    def _tick(self, v: float) -> str:
        return format_number(v, self.st.language, self.tick_decimals)

    def _value(self, v: float) -> str:
        return self.prefix + format_number(v, self.st.language, self.decimals)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = st.safe[0]
        px, py, pw, ph = self.plot
        _draw_title(self, cv, t, self.title_rows, x0, self.title_y, self.title_size)
        colors = [self.color, c["muted"]]
        if len(self.series) > 1:                                              # legend: a swatch and a name per line
            lx = x0
            for k, name in enumerate(self.names or ["", ""]):
                if not name:
                    continue
                cy = self.legend_y
                _line(cv, st, [(lx, cy), (lx + z(44), cy)], colors[k], z(9), self.ink if k == 0 else None)
                cv.fill(circle(lx + z(22), cy, z(9)), c["paper"])
                w = kit.text(cv, st, name, lx + z(60), _baseline(cy, self.legend_size), "label", self.legend_size,
                             c["paper"] if k == 0 else c["muted"])
                lx += z(60) + w + z(46)
        for v in self.ticks:                                                  # the grid and its numbers
            y = self._y(v)
            cv.stroke(poly([(px, y), (px + pw, y)], closed=False), c["trace"], z(4), cap="butt")
            kit.text(cv, st, self._tick(v), px - z(20), _baseline(y, self.tick_size), "label", self.tick_size,
                     c["muted"], anchor="right")
        base = self._y(max(self.lo, min(0.0, self.top))) if self.lo < 0 < self.top else self._y(self.lo)
        kit.thread(cv, st, [(px, base), (px + pw, base)], alpha=0.85)
        for i, (lb, (x, _)) in enumerate(zip(self.labels, self.pts[0])):      # the time axis
            hi = i == self.hi
            if i % self.every and i != len(self.labels) - 1 and not hi:
                continue
            cv.stroke(poly([(x, base), (x, base + z(14))], closed=False), c["muted"], z(4), cap="butt")
            kit.text(cv, st, lb, x, self.xl_y, "label", self.x_size, c["paper"] if hi else c["muted"], "center")
        # the area under the main line, then the lines (the second one behind), up to where they are drawn
        drawn = [motion.ease_out(motion.progress(t, self.t_line + d, self.draw_dur)) for d in self.delay]
        main = cut(self.pts[0], drawn[0])
        if len(main) > 1:
            area = poly(main + [(main[-1][0], base), (main[0][0], base)])
            h = st.theme["halftone"]
            cv.fill(area, self.color, 0.07)
            cv.halftone(area, self.color, st.px(h["cell"]), h["angle"], st.px(float(h["radius"]) * 0.8), 0.32)
        for k in reversed(range(len(self.pts))):
            part = cut(self.pts[k], drawn[k])
            with kit.boiled(cv, st, 60 + k, t, px + pw / 2, py + ph / 2):
                _line(cv, st, part, colors[k], z(10 if k == 0 else 7), self.ink if k == 0 else None)
        for k, pts in enumerate(self.pts):                                    # the points pop as the line reaches them
            for i, (x, y) in enumerate(pts):
                p = motion.progress(t, self.pops[k][i], 0.28)
                if p <= 0:
                    continue
                main_hi = k == 0 and i == self.hi
                r = z(17 if main_hi else 12 if k == 0 else 9) * motion.stamp_scale(p, 0.3)
                kit.shape(cv, st, circle(x, y, r), self.color if main_hi else c["paper"], self.ink if main_hi else None,
                          halo=False, shake=motion.shake(t - self.stamp) if main_hi else 0.0)
                if not main_hi and len(pts) <= 7 and (k == 0 or i == len(pts) - 1):   # small value over each point
                    kit.text(cv, st, self._value(self.series[k][i]), x, y - z(30), "label", self.small_size,
                             c["paper"] if k == 0 else c["muted"], "center", p=kit.printed(t, self.pops[k][i]))
        self._highlight(cv, t, base)
        kit.source_line(cv, st, self.source, x0, self.source_y, p=kit.printed(t, 0.25), size=z(26))

    def _highlight(self, cv: Canvas, t: float, base: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        hx, hy = self.pts[0][self.hi]
        landed = motion.progress(t, self.stamp, 0.3)
        if landed > 0:                                                         # a dashed drop line down to the axis
            kit.rail(cv, st, [(hx, hy + z(24)), (hx, base - z(6))], t, color=self.color, width=z(4), alpha=0.8)
            kit.glow(cv, st, hx, hy, z(120), self.color, t)
        # the marker rides the tip of the line while it draws, then sits on the highlighted point
        drawn = motion.ease_out(motion.progress(t, self.t_line, self.draw_dur))
        tip = cut(self.pts[0], drawn)[-1]
        hop = motion.ease_in_out(motion.progress(t, self.t_line + self.draw_dur, 0.35))
        mx, my = motion.lerp(tip[0], hx, hop), motion.lerp(tip[1], hy, hop) - z(30) * math.sin(math.pi * hop)
        if t >= self.t_line:
            kit.draw_element("marker", cv, st, mx, my + _bob(self, t, t >= self.settle), t, scale=self.k)
        if landed <= 0:
            return
        txt = self._value(self.series[0][self.hi] * motion.ease_out(motion.progress(t, self.stamp, 0.4)))
        final = self._value(self.series[0][self.hi])
        size, usize = self.value_size, self.value_size * 0.55
        w = self.st.measure(final, "number", size) + (z(10) + st.measure(self.unit, "number", usize) if self.unit else 0)
        bw, bh = w + z(44), size * 1.25
        px, py, pw, ph = self.plot
        bx = min(max(hx - bw / 2, px - self.gutter * 0.4), px + pw - bw)
        by = hy - z(46) - bh
        scale = motion.stamp_scale(landed, 0.4) + 0.06 * motion.shake(t - self.stamp)
        with kit.tilted(cv, kit.box_tilt(st, 1), hx, by + bh, scale):
            kit.box(cv, st, bx, by, bw, bh)
            tip_path = poly([(hx - z(16), by + bh - z(2)), (hx + z(16), by + bh - z(2)), (hx, by + bh + z(20))])
            kit.shape(cv, st, tip_path, c["paper"], halo=False)
            cv.fill(rect(hx - z(14), by + bh - z(6), z(28), z(6)), c["paper"])
            vw = kit.text(cv, st, txt, bx + z(22), _baseline(by + bh / 2, size), "number", size, c["ink"])
            if self.unit:
                kit.text(cv, st, self.unit, bx + z(22) + vw + z(10), _baseline(by + bh / 2, size), "number", usize,
                         c["ink"])
        kit.burst(cv, st, hx, by + bh / 2, bw / 2 + z(12), bh * 0.42, z(20), self.color, landed, t, rays=3)
        kit.sparkles(cv, st, (bx, by, bw, bh), t, seed=13, size=z(18), appear=landed)

    def sounds(self):
        return [("whoosh", 0.0), ("pop", self.stamp)]

    def lines(self):
        return [x for x in (" ".join(self.title_rows), *self.names, self.labels[0], self.labels[-1], self.source) if x]


# ── versus ───────────────────────────────────────────────────────────────────
_STAR = "*"


def _side_value(text: str) -> tuple[str, bool]:
    """'40*' → ('40', True): a star marks the winner of a row."""
    text = text.strip()
    return (text[:-1].strip(), True) if text.endswith(_STAR) else (text, False)


def versus_rows(value) -> list[tuple[str, str, str, int | None]]:
    """Rows of a comparison: "Speed:10|40, Price:5*|9" → [(label, left, right, winner)], where winner
    is 0 (left), 1 (right) or None (a tie, or text that says nothing about which is better). A star
    after a value marks it as the better one; without stars the higher number wins."""
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        text = str(value or "")
        items = text.split(";") if ";" in text else text.split(",")
    out = []
    for item in (i.strip() for i in items):
        if not item:
            continue
        label, sep, vals = item.rpartition(":") if "|" in item.rpartition(":")[2] else item.partition(":")
        left, bar, right = vals.partition("|")
        if not sep or not bar:
            raise PlanError(f"Graphic 'versus': row '{item}' must be \"Label:left|right\" (e.g. \"Speed:10|40\")")
        (a, star_a), (b, star_b) = _side_value(left), _side_value(right)
        if star_a or star_b:
            winner = None if star_a and star_b else 0 if star_a else 1
        else:
            na, nb = number(a), number(b)
            winner = None if na is None or nb is None or na == nb else 0 if na > nb else 1
        out.append((label.strip(), a, b, winner))
    return out


@template("versus")
class Versus(Scene):
    """Two things face to face: their names (an illustration when the name is known) on each side of a
    VS stamp, then one row at a time with both figures as bars that meet in the middle; the better side
    of each row gets the color and a check, and the side that wins more rows is crowned at the end.

    left="Phone" · right="Laptop" · rows="Battery:20 h|9 h, Price:900|1200*" (a star marks the better
    value; without one the higher number wins; text values only win with a star) · icons="phone, laptop"
    ("-" = none) · title · source · color
    """

    default_dur = 5.0

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.left, self.right = str(required(sp, "left", "versus")), str(required(sp, "right", "versus"))
        self.rows = versus_rows(required(sp, "rows", "versus"))
        if not 1 <= len(self.rows) <= 5:
            raise PlanError(f"Graphic 'versus' takes 1 to 5 rows, not {len(self.rows)}")
        asked = split_list(sp.get("icons"))
        if asked and len(asked) != 2:
            raise PlanError(f"Graphic 'versus': 'icons' needs two entries (left, right), not {len(asked)}")
        self.icons = [None if a == "-" else _icon_name(a) for a in asked] if asked else \
            [find_icon(self.left), find_icon(self.right)]
        for a, found in zip(asked, self.icons):
            if a != "-" and found is None:
                raise PlanError(f"Graphic 'versus': no illustration called '{a}' ({', '.join(sorted(ICONS))})")
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.source = str(sp.get("source") or "")
        wins = [sum(1 for r in self.rows if r[3] == side) for side in (0, 1)]
        self.wins = wins
        self.champion = None if wins[0] == wins[1] else 0 if wins[0] > wins[1] else 1
        self.nums = [(number(a), number(b)) for _, a, b, _ in self.rows]
        self.bars = [na is not None and nb is not None and max(na, nb) > 0 for na, nb in self.nums]

        x0, y0, x1, y1 = st.safe
        cx = (x0 + x1) / 2
        self.cx = cx
        self.title_size = z(46)
        self.title_rows = _title_rows(self, self.title_size)
        self.name_size, self.label_size, self.value_size = z(42), z(34), z(56)
        self.icon_size = z(190 if st.vertical else 140) if any(self.icons) else 0.0
        self.vs_r = z(58)
        half = st.safe_w / 2
        name_w = max(st.measure(self.left, "label", self.name_size), st.measure(self.right, "label", self.name_size))
        name_w += self.name_size
        self.side_x = [cx - min(half * 0.5, half - self.vs_r * 0.4), cx + min(half * 0.5, half - self.vs_r * 0.4)]
        title_h = len(self.title_rows) * self.title_size * 1.12 + z(56) if self.title_rows else 0
        head_h = self.icon_size + z(20) + self.name_size * 1.7 + z(40)
        self.bar_h = z(48 if st.vertical else 40)
        self.on_top = st.vertical                                  # vertical: the figures on the label's line
        self.line_h = self.value_size * 1.3 if self.on_top else self.label_size * 1.5
        row_h = self.line_h + self.bar_h
        gap = z(46 if st.vertical else 26)
        source_h = z(64) if self.source else 0
        n = len(self.rows)
        height = title_h + head_h + n * row_h + (n - 1) * gap + source_h
        value_w = max([st.measure(self._shown(v, number(v), 1.0), "number", self.value_size)
                       for _, a, b, _ in self.rows for v in (a, b)] + [0.0])
        label_w = max(st.measure(r[0], "label", self.label_size) for r in self.rows)
        self.gap_mid = z(10)
        self.mark_r = z(26)
        if self.on_top:
            self.track = half - self.gap_mid
            room = 2 * (value_w + z(14) + 2 * self.mark_r) + label_w + z(30)
        else:                                                      # room for the figure and the check outside
            self.track = half - self.gap_mid - value_w - z(28) - 2 * self.mark_r - z(14)
            room = label_w
        wide_enough = self.track > half * 0.25 and name_w < half * 1.05 and room < st.safe_w
        self.block = (st.safe_w if wide_enough else st.safe_w * 2, height)
        spare = max(st.safe_h * self.fill - height, 0)
        gap += min(spare / max(n + 1, 1), z(90))                   # spread the rows over the height that is left
        head_h += min(spare / max(n + 1, 1), z(60))
        height = title_h + head_h + n * row_h + (n - 1) * gap + source_h
        top = (y0 + y1) / 2 - height / 2
        self.title_y = top + self.title_size * 0.9
        self.head_y = top + title_h                                # top of the two names
        self.icon_y = self.head_y + self.icon_size / 2
        self.name_y = self.head_y + self.icon_size + z(20) + self.name_size * 0.85
        self.vs_y = self.head_y + (self.icon_size if self.icon_size else self.name_size * 1.7) * 0.5 + z(8)
        self.rows_y = [top + title_h + head_h + i * (row_h + gap) for i in range(n)]
        self.source_y = top + height
        self.value_w = value_w
        step = min(max((self.dur - 1.9) / n, 0.32), 0.75)
        self.starts = [0.55 + i * step for i in range(n)]
        self.grow = 0.5
        self.crown = self.starts[-1] + self.grow + 0.25
        self.settle = self.crown + 0.5

    def _shown(self, val: str, num: float | None, p: float) -> str:
        """A figure as shown: a plain number counts up and is written for the language; text as is."""
        if num is None or not _PLAIN_NUMBER.fullmatch(val.strip()):
            return val
        return format_number(num * motion.ease_out(p), self.st.language, decimals_of(val))

    def _side(self, cv: Canvas, t: float, side: int) -> None:
        st, z = self.st, self.z
        x = self.side_x[side]
        name = (self.left, self.right)[side]
        champion = self.champion == side and t >= self.crown
        p = motion.progress(t, side * 0.08, 0.36)
        if champion:
            kit.glow(cv, st, x, self.icon_y if self.icon_size else self.name_y, z(170), self.color, t)
        icon = self.icons[side]
        if icon:
            kit.ground(cv, st, x, self.icon_y + self.icon_size * 0.5, self.icon_size * 0.8)
            with kit.boiled(cv, st, 70 + side, t, x, self.icon_y):
                draw_icon(cv, st, icon, x, self.icon_y, self.icon_size * motion.stamp_scale(p, 0.6),
                          icon_ink(st, icon)[0], t, active=motion.shake(t - self.crown) if champion else 0.0)
        kit.tag(cv, st, name, x, self.name_y - self.name_size * 0.3, self.name_size, anchor="center", index=side,
                fill=self.color if champion else None)
        if champion:
            w = st.measure(name, "label", self.name_size) + self.name_size
            kit.sparkles(cv, st, (x - w / 2, self.name_y - self.name_size * 1.2, w, self.name_size * 1.6), t, seed=17,
                         size=z(20), appear=motion.progress(t, self.crown, 0.3))
            score = f"{self.wins[side]}–{self.wins[1 - side]}"
            sp = motion.progress(t, self.crown, 0.3)
            sy = self.name_y + self.name_size * 0.95
            kit.tag(cv, st, score, x, sy, self.label_size * 0.9, anchor="center", p=sp, index=side + 1)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = st.safe[0]
        _draw_title(self, cv, t, self.title_rows, x0, self.title_y, self.title_size)
        for side in (0, 1):
            self._side(cv, t, side)
        # the VS stamp rocks gently between them
        vp = motion.progress(t, 0.15, 0.36)
        if vp > 0:
            rock = 4.0 * (motion.wave(t, 0.1) - 0.5) if st.theme["motion"].get("ambient", True) else 0.0
            with kit.tilted(cv, -6 + rock, self.cx, self.vs_y, motion.stamp_scale(vp, 0.4) + 0.08 * motion.shake(t - 0.5)):
                kit.badge(cv, st, self.cx, self.vs_y, self.vs_r, self.color)
                kit.text(cv, st, "VS", self.cx, _baseline(self.vs_y, self.vs_r * 0.95), "number", self.vs_r * 0.95,
                         c["ink"], "center")
        half = st.safe_w / 2
        for i, ((label, a, b, winner), (na, nb), bars, y, start) in enumerate(
                zip(self.rows, self.nums, self.bars, self.rows_y, self.starts)):
            ly = y + self.line_h / 2 if self.on_top else y + self.label_size * 0.45
            kit.text(cv, st, label, self.cx, _baseline(ly, self.label_size), "label", self.label_size,
                     c["paper"] if t >= start else c["muted"], "center")
            by = y + self.line_h
            cy = by + self.bar_h / 2
            p = motion.progress(t, start, self.grow)
            done = t >= start + self.grow
            for side, (val, num) in enumerate(((a, na), (b, nb))):
                sign = -1 if side == 0 else 1
                inner = self.cx + sign * self.gap_mid
                won = winner == side
                if bars:                                                # an empty track, then the bar grows outwards
                    tx = inner if side == 1 else inner - self.track
                    kit.shape(cv, st, rect(tx, by, self.track, self.bar_h), c["trace"], halo=False, with_shadow=True)
                    full = self.track * num / max(na, nb)
                    cur = max(full * motion.action(p), z(6)) if p > 0 else 0.0
                    if cur > 0:
                        bx = inner if side == 1 else inner - cur
                        with kit.boiled(cv, st, 80 + 2 * i + side, t, inner, cy):
                            kit.shape(cv, st, rect(bx, by, cur, self.bar_h), self.color if won and done else c["muted"],
                                      self.ink if won and done else None, halo=False,
                                      shake=motion.shake(t - start - self.grow) if won else 0.0)
                            kit.shade(cv, st, rect(bx, by + self.bar_h * 0.62, cur, self.bar_h * 0.38), 0.3)
                    if self.on_top:
                        vx, vy = (st.safe[0], st.safe[2])[side], _baseline(ly, self.value_size)
                    else:
                        vx, vy = inner + sign * (self.track + z(22)), _baseline(cy, self.value_size)
                    shown = self._shown(val, num, p)
                    if p > 0:
                        kit.text(cv, st, shown, vx, vy, "number", self.value_size,
                                 self.color if won and done else c["paper"],
                                 ("left" if side == 0 else "right") if self.on_top else ("right" if side == 0 else "left"),
                                 p=kit.printed(t, start))
                    vw = st.measure(self._shown(val, num, 1.0), "number", self.value_size)
                    out = -sign if self.on_top else sign             # the check goes after the figure
                    mark_x, mark_y = vx + out * (vw + z(12) + self.mark_r), (ly if self.on_top else cy)
                else:                                                   # words: a tag on each side
                    vx = self.cx + sign * half * 0.5
                    if p > 0:
                        kit.tag(cv, st, val, vx, cy, self.label_size, anchor="center", p=p, index=i + side,
                                fill=self.color if won and done else None)
                    mark_x = vx + sign * (st.measure(val, "label", self.label_size) / 2 + self.label_size * 0.9)
                    mark_y = cy
                if won and done:
                    mp = motion.ease_out(motion.progress(t, start + self.grow, 0.25))
                    kit.badge(cv, st, mark_x, mark_y, self.mark_r, c["paper"], scale=motion.stamp_scale(mp, 0.4))
                    kit.draw_element("check", cv, st, mark_x, mark_y, t, color=c.get("good", self.color), p=mp, size=z(30))
        if self.champion is not None and t >= self.crown:              # the marker sits on the winner's name
            x = self.side_x[self.champion]
            w = st.measure((self.left, self.right)[self.champion], "label", self.name_size) + self.name_size
            hop = motion.ease_out(motion.progress(t, self.crown, 0.3))
            kit.draw_element("marker", cv, st, x + w / 2, self.name_y - self.name_size * 1.25 - z(18) * (1 - hop)
                             + _bob(self, t, hop >= 1), t, scale=self.k)
        kit.source_line(cv, st, self.source, self.cx, self.source_y, "center", p=kit.printed(t, 0.3), size=z(26))

    def sounds(self):
        return [("whoosh", 0.0), ("pop", 0.5)] + [("pop", s + self.grow) for s, r in zip(self.starts, self.rows)
                                                  if r[3] is not None]

    def lines(self):
        return [x for x in (" ".join(self.title_rows), self.left, self.right, *(r[0] for r in self.rows), self.source) if x]


__all__ = ["LineChart", "Versus", "nice_ticks", "versus_rows"]
