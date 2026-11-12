"""The built-in graphics: bars, number, product_card, title, headline, list, lower_third, diagram.

Each template reads a small spec (see docs/GRAPHICS.md), lays itself out for the frame (vertical or
landscape) as large as fits in the safe zone and draws any instant.

Every graphic has three layers on top of the scenery: the subject (what the sentence is about),
context around it (tracks behind bars, tags, tape, a second sheet, sparkles) and ambient life that
never stops (the marker bobs, lights blink, dashes march). The first frame is never empty: titles
and labels are there from the start and the values come in.

Sizes are written for a 1080 frame through self.z(), which applies the stage size and the scale the
graphic got; strokes, shadows and outlines keep the theme's weight at any scale.
"""
from __future__ import annotations

from ..models import PlanError
from . import kit, motion
from .canvas import Canvas, saved
from .icons import ICONS, draw_icon, find_icon, icon_ink
from .paths import cut, length, point_at, rect, round_rect, smooth_points
from .scene import Scene, template
from .spec import decimals_of, format_number, number, pairs, required, split_list
from .theme import ink

GROW = 0.62            # seconds a bar takes to grow
STAGGER = 0.16         # delay between consecutive rows


def _baseline(y_center: float, size: float) -> float:
    """Baseline that centers capital letters of `size` on y_center."""
    return y_center + size * 0.35


def _icon_name(value) -> str | None:
    """An illustration asked for by name or by any word it answers to."""
    if not value:
        return None
    name = str(value).strip().lower()
    return name if name in ICONS else find_icon(name)


def _bob(scene: Scene, t: float, resting: bool) -> float:
    """The small up-and-down of the marker while it waits."""
    return scene.z(3) * (motion.wave(t, 0.0, 2) - 0.5) if resting else 0.0


# ── bars ─────────────────────────────────────────────────────────────────────
@template("bars")
class Bars(Scene):
    """Compare 1-6 values: gauges fill from the left, the numbers count up, one bar is the point.

    data="Before:15, 2026:40" · unit · prefix · title · source · highlight (label or 1-based index;
    default the last) · max · color
    """

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        rows = pairs(required(sp, "data", "bars"))
        if not 1 <= len(rows) <= 6:
            raise PlanError(f"Graphic 'bars' takes 1 to 6 values, not {len(rows)}")
        self.labels = [label for label, _ in rows]
        self.values = [number(v) for _, v in rows]
        if any(v is None for v in self.values):
            raise PlanError(f"Graphic 'bars': every value needs a number (data={sp.get('data')!r})")
        self.decimals = max(decimals_of(v) for _, v in rows)
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.unit, self.prefix = str(sp.get("unit") or ""), str(sp.get("prefix") or "")
        self.source = str(sp.get("source") or "")
        self.hi = self._highlight(sp.get("highlight"))
        top = float(number(sp.get("max")) or max(self.values)) or 1.0

        x0, y0, x1, y1 = st.safe
        self.title_size = z(46)
        self.title_rows = kit.balanced(st, str(sp["title"]), "display", self.title_size, st.safe_w) if sp.get("title") else []
        label_size = z(38)
        if st.vertical:                                    # label and value on a line, the gauge below across the width
            self.value_size, self.unit_size, self.bar_h = z(76), z(40), z(74)
            value_w = max(self._value_width(val) for val in self.values)
            self.label_size = min(kit.fit_size(st, lb, "label", label_size, st.safe_w - value_w - z(30)) for lb in self.labels)
            self.bar_x, self.bar_dy = x0, z(92)
            pitch = self.bar_dy + self.bar_h + z(64)
            self.track_w = st.safe_w
            narrow = st.safe_w - value_w < st.safe_w * 0.3
        else:                                              # labels in a column on the left, value after the gauge
            self.value_size, self.unit_size, self.bar_h = z(84), z(44), z(66)
            value_w = max(self._value_width(val) for val in self.values) + z(30)
            col = min(max(st.measure(lb, "label", label_size) for lb in self.labels), st.safe_w * 0.3)
            self.label_size = min(kit.fit_size(st, lb, "label", label_size, col) for lb in self.labels)
            self.bar_x, self.bar_dy = x0 + col + z(28), 0.0
            pitch = self.bar_h + z(60)
            self.track_w = x1 - self.bar_x - value_w
            narrow = self.track_w < st.safe_w * 0.3
        row_h = self.bar_dy + self.bar_h
        self.widths = [max(self.track_w * val / top, z(12)) for val in self.values]
        n = len(rows)
        title_h = len(self.title_rows) * self.title_size * 1.12 + z(58) if self.title_rows else 0
        source_h = z(72) if self.source else 0
        height = title_h + (n - 1) * pitch + row_h + source_h
        self.block = (st.safe_w * (2 if narrow else 1), height)
        if st.vertical:                                    # spread the rows over the height that is left
            pitch += min(max(st.safe_h * self.fill - height, 0) / max(n, 1), z(60))
            height = title_h + (n - 1) * pitch + row_h + source_h
        top_y = (y0 + y1) / 2 - height / 2
        self.title_y = top_y + self.title_size * 0.9
        self.rows_y = [top_y + title_h + i * pitch for i in range(n)]
        self.source_y = top_y + height
        self.starts = [i * STAGGER for i in range(n)]
        self.settle = self.starts[-1] + GROW + 0.3

    def _highlight(self, value) -> int:
        if value in (None, ""):
            return len(self.labels) - 1
        if str(value) in self.labels:
            return self.labels.index(str(value))
        idx = number(value)
        if idx is None or not 1 <= int(idx) <= len(self.labels):
            raise PlanError(f"Graphic 'bars': highlight '{value}' is not a label or a row number")
        return int(idx) - 1

    def _text(self, val: float) -> str:
        return self.prefix + format_number(val, self.st.language, self.decimals)

    def _value_width(self, val: float) -> float:
        w = self.st.measure(self._text(val), "number", self.value_size)
        return w + (self.z(10) + self.st.measure(self.unit, "number", self.unit_size) if self.unit else 0)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = st.safe[0]
        for k, row in enumerate(self.title_rows):
            kit.text(cv, st, row, x0, self.title_y + k * self.title_size * 1.12, "display", self.title_size, c["paper"])
        if self.title_rows:
            y = self.title_y + (len(self.title_rows) - 1) * self.title_size * 1.12 + z(26)
            kit.thread(cv, st, kit.squiggle(x0, x0 + z(190), y, z(5)), motion.ease_out(motion.progress(t, 0.05, 0.4)))
        for i, (label, val, w, y, start) in enumerate(zip(self.labels, self.values, self.widths, self.rows_y, self.starts)):
            hi = i == self.hi
            by = y + self.bar_dy
            cy = by + self.bar_h / 2
            if st.vertical:
                kit.text(cv, st, label, self.bar_x, y + self.bar_dy - z(24), "label", self.label_size,
                         c["paper"] if hi else c["muted"])
            else:
                kit.text(cv, st, label, self.bar_x - z(28), _baseline(cy, self.label_size), "label", self.label_size,
                         c["paper"] if hi else c["muted"], anchor="right")
            kit.shape(cv, st, rect(self.bar_x, by, self.track_w, self.bar_h), c["trace"], halo=False, with_shadow=True)
            for q in (0.25, 0.5, 0.75):                                            # quarter marks on the empty gauge
                qx = self.bar_x + self.track_w * q
                cv.stroke(rect(qx, by + self.bar_h * 0.66, 0.1, self.bar_h * 0.34), c["bg"], z(4), 0.8, cap="butt")
            p = motion.progress(t, start, GROW)
            cur = max(w * motion.action(p), z(8)) if p > 0 else 0.0
            if cur > 0:
                with kit.boiled(cv, st, i + 1, t, self.bar_x, cy):
                    kit.shape(cv, st, rect(self.bar_x, by, cur, self.bar_h), self.color if hi else c["muted"],
                              self.ink if hi else None, halo=False, shake=motion.shake(t - start - GROW) if hi else 0.0)
                    kit.shade(cv, st, rect(self.bar_x, by + self.bar_h * 0.64, cur, self.bar_h * 0.36), 0.3)
                    cv.fill(rect(self.bar_x + z(8), by + z(9), max(cur - z(16), 0), z(6)), c["paper"], 0.35)    # a shine
            if st.vertical:                                # right end of the label line (fixed, so it does not jump)
                vx, vy = st.safe[2] - self._value_width(val), y + self.bar_dy - z(24)
            else:
                vx, vy = self.bar_x + self.track_w + z(30), _baseline(cy, self.value_size)
            if p > 0:
                vw = kit.text(cv, st, self._text(val * motion.ease_out(p)), vx, vy, "number", self.value_size,
                              self.color if hi else c["paper"], p=kit.printed(t, start))
                if self.unit:
                    kit.text(cv, st, self.unit, vx + vw + z(10), vy, "number", self.unit_size, c["paper"], p=kit.printed(t, start))
            if hi and cur > 0:
                landed = motion.progress(t, start + GROW, 0.3)
                kit.draw_element("marker", cv, st, self.bar_x + cur, cy + _bob(self, t, landed >= 1), t, scale=self.k)
                if landed > 0:
                    kit.sparkle(cv, st, vx - z(20), vy - self.value_size * 0.9, z(15) * landed, t, 0.3)
                    kit.sparkle(cv, st, vx + self._value_width(val) + z(14), vy - self.value_size * 0.25, z(11) * landed, t, 0.7)
        kit.source_line(cv, st, self.source, x0, self.source_y, p=kit.printed(t, 0.25), size=z(26))

    def sounds(self):
        return [("whoosh", 0.0), ("pop", self.starts[self.hi] + GROW)]

    def lines(self):
        return [x for x in (" ".join(self.title_rows), *self.labels, self.source) if x]


# ── number ───────────────────────────────────────────────────────────────────
@template("number")
class Number(Scene):
    """One big figure, stamped on the page with rays around it.

    value (a number counts up; any other text is shown as is) · label · unit · prefix · sub · source ·
    icon (an illustration above it: phone, battery…) · from · count=false · color
    """

    default_dur = 3.0

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        raw = str(required(sp, "value", "number")).strip()
        num = number(raw)
        self.counts = num is not None and str(sp.get("count", "true")).lower() not in ("false", "no", "0") \
            and raw.replace(".", "", 1).lstrip("+-").isdigit()
        self.raw, self.num = raw, num
        self.start_value = float(number(sp.get("from")) or 0)
        self.decimals = decimals_of(raw)
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.source = str(sp.get("source") or "")
        self.unit, self.prefix = str(sp.get("unit") or ""), str(sp.get("prefix") or "")
        self.icon = _icon_name(sp.get("icon"))
        if sp.get("icon") and not self.icon:
            raise PlanError(f"Graphic 'number': no illustration called '{sp['icon']}' ({', '.join(sorted(ICONS))})")
        self.text_size = z(44)
        self.label = str(sp.get("label") or "")
        self.label_size = kit.fit_size(st, self.label, "label", self.text_size, st.safe_w * 0.7) if self.label else 0.0
        self.side = bool(self.icon) and not st.vertical        # landscape: the illustration sits beside the figure
        self.icon_size = (z(300) if self.side else z(230)) if self.icon else 0.0
        room = st.safe_w * 0.96 - (self.icon_size + z(70) if self.side else 0)
        self.sub_rows = kit.balanced(st, str(sp["sub"]), "label", self.text_size, room) if sp.get("sub") else []
        final = self._text(1.0)
        size, unit_ratio, gap = z(290), 0.42, z(16)

        def total(s):
            return st.measure(final, "number", s) + (gap + st.measure(self.unit, "number", s * unit_ratio) if self.unit else 0)

        rays = z(84)                                       # room for the rays on both sides
        if total(size) > room - 2 * rays:
            size *= (room - 2 * rays) / total(size)
        self.size, self.unit_size, self.gap, self.total_w = size, size * unit_ratio, gap, total(size)
        lh = self.text_size * 1.2
        icon_h = self.icon_size + z(30) if self.icon and not self.side else 0
        label_h = self.label_size * 1.6 + z(34) if self.label else 0
        sub_h = len(self.sub_rows) * lh + z(44) if self.sub_rows else 0
        source_h = z(64) if self.source else 0
        height = icon_h + label_h + size * 0.76 + sub_h + source_h
        col_w = max(self.total_w + 2 * rays, st.measure(self.label, "label", self.label_size) * 1.25 if self.label else 0)
        width = col_w + (self.icon_size + z(70) if self.side else 0)
        self.block = (width if size >= z(120) else st.safe_w * 2, max(height, self.icon_size if self.side else 0))
        cx, cy = st.center
        self.cx = cx + (self.icon_size + z(70)) / 2 if self.side else cx
        top = cy - height / 2
        self.icon_x, self.icon_y = (cx - width / 2 + self.icon_size / 2, cy) if self.side else (cx, top + self.icon_size / 2)
        self.label_y = top + icon_h + self.label_size * 0.8
        self.base_y = top + icon_h + label_h + size * 0.74
        self.mid_y = self.base_y - size * 0.36
        self.sub_y = self.base_y + z(44) + self.text_size
        self.source_y = top + height
        self.lh = lh
        self.count_dur = 0.7 if self.counts else 0.0
        self.impact = max(self.count_dur, 0.28)
        self.settle = self.impact + 0.6

    def _text(self, p: float) -> str:
        if not self.counts:
            return self.prefix + self.raw
        val = motion.lerp(self.start_value, self.num, motion.ease_out(p))
        return self.prefix + format_number(val, self.st.language, self.decimals)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        since = t - self.impact
        hit = motion.ease_out(motion.progress(t, self.impact, 0.22))
        kit.glow(cv, st, self.cx, self.mid_y, max(self.total_w * 0.6, self.size * 0.62), self.color, t)
        kit.burst(cv, st, self.cx, self.mid_y, self.total_w / 2 + z(26), self.size * 0.3, z(52), self.color, hit, t)
        if self.icon:
            pop = motion.stamp_scale(motion.progress(t, 0, 0.36), 0.7)
            kit.ground(cv, st, self.icon_x, self.icon_y + self.icon_size * 0.52, self.icon_size * 0.8)
            draw_icon(cv, st, self.icon, self.icon_x, self.icon_y, self.icon_size * pop, icon_ink(st, self.icon)[0], t,
                      active=motion.shake(since))
        if self.label:
            kit.tag(cv, st, self.label, self.cx, self.label_y, self.label_size, anchor="center")
        shown = self._text(motion.progress(t, 0, self.count_dur) if self.counts else 1.0)
        x = self.cx - self.total_w / 2
        scale = motion.stamp_scale(motion.progress(t, 0, 0.36)) + 0.05 * motion.shake(since)
        with kit.tilted(cv, 0.0, self.cx, self.mid_y, scale):
            path = cv.text_path(shown, x, self.base_y, "number", self.size)
            kit.shape(cv, st, path, self.color, self.ink, halo=False, shake=motion.shake(since))
            if self.unit:
                kit.text(cv, st, self.unit, x + st.measure(shown, "number", self.size) + self.gap, self.base_y, "number",
                         self.unit_size, c["paper"])
        kit.thread(cv, st, kit.squiggle(self.cx - self.total_w * 0.36, self.cx + self.total_w * 0.36, self.base_y + z(26), z(6), 4),
                   motion.ease_out(motion.progress(t, self.impact, 0.4)))
        for k, row in enumerate(self.sub_rows):
            kit.text(cv, st, row, self.cx, self.sub_y + k * self.lh, "label", self.text_size, c["paper"], "center",
                     p=kit.printed(t, self.impact))
        kit.sparkles(cv, st, (x - z(40), self.mid_y - self.size * 0.5, self.total_w + z(80), self.size), t, seed=7,
                     size=z(22), appear=hit)
        kit.source_line(cv, st, self.source, self.cx, self.source_y, "center", p=kit.printed(t, self.impact + 0.1),
                        size=z(26))

    def sounds(self):
        return [("whoosh", 0.0), ("pop", self.impact)]

    def lines(self):
        return [x for x in (self.label, " ".join(self.sub_rows), self.source) if x]


# ── product card ─────────────────────────────────────────────────────────────
@template("product_card")
class ProductCard(Scene):
    """A product with its key facts: photo as a sticker on a glow, name, up to 4 label/value facts.

    name · image (file; a PNG cut-out looks best) · specs="Capacity:2 TB; Read:14900 MB/s" · price ·
    source · color
    """

    default_dur = 4.0

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.product = str(required(sp, "name", "product_card"))
        self.image = str(sp.get("image") or "")
        if self.image and st.image_size(self.image) is None:
            raise PlanError(f"Graphic 'product_card': image not found or not readable: {self.image}")
        self.specs = pairs(sp.get("specs"))
        if sp.get("price"):
            self.specs.append(("Precio" if st.language == "es" else "Price", str(sp["price"])))
        if len(self.specs) > 4:
            raise PlanError(f"Graphic 'product_card' shows up to 4 facts, not {len(self.specs)}")
        self.color, self.ink = ink(st.theme, sp.get("color"))
        self.source = str(sp.get("source") or "")
        x0, y0, x1, y1 = st.safe
        self.value_size, self.label_size, self.name_size = z(72), z(28), z(42)
        self.pitch = self.label_size * 1.6 + self.value_size + z(44)
        specs_h = len(self.specs) * self.pitch + (z(40) if self.source else 0)
        if st.vertical:
            img_h = st.safe_h * 0.36 if self.image else 0.0
            name_h = self.name_size * 1.6 + z(50)
            height = img_h + name_h + specs_h
            top = (y0 + y1) / 2 - height / 2
            self.img_box = (x0, top, st.safe_w, img_h)
            self.anchor = "center"
            self.col_x, col_w = (x0 + x1) / 2, st.safe_w
            self.name_y = top + img_h + z(26) + self.name_size * 0.8
            self.specs_y = top + img_h + name_h
        else:
            left_w = st.safe_w * 0.5 if self.image else 0.0
            self.img_box = (x0, y0 + st.safe_h * 0.04, left_w - z(40), st.safe_h * 0.9)
            name_h = self.name_size * 1.6 + z(44)
            height = name_h + specs_h
            top = (y0 + y1) / 2 - height / 2
            self.anchor = "left"
            self.col_x, col_w = x0 + left_w + (z(30) if self.image else 0), st.safe_w - left_w - z(30)
            self.name_y = top + self.name_size * 0.8
            self.specs_y = top + name_h
        widest = max([st.measure(v, "number", self.value_size) for _, v in self.specs]
                     + [st.measure(self.product, "label", self.name_size) * 1.3])
        self.block = (st.safe_w + widest - col_w if widest > col_w else st.safe_w, height)
        self.starts = [0.3 + i * 0.26 for i in range(len(self.specs))]
        self.settle = (self.starts[-1] if self.starts else 0.3) + motion.PRINT_SECONDS + 0.2
        self.source_y = self.specs_y + len(self.specs) * self.pitch + z(20)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        if self.image:
            x, y, w, h = self.img_box
            kit.glow(cv, st, x + w / 2, y + h / 2, min(w, h) * 0.6, self.color, t)
            scale = motion.stamp_scale(motion.progress(t, 0, 0.4))
            sway = 0.6 * (motion.wave(t) - 0.5) if t > 0.5 else 0.0                 # the sticker breathes a little
            with kit.tilted(cv, kit.box_tilt(st) * 2.5 + sway, x + w / 2, y + h / 2, scale):
                cv.sticker(self.image, x, y, w, h, st.px(float(st.theme["sticker"]["border"])), c["paper"],
                           st.px(float(st.theme["stroke"]["outline"])), c["ink"])
            kit.sparkles(cv, st, (x + w * 0.12, y + h * 0.1, w * 0.76, h * 0.8), t, seed=3, size=z(22),
                         appear=motion.progress(t, 0.3, 0.3))
        kit.tag(cv, st, self.product, self.col_x, self.name_y, self.name_size, anchor=self.anchor)
        for i, ((label, value), start) in enumerate(zip(self.specs, self.starts)):
            y = self.specs_y + i * self.pitch
            p = kit.printed(t, start)
            kit.text(cv, st, label, self.col_x, y + self.label_size, "label", self.label_size, c["muted"], self.anchor, p=p)
            vy = y + self.label_size * 1.6 + self.value_size * 0.76
            vw = kit.text(cv, st, value, self.col_x, vy, "number", self.value_size, self.color if i == 0 else c["paper"],
                          self.anchor, p=p)
            ux = self.col_x - vw / 2 if self.anchor == "center" else self.col_x
            kit.thread(cv, st, kit.squiggle(ux, ux + vw, vy + z(20), z(4), max(int(vw / z(90)), 2)),
                       motion.ease_out(motion.progress(t, start + 0.1, 0.35)), alpha=0.9 if i == 0 else 0.45)
        kit.source_line(cv, st, self.source, self.col_x, self.source_y, self.anchor, p=kit.printed(t, self.settle - 0.3),
                        size=z(26))

    def lines(self):
        return [self.product, *(f"{a} {b}" for a, b in self.specs), *([self.source] if self.source else [])]


# ── title ────────────────────────────────────────────────────────────────────
@template("title")
class Title(Scene):
    """A section title or hook phrase as a poster on a glow (3 to 6 words work best). The words are
    split into the lines that make them largest for the frame; "|" forces the line breaks.

    text · kicker · icon (an illustration above it: phone, battery…) · color
    """

    default_dur = 2.2
    scales = (1.0,)                                  # it sizes itself: as large as the frame allows

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.textv = str(required(sp, "text", "title"))
        self.kicker = str(sp.get("kicker") or "")
        self.ink_name = sp.get("color")
        self.color, _ = ink(st.theme, self.ink_name)
        self.icon = _icon_name(sp.get("icon"))
        if sp.get("icon") and not self.icon:
            raise PlanError(f"Graphic 'title': no illustration called '{sp['icon']}' ({', '.join(sorted(ICONS))})")
        self.kicker_size = z(40)
        kicker_h = self.kicker_size * 1.6 + z(50) if self.kicker else 0
        under = z(90)
        room = st.safe_h * (0.9 if st.vertical else 0.84) - kicker_h - under
        self.side = bool(self.icon) and not st.vertical        # landscape: the illustration sits beside the title
        if self.side:
            self.icon_size = min(room * 0.95, st.safe_w * 0.26)
            icon_h, side_w = 0.0, self.icon_size + z(80)
        else:
            self.icon_size = min(room * 0.4, st.safe_w * 0.56) if self.icon else 0.0
            icon_h, side_w = (self.icon_size + z(50) if self.icon else 0.0), 0.0
        room -= icon_h
        max_w = st.safe_w * 0.94 - side_w
        forced = [r.strip() for r in self.textv.split("|") if r.strip()]
        options = [forced] if len(forced) > 1 else [r for n in range(1, 5) if (r := kit.split_lines(st, self.textv, "display", 100, n))]
        best = None
        for rows in options:                          # the line count that gives the largest letters…
            widths = [st.measure(r, "display", 100) for r in rows]
            if best is not None and min(widths) < 0.5 * max(widths):
                continue                              # …without leaving a short word alone on a line
            size = min(100 * max_w / max(widths), room / (len(rows) * kit.POSTER_LINE), z(340))
            if best is None or size > best[0] * 1.04:
                best = (size, rows)
        self.size, self.rows = best
        self.widest = max(st.measure(r, "display", self.size) for r in self.rows)
        self.height = len(self.rows) * self.size * kit.POSTER_LINE
        self.block = (self.widest, self.height)
        cx, cy = st.center
        self.cx = cx + side_w / 2
        total = icon_h + kicker_h + self.height + under
        top = cy - total / 2
        self.icon_x, self.icon_y = (self.cx - self.widest / 2 - side_w + self.icon_size / 2, cy) if self.side else (cx, top + self.icon_size / 2)
        self.kicker_y = top + icon_h + self.kicker_size * 0.8
        self.cy = top + icon_h + kicker_h + self.height / 2
        self.under = under
        self.settle = 0.95

    def draw(self, cv: Canvas, t: float) -> None:
        st, z = self.st, self.z
        since = t - 0.26
        box = (self.cx - self.widest / 2, self.cy - self.height / 2, self.widest, self.height)
        kit.glow(cv, st, self.cx, self.cy, max(self.widest * 0.56, self.height * 0.75), self.color, t)
        if self.icon:
            pop = motion.stamp_scale(motion.progress(t, 0.05, 0.4), 0.6)
            kit.ground(cv, st, self.icon_x, self.icon_y + self.icon_size * 0.5, self.icon_size * 0.8)
            draw_icon(cv, st, self.icon, self.icon_x, self.icon_y, self.icon_size * pop, icon_ink(st, self.icon)[0], t,
                      active=motion.shake(since))
        if self.kicker:
            kit.tag(cv, st, self.kicker, self.cx, self.kicker_y, self.kicker_size, anchor="center")
        kit.poster(cv, st, self.rows, self.cx, self.cy, self.size, self.ink_name,
                   scale=motion.stamp_scale(motion.progress(t, 0, 0.4)) + 0.04 * motion.shake(since),
                   shake=motion.shake(since))
        drawn = motion.ease_out(motion.progress(t, 0.4, 0.45))
        line = kit.squiggle(box[0] + self.widest * 0.08, box[0] + self.widest * 0.86, box[1] + self.height + self.under * 0.55,
                            z(9), 4)
        part = kit.thread(cv, st, line, drawn)
        if drawn > 0 and part:
            kit.draw_element("marker", cv, st, part[-1][0], part[-1][1] + _bob(self, t, drawn >= 1), t,
                             scale=max(self.size / st.px(150), 1.0) * 1.1)
        kit.sparkles(cv, st, box, t, seed=11, size=max(self.size * 0.2, z(26)), appear=motion.progress(t, 0.25, 0.35))

    def sounds(self):
        return [("pop", 0.0), ("whoosh", 0.4)]

    def lines(self):
        return [x for x in (self.kicker, self.textv.replace("|", " ")) if x]


# ── headline ─────────────────────────────────────────────────────────────────
@template("headline")
class Headline(Scene):
    """A news headline or a quote as a paper clipping taped to the stage, with a phrase marked.

    text · outlet (who published it) · date · highlight (words of the text to mark) · color
    """

    default_dur = 4.0

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.textv = str(required(sp, "text", "headline"))
        self.outlet, self.date = str(sp.get("outlet") or sp.get("source") or ""), str(sp.get("date") or "")
        self.color, _ = ink(st.theme, sp.get("color"))
        pad = z(40)
        w = st.safe_w * (0.97 if st.vertical else 0.72)
        self.size = z(62)
        self.rows = kit.wrap(st, self.textv, "display", self.size, w - 2 * pad)
        self.lh = self.size * 1.1
        self.small = z(27)
        head = self.small + z(40) if self.outlet else 0
        foot = self.small + z(26) if self.date else 0
        h = pad + head + len(self.rows) * self.lh + foot + pad * 0.7
        self.block = (w if len(self.rows) <= 6 else st.safe_w * 2, h + z(60))
        cx, cy = st.center
        self.rect = (cx - w / 2, cy - h / 2, w, h)
        self.text_x, self.text_y, self.pad = cx - w / 2 + pad, cy - h / 2 + pad + head, pad
        self.marks = self._marks(str(sp.get("highlight") or ""))
        self.mark_start, self.mark_dur = 0.7, 0.5
        self.settle = (self.mark_start + self.mark_dur + 0.1) if self.marks else 0.9

    def _marks(self, phrase: str) -> list[tuple[int, float, float]]:
        """(row, x0, x1) of the highlighted words, one entry per row they touch."""
        def clean(word: str) -> str:
            return word.lower().strip(".,;:!?¿¡\"'")

        want = [clean(w) for w in phrase.split()]
        if not want:
            return []
        words = []                                   # (row, word, x0, x1)
        space = self.st.measure(" ", "display", self.size)
        for r, row in enumerate(self.rows):
            x = 0.0
            for word in row.split():
                w = self.st.measure(word, "display", self.size)
                words.append((r, clean(word), x, x + w))
                x += w + space
        for i in range(len(words) - len(want) + 1):
            if [w[1] for w in words[i:i + len(want)]] == want:
                found: dict[int, list[float]] = {}
                for r, _, a, b in words[i:i + len(want)]:
                    lo, hi = found.get(r, [a, b])
                    found[r] = [min(lo, a), max(hi, b)]
                return [(r, a, b) for r, (a, b) in sorted(found.items())]
        raise PlanError(f"Graphic 'headline': highlight '{phrase}' is not in the text")

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x, y, w, h = self.rect
        cx, cy = x + w / 2, y + h / 2
        scale = motion.stamp_scale(motion.progress(t, 0, 0.4))
        tilt = kit.box_tilt(st)
        with kit.tilted(cv, tilt + 2.6, cx, cy, scale):                           # the sheet underneath
            kit.shape(cv, st, round_rect(x + z(10), y + z(14), w, h, st.px(float(st.theme["box"].get("radius", 0)))),
                      c["muted"], halo=False, with_shadow=True)
        with kit.tilted(cv, tilt, cx, cy, scale):
            kit.box(cv, st, x, y, w, h)
            if self.outlet:
                cv.fill(rect(self.text_x, y + self.pad - z(6), z(14), self.small * 0.9), self.color)
                kit.text(cv, st, self.outlet, self.text_x + z(26), y + self.pad + self.small * 0.72, "label", self.small,
                         c["ink"], alpha=0.75)
                cv.fill(rect(self.text_x, y + self.pad + self.small + z(14), w - 2 * self.pad, z(3)), c["ink"], 0.75)
            total = sum(b - a for _, a, b in self.marks) or 1.0
            done = motion.ease_in_out(motion.progress(t, self.mark_start, self.mark_dur)) * total
            for r, a, b in self.marks:
                part = min(max(done, 0.0), b - a)
                done -= b - a
                if part > 0:
                    cv.fill(rect(self.text_x + a - z(5), self.text_y + r * self.lh + self.size * 0.3, part + z(10),
                                 self.size * 0.62), self.color)
            for r, row in enumerate(self.rows):
                kit.text(cv, st, row, self.text_x, self.text_y + r * self.lh + self.size * 0.8, "display", self.size, c["ink"])
            if self.date:
                kit.text(cv, st, self.date, self.text_x, y + h - z(30), "label", self.small, c["ink"], alpha=0.6)
            for tx, deg in ((x + w * 0.16, -7.0), (x + w * 0.84, 6.0)):           # tape
                with kit.tilted(cv, deg, tx, y):
                    cv.fill(rect(tx - z(64), y - z(19), z(128), z(38)), self.color, 0.82)
                    cv.fill(rect(tx - z(64), y - z(19), z(128), z(9)), c["paper"], 0.25)
            peek = motion.ease_out(motion.progress(t, 0.5, 0.3))                   # the marker looks over the edge
            if peek > 0:
                kit.draw_element("marker", cv, st, x + w * 0.6, y + z(8) - z(24) * peek + _bob(self, t, peek >= 1), t,
                                 scale=self.k * 1.25)

    def sounds(self):
        return [("pop", 0.0)]

    def lines(self):
        return [x for x in (self.outlet, self.textv, self.date) if x]


# ── list ─────────────────────────────────────────────────────────────────────
@template("list")
class ListScene(Scene):
    """1-6 points that appear one by one, each on its own paper strip with a round mark.

    items="first; second; third" · title · marks: "n" numbers (default), "+" checks, "-" crosses, or
    one per item ("+,-,+") · color
    """

    default_dur = 4.5

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.items = split_list(required(sp, "items", "list"))
        if not 1 <= len(self.items) <= 6:
            raise PlanError(f"Graphic 'list' takes 1 to 6 items, not {len(self.items)}")
        marks = split_list(sp.get("marks")) or ["n"]
        self.marks = [marks[i % len(marks)] for i in range(len(self.items))]
        self.color, self.ink = ink(st.theme, sp.get("color"))
        x0, y0, x1, y1 = st.safe
        self.size, self.title_size = z(42), z(60)
        self.badge_r = z(38)
        self.pad_x, self.pad_y = z(30), z(24)
        self.left = x0
        self.strip_x = x0 + self.badge_r * 0.9
        strip_w = st.safe_w - self.badge_r * 0.9
        text_w = strip_w - self.badge_r - 2 * self.pad_x
        self.title_rows = kit.balanced(st, str(sp["title"]), "display", self.title_size, st.safe_w) if sp.get("title") else []
        self.rows = [kit.wrap(st, it, "label", self.size, text_w) for it in self.items]
        self.lh = self.size * 1.2
        gap = z(30)
        self.heights = [len(r) * self.lh + 2 * self.pad_y for r in self.rows]
        widths = [max(st.measure(r, "label", self.size) for r in rows) + self.badge_r + 2 * self.pad_x for rows in self.rows]
        self.strip_w = [strip_w] * len(widths) if st.vertical else [min(strip_w, max(w, strip_w * 0.5)) for w in widths]
        if not st.vertical:                                # landscape: the whole list sits in the middle
            used = max([w + self.badge_r * 0.9 for w in self.strip_w]
                       + [st.measure(r, "display", self.title_size) for r in self.title_rows])
            self.left = x0 + (st.safe_w - used) / 2
            self.strip_x = self.left + self.badge_r * 0.9
        title_h = len(self.title_rows) * self.title_size * 1.1 + z(70) if self.title_rows else 0
        height = title_h + sum(self.heights) + gap * (len(self.rows) - 1)
        self.block = (st.safe_w if all(len(r) <= 3 for r in self.rows) else st.safe_w * 2, height)
        if st.vertical:                                    # spread the strips over the height that is left
            gap += min(max(st.safe_h * self.fill - height, 0) / max(len(self.rows), 1), z(40))
            height = title_h + sum(self.heights) + gap * (len(self.rows) - 1)
        top = (y0 + y1) / 2 - height / 2
        self.title_y = top + self.title_size * 0.9
        self.ys, y = [], top + title_h
        for hh in self.heights:
            self.ys.append(y)
            y += hh + gap
        n = len(self.items)
        step = min(max((self.dur - 1.4) / n, 0.18), 0.5)
        self.starts = [i * step for i in range(n)]
        self.settle = self.starts[-1] + 0.7

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = self.left
        for k, row in enumerate(self.title_rows):
            kit.text(cv, st, row, x0, self.title_y + k * self.title_size * 1.1, "display", self.title_size, c["paper"])
        if self.title_rows:
            y = self.title_y + (len(self.title_rows) - 1) * self.title_size * 1.1 + z(30)
            kit.thread(cv, st, kit.squiggle(x0, x0 + z(230), y, z(6)), motion.ease_out(motion.progress(t, 0.05, 0.4)))
        for i, (rows, mark, y, h, w, start) in enumerate(zip(self.rows, self.marks, self.ys, self.heights, self.strip_w, self.starts)):
            if t < start:
                continue
            p = motion.progress(t, start, 0.26)
            grown = w * motion.ease_out(p)
            cy = y + h / 2
            with kit.boiled(cv, st, 40 + i, t, self.strip_x, cy):
                with kit.tilted(cv, kit.box_tilt(st, i) * 0.7, self.strip_x, cy):
                    if grown > self.badge_r:
                        kit.box(cv, st, self.strip_x, y, grown, h)
                        with saved(cv):
                            cv.clip(rect(self.strip_x, y, grown, h))
                            for k, row in enumerate(rows):
                                kit.text(cv, st, row, self.strip_x + self.badge_r + self.pad_x,
                                         y + self.pad_y + k * self.lh + self.size * 0.86, "label", self.size, c["ink"],
                                         p=kit.printed(t, start + 0.08))
                    kit.badge(cv, st, self.strip_x, cy, self.badge_r, c["paper"] if mark in ("+", "-") else self.color,
                              scale=motion.stamp_scale(p, 0.5) + 0.08 * motion.shake(t - start - 0.26))
                    mp = motion.ease_out(motion.progress(t, start + 0.12, 0.24))
                    if mark == "+":
                        kit.draw_element("check", cv, st, self.strip_x, cy, t, color=c.get("good", self.color), p=mp,
                                         size=self.badge_r * 1.1)
                    elif mark == "-":
                        kit.draw_element("cross", cv, st, self.strip_x, cy, t, p=mp, size=self.badge_r * 1.1)
                    else:
                        kit.text(cv, st, str(i + 1) if mark == "n" else mark, self.strip_x, _baseline(cy, self.badge_r * 1.1),
                                 "number", self.badge_r * 1.1, c["ink"], "center", p=kit.printed(t, start + 0.1))
        kit.sparkles(cv, st, (x0, self.ys[0], max(self.strip_w) + self.badge_r, self.ys[-1] + self.heights[-1] - self.ys[0]), t, seed=5, size=z(20),
                     appear=motion.progress(t, self.starts[-1] + 0.3, 0.3))

    def sounds(self):
        return [("pop", s) for s in self.starts]

    def lines(self):
        return [x for x in (" ".join(self.title_rows), *self.items) if x]


# ── lower third ──────────────────────────────────────────────────────────────
@template("lower_third")
class LowerThird(Scene):
    """A small caption box over the video, in the bottom-left corner of the safe zone so it covers as
    little of the picture as possible: who is talking, what is on screen, or where a clip comes from.

    text · kicker (small line above: "Video", "On the channel"…) · color
    """

    overlay = True
    default_dur = 3.0
    scales = (1.0,)

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.textv = str(required(sp, "text", "lower_third"))
        self.kicker = str(sp.get("kicker") or "")
        self.color, _ = ink(st.theme, sp.get("color"))
        self.size, self.k_size = z(38), z(24)
        self.strip = z(12)
        pad_x, pad_y = z(24), z(16)
        max_w = st.W * (0.56 if st.vertical else 0.34)
        self.rows = kit.wrap(st, self.textv, "label", self.size, max_w)[:2]
        self.lh = self.size * 1.16
        inner_w = max([st.measure(r, "label", self.size) for r in self.rows]
                      + [st.measure(self.kicker, "label", self.k_size) if self.kicker else 0])
        head = self.k_size + z(10) if self.kicker else 0
        self.w, self.h = inner_w + 2 * pad_x + self.strip, pad_y * 2 + head + len(self.rows) * self.lh - z(8)
        self.x = st.safe[0]
        self.y = st.safe[3] - self.h                         # bottom-left corner of the safe zone
        self.pad_x, self.pad_y, self.head = pad_x, pad_y, head
        self.settle = 0.2 + motion.PRINT_SECONDS + 0.2
        self.exit_at = max(self.dur - 0.2, self.settle)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        grow = motion.ease_out(motion.progress(t, 0, 0.2)) * (1 - motion.ease_in_out(motion.progress(t, self.exit_at, 0.2)))
        w = self.w * grow
        if w < self.strip * 2:
            return
        with kit.tilted(cv, kit.box_tilt(st), self.x, self.y + self.h / 2):
            kit.box(cv, st, self.x, self.y, w, self.h)
            cv.fill(rect(self.x, self.y, self.strip, self.h), self.color)       # accent strip on the left
            with saved(cv):
                cv.clip(rect(self.x, self.y, w, self.h))
                tx, p = self.x + self.strip + self.pad_x, kit.printed(t, 0.12)
                if self.kicker:
                    kit.text(cv, st, self.kicker, tx, self.y + self.pad_y + self.k_size * 0.82, "label", self.k_size,
                             c["ink"], alpha=0.7, p=p)
                for k, row in enumerate(self.rows):
                    kit.text(cv, st, row, tx, self.y + self.pad_y + self.head + k * self.lh + self.size * 0.8, "label",
                             self.size, c["ink"], p=p)
            if grow >= 1:                                                        # the marker sits on the box
                kit.draw_element("marker", cv, st, self.x + self.w - z(34), self.y - z(10) + _bob(self, t, True), t, scale=0.9)

    def sounds(self):
        return []

    def lines(self):
        return [x for x in (self.kicker, self.textv) if x]


# ── diagram ──────────────────────────────────────────────────────────────────
@template("diagram")
class Diagram(Scene):
    """How something works: 2-5 things and the arrows between them, played one step at a time while a
    marker travels along each arrow. A thing whose name is known (phone, router, internet, server,
    chip, battery…) is drawn as its illustration with the name on a tag; any other is a paper card.

    nodes="Phone, Router, Internet" (add ":ink" to color one: "Router:blue") ·
    icons="phone, router, globe" (to choose the illustrations; "-" = a plain card) ·
    edges="Phone>Router:asks; Router>Internet" (in the order they happen; ":label" is optional) · title
    """

    default_dur = 5.0

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        names, given = [], []
        for item in split_list(required(sp, "nodes", "diagram")):
            name, _, color = item.partition(":")
            names.append(name.strip())
            given.append(color.strip() or None)
        if not 2 <= len(names) <= 5:
            raise PlanError(f"Graphic 'diagram' takes 2 to 5 nodes, not {len(names)}")
        self.names = names
        n = len(names)
        asked = split_list(sp.get("icons"))
        if asked and len(asked) != n:
            raise PlanError(f"Graphic 'diagram': 'icons' needs one entry per node ({n}), not {len(asked)}")
        self.icons = [None if a == "-" else _icon_name(a) for a in asked] if asked else [find_icon(nm) for nm in names]
        for a, found in zip(asked, self.icons):
            if a != "-" and found is None:
                raise PlanError(f"Graphic 'diagram': no illustration called '{a}' ({', '.join(sorted(ICONS))})")
        self.fills = [icon_ink(st, ic, g) if ic else (ink(st.theme, g) if g else (st.theme["colors"]["paper"], None))
                      for ic, g in zip(self.icons, given)]
        x0, y0, x1, y1 = st.safe
        self.s = z(250 if st.vertical else 240)            # an illustration
        self.tag_size, self.title_size, self.edge_size = z(34), z(60), z(30)
        self.tag_h = self.tag_size * 1.6
        self.cell_h = self.s + z(12) + self.tag_h
        self.title_rows = kit.balanced(st, str(sp["title"]), "display", self.title_size, st.safe_w) if sp.get("title") else []
        title_h = len(self.title_rows) * self.title_size * 1.1 + z(74) if self.title_rows else 0
        tag_w = max(st.measure(nm, "label", self.tag_size) for nm in names) + self.tag_size
        parsed = [self._parse(e) for e in split_list(sp.get("edges"))]
        around = any(j != i + 1 for i, j, _ in parsed)                     # arrows that go back or skip a thing
        label_w = max([st.measure(lb, "label", self.edge_size) + self.edge_size for _, _, lb in parsed] + [0.0])
        self.bulge = z(104 if st.vertical else 84)
        cell_w = max(self.s, tag_w)
        if st.vertical:                                    # a zigzag down the frame: left, right, left…
            pitch = self.cell_h * 0.6 + z(30)
            height = title_h + self.cell_h + (n - 1) * pitch
            self.block = (2 * cell_w + max(z(70), label_w * 0.6), height)
            spare = max(st.safe_h * self.fill - height, 0)
            pitch += min(spare / (n - 1), z(120))
            total = title_h + self.cell_h + (n - 1) * pitch
            top = (y0 + y1) / 2 - total / 2
            inset = min(cell_w / 2 + st.safe_w * 0.04, st.safe_w * 0.3)
            self.centers = [(x0 + inset if i % 2 == 0 else x1 - inset, top + title_h + self.s / 2 + i * pitch)
                            for i in range(n)]
        else:
            min_gap = max(z(150), label_w + z(70))
            width = n * cell_w + (n - 1) * min_gap
            below = self.bulge + z(60) + self.edge_size * 1.6 if around else 0
            height = title_h + self.cell_h + below
            self.block = (width, height)
            gap = min(max((st.safe_w - n * cell_w) / (n - 1), min_gap), z(460))
            total_w = n * cell_w + (n - 1) * gap
            left = (x0 + x1) / 2 - total_w / 2
            top = (y0 + y1) / 2 - height / 2
            self.centers = [(left + cell_w / 2 + i * (cell_w + gap), top + title_h + self.s / 2) for i in range(n)]
        self.title_y = top + self.title_size * 0.9
        self.edges = [self._route(i, j, label) for i, j, label in parsed]
        self.steps = None                                  # vertical: the steps counted off in the corner the zigzag leaves empty
        if st.vertical and parsed and all(i != n - 1 for i, _, _ in parsed):
            lx, ly = self.centers[-1]
            ox = self.centers[-2][0]
            room = min(abs(lx - ox) - cell_w / 2 - z(40), 2 * min(ox - x0, x1 - ox))
            m = len(parsed)
            r = min(z(36), room / (m * 2.7 - 0.7))
            if r >= z(16):
                gap = r * 0.7
                left = ox - (m * 2 * r + (m - 1) * gap) / 2 + r
                self.steps = {"y": ly + self.s / 2 + z(12) + self.tag_h / 2, "r": r,
                              "xs": [left + k * (2 * r + gap) for k in range(m)]}
        self.t0 = 0.45
        k = max(len(self.edges), 1)
        self.seg = min((self.dur - self.t0 - 0.35) / k, 1.3)
        if self.seg < 0.35:
            raise PlanError(f"Graphic 'diagram': {len(self.edges)} steps do not fit in {self.dur} s (give it more 'dur')")
        self.settle = self.t0 + len(self.edges) * self.seg + 0.35

    def _parse(self, text: str) -> tuple[int, int, str]:
        ends, _, label = text.partition(":")
        a, sep, b = ends.partition(">")
        a, b = a.strip(), b.strip()
        if not sep or a not in self.names or b not in self.names or a == b:
            raise PlanError(f"Graphic 'diagram': edge '{text}' must be \"A>B\" with two different nodes "
                            f"({', '.join(self.names)})")
        return self.names.index(a), self.names.index(b), label.strip()

    def _route(self, i: int, j: int, label: str) -> dict:
        """The line of an arrow from thing i to thing j: straight to the next one; around the side
        (vertical) or underneath (landscape) when it goes back or skips one."""
        st, z = self.st, self.z
        (ax, ay), (bx, by) = self.centers[i], self.centers[j]
        g = z(16)
        under = z(46)                                      # clear of the name tag, with room for the marker
        direct = j == i + 1
        half = self.s / 2
        if st.vertical:
            if abs(ax - bx) < 1:                           # same column: straight down (or up) between them
                down = by > ay
                pts = [(ax, ay - half + self.cell_h + under) if down else (ax, ay - half - g),
                       (bx, by - half - g) if down else (bx, by - half + self.cell_h + under)]
            else:                                          # out of its side, then into the other one from above or below
                side = 1 if bx > ax else -1
                start = (ax + side * (half + g), ay)
                end = (bx, by - half - g) if by > ay else (bx, by - half + self.cell_h + under)
                corner = (bx - side * half * 0.25, ay + (by - ay) * 0.08)
                pts = smooth_points([start, corner, end], tension=0.75)
            label_at, anchor = point_at(pts, 0.42)[0], "center"
        else:
            if direct:
                pts = [(ax + half + g, ay), (bx - half - g, by)]
                label_at, anchor = ((pts[0][0] + pts[1][0]) / 2, ay - z(44)), "center"
            else:
                ey = ay - half + self.cell_h + under
                pts = smooth_points([(ax, ey), ((ax + bx) / 2, ey + self.bulge), (bx, ey)])
                label_at, anchor = ((ax + bx) / 2, ey + self.bulge + z(30)), "center"
        return {"to": j, "pts": pts, "label": label, "label_at": label_at, "anchor": anchor}

    def _node(self, cv: Canvas, i: int, t: float) -> None:
        st, z = self.st, self.z
        cx, cy = self.centers[i]
        arrivals = [self.t0 + k * self.seg + self.seg * 0.9 for k, e in enumerate(self.edges) if e["to"] == i]
        pulse = sum(motion.shake(t - a) for a in arrivals)
        scale = motion.stamp_scale(motion.progress(t, i * 0.08, 0.36), 0.6) + 0.1 * pulse
        color, _ = self.fills[i]
        if self.icons[i]:
            kit.ground(cv, st, cx, cy + self.s * 0.5, self.s * 0.86)
            with kit.boiled(cv, st, 20 + i, t, cx, cy):
                draw_icon(cv, st, self.icons[i], cx, cy, self.s * scale, color, t, active=min(pulse * 1.4, 1.0))
        else:
            with kit.boiled(cv, st, 20 + i, t, cx, cy):
                with kit.tilted(cv, kit.box_tilt(st, i), cx, cy, scale):
                    kit.box(cv, st, cx - self.s * 0.5, cy - self.s * 0.34, self.s, self.s * 0.68, fill=color)
                    cv.fill(rect(cx - self.s * 0.5, cy - self.s * 0.34, self.s, z(16)), st.theme["colors"]["ink"], 0.18)
        kit.tag(cv, st, self.names[i], cx, cy + self.s / 2 + z(12) + self.tag_h / 2, self.tag_size, anchor="center", index=i,
                p=motion.progress(t, i * 0.08, 0.3))

    def _steps(self, cv: Canvas, t: float, current: int) -> None:
        """The steps counted off (1, 2, 3…): a row of small stamps, each one inked in the color of the
        thing its arrow reaches once the marker gets there."""
        if not self.steps:
            return
        st, c = self.st, self.st.theme["colors"]
        y, r, xs = self.steps["y"], self.steps["r"], self.steps["xs"]
        if len(xs) > 1:
            kit.thread(cv, st, [(xs[0], y), (xs[-1], y)], motion.ease_out(motion.progress(t, 0.1, 0.4)), alpha=0.5)
        for k, x in enumerate(xs):
            arrive = self.t0 + k * self.seg + self.seg * 0.9
            done = t >= arrive
            p = motion.progress(t, 0.12 + k * 0.06, 0.3)
            if p <= 0:
                continue
            kit.badge(cv, st, x, y, r, self.fills[self.edges[k]["to"]][0] if done else c["paper"],
                      scale=motion.stamp_scale(p, 0.5) + (0.12 * motion.shake(t - arrive) if done else 0.0))
            kit.text(cv, st, str(k + 1), x, _baseline(y, r * 1.1), "number", r * 1.1, c["ink"], "center",
                     alpha=1.0 if done or k == current else 0.55)

    def draw(self, cv: Canvas, t: float) -> None:
        st, c, z = self.st, self.st.theme["colors"], self.z
        x0 = st.safe[0]
        for k, row in enumerate(self.title_rows):
            kit.text(cv, st, row, x0, self.title_y + k * self.title_size * 1.1, "display", self.title_size, c["paper"])
        if self.title_rows:
            y = self.title_y + (len(self.title_rows) - 1) * self.title_size * 1.1 + z(30)
            kit.thread(cv, st, kit.squiggle(x0, x0 + z(230), y, z(6)), motion.ease_out(motion.progress(t, 0.05, 0.4)))
        marker_at = self.edges[0]["pts"][0] if self.edges else None
        current = -1
        for k, e in enumerate(self.edges):
            start = self.t0 + k * self.seg
            if t < start:
                break
            current = k
            drawn = motion.ease_out(motion.progress(t, start, self.seg * 0.35))
            part = kit.thread(cv, st, e["pts"], drawn)
            if drawn >= 1 and length(part) > 0:
                (hx, hy), deg = point_at(e["pts"], 1.0)
                kit.arrow_head(cv, st, hx, hy, deg, size=z(26))
            travel = motion.ease_in_out(motion.progress(t, start + self.seg * 0.15, self.seg * 0.75))
            marker_at = cut(e["pts"], travel)[-1]
        if current >= 0:                                    # the connection in use: dashes march along it
            e = self.edges[current]
            if t >= self.t0 + current * self.seg + self.seg * 0.35:
                kit.rail(cv, st, cut(e["pts"], 0.93), t, color=self.fills[e["to"]][0], width=st.px(4))
        for k, e in enumerate(self.edges[:current + 1]):
            if e["label"]:
                kit.tag(cv, st, e["label"], *e["label_at"], self.edge_size, anchor=e["anchor"], index=k + 1,
                        p=motion.progress(t, self.t0 + k * self.seg + 0.05, 0.3))
        lit = self.edges[current]["to"] if current >= 0 else None
        if current >= 0 and t < self.t0 + current * self.seg + self.seg * 0.9:
            lit = None                                      # the light comes on when the marker arrives
        if lit is not None:
            kit.glow(cv, st, *self.centers[lit], self.s * 0.82, self.fills[lit][0], t)
        self._steps(cv, t, current)
        for i in range(len(self.names)):
            self._node(cv, i, t)
        if marker_at is not None and t >= self.t0 * 0.6:
            kit.draw_element("marker", cv, st, marker_at[0], marker_at[1] + _bob(self, t, t >= self.settle), t,
                             scale=self.k * 1.1)

    def sounds(self):
        return [("whoosh", 0.0)] + [("pop", self.t0 + k * self.seg + self.seg * 0.9) for k in range(len(self.edges))]

    def lines(self):
        return [x for x in (" ".join(self.title_rows), *self.names, *(e["label"] for e in self.edges)) if x]
