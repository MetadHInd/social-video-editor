"""Illustrated scenes: a short animation of the thing a sentence talks about doing what the sentence
says — an app swiped away, a battery that drains, an app asleep, something stored in memory, a
message that travels — drawn by the engine when no footage shows it well.

    scene action="swipe" subject="app" text="La cierras y empieza de cero"

action: swipe · open · sleep · drain · charge · send · store · freeze · clean · work · wait · faster · slower
subject: the illustration that acts (phone, app, battery, ram…; a word naming one works too)
target: a second illustration for send / store (where it goes)
text: what is said, as a short poster above the scene (optional) · kicker · color

`action_of(words)` reads the action from the verbs of a sentence (Spanish and English), so the
director can propose one. Every scene: the subject large on a glow, the action played once (about a
second and a half), then a loop of ambient life (Zs that float, a packet that keeps travelling, a
spinner, snowflakes, heat waves…), so a scene that stays longer is never still.
"""
from __future__ import annotations

import math
import unicodedata

from ..models import PlanError
from . import kit, motion
from .canvas import Canvas, saved
from .icons import ICONS, draw_icon, find_icon, icon_ink
from .paths import circle, poly, round_rect, smooth_points
from .scene import Scene, template

ACTIONS = {          # action → (default subject, default target, words that ask for it: stems, accents removed)
    "swipe": ("app", "phone", ("cierr", "cerr", "desliz", "quit", "borr", "elimin", "close", "swipe", "remov", "kill")),
    "open": ("app", "phone", ("abr", "abre", "lanz", "arranc", "empiez", "inici", "open", "launch", "start")),
    "sleep": ("app", None, ("dorm", "duerm", "descans", "pausad", "reposo", "sleep", "asleep", "idle")),
    "drain": ("battery", None, ("gast", "consum", "agot", "chup", "drena", "drain", "waste", "use up")),
    "charge": ("battery", None, ("carg", "ahorr", "recarg", "charg", "save")),
    "send": ("phone", "globe", ("envi", "mand", "viaj", "lleg", "transmit", "send", "travel", "arriv")),
    "store": ("app", "disk", ("guard", "almacen", "bodeg", "baj", "meter", "mete", "store", "keep", "move to")),
    "freeze": ("phone", None, ("congel", "trab", "cuelg", "bloque", "freez", "hang", "stuck")),
    "clean": ("phone", None, ("limpi", "liber", "despej", "clean", "clear", "free up")),
    "work": ("chip", None, ("trabaj", "esfuerz", "proces", "calient", "work", "process", "heat")),
    "wait": ("app", None, ("esper", "tard", "cuest", "demor", "wait", "take", "slow")),
    "faster": ("phone", None, ("rapid", "acelera", "agil", "fast", "speed", "quick")),
    "slower": ("phone", None, ("lent", "pesad", "slow down", "laggy")),
}
TWO = ("send", "store")                    # actions between two illustrations
ENTER = 0.25                               # the stage comes in, then the action
ACT = 1.2                                  # seconds the action takes


def _plain(word: str) -> str:
    w = unicodedata.normalize("NFKD", str(word).lower())
    return "".join(c for c in w if not unicodedata.combining(c)).strip("¿?¡!.,;:«»\"'()…")


def action_of(words) -> tuple[str, int] | None:
    """(action, index of the word) a sentence asks for: the first verb (by its stem) that names one.
    `words`: the sentence's words, as written."""
    plain = [_plain(w) for w in words]
    for i, w in enumerate(plain):
        if len(w) < 3:
            continue
        for name, (_, _, stems) in ACTIONS.items():
            if any(w.startswith(s) for s in stems if " " not in s):
                return name, i
    return None


def _icon(value, fallback: str | None) -> str | None:
    if not value:
        return fallback
    name = str(value).strip().lower()
    found = name if name in ICONS else find_icon(name)
    if found is None:
        raise PlanError(f"Graphic 'scene': no illustration called '{value}' ({', '.join(sorted(ICONS))})")
    return found


@template("scene")
class SceneAction(Scene):
    """An illustration doing what the sentence says (see the module docstring)."""

    default_dur = 3.5
    scales = (1.0,)

    def layout(self) -> None:
        st, sp, z = self.st, self.spec, self.z
        self.action = str(sp.get("action") or "").strip().lower()
        if self.action not in ACTIONS:
            raise PlanError(f"Graphic 'scene': action must be one of {', '.join(ACTIONS)}, not '{sp.get('action')}'")
        subject, target, _ = ACTIONS[self.action]
        self.subject = _icon(sp.get("subject"), subject)
        self.target = _icon(sp.get("target"), target) if self.action in TWO or self.action in ("swipe", "open") else None
        self.textv = str(sp.get("text") or "").strip()
        self.kicker = str(sp.get("kicker") or "")
        self.ink_name = sp.get("color")
        x0, y0, x1, y1 = st.safe
        # the words on top (vertical) or on the left (landscape); the scene takes the rest, as large as it can
        room_w = st.safe_w * (0.94 if st.vertical else 0.42)
        self.rows, self.size = [], 0.0
        if self.textv:
            best = None
            for n in range(1, 4):
                rows = kit.split_lines(st, self.textv, "display", 100, n)
                if not rows:
                    continue
                widths = [st.measure(r, "display", 100) for r in rows]
                limit = st.safe_h * (0.26 if st.vertical else 0.7)
                size = min(100 * room_w / max(widths), limit / (len(rows) * kit.POSTER_LINE), z(150))
                if best is None or size > best[0] * 1.04:
                    best = (size, rows)
            self.size, self.rows = best
        text_h = len(self.rows) * self.size * kit.POSTER_LINE
        if st.vertical:
            self.text_c = (st.center[0], y0 + text_h / 2 + z(10))
            area = (x0, y0 + text_h + z(70), x1, y1)
        else:
            self.text_c = (x0 + room_w / 2, st.center[1])
            area = (x0 + room_w + z(60), y0, x1, y1)
        self.area = area
        aw, ah = area[2] - area[0], area[3] - area[1]
        self.cx, self.cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
        two = self.action in TWO
        self.S = min(aw, ah * 0.86) * (0.78 if two else 1.12)     # an illustration fills about half to 0.9 of its box
        if two:                                    # one above the other (vertical) or side by side (landscape)
            d = (ah if st.vertical else aw) * 0.25
            self.a = (self.cx - aw * 0.12, self.cy - d) if st.vertical else (self.cx - d, self.cy - ah * 0.05)
            self.b = (self.cx + aw * 0.12, self.cy + d) if st.vertical else (self.cx + d, self.cy + ah * 0.05)
        self.block = (st.safe_w, st.safe_h * 0.9)        # it fills the safe zone by construction
        self.settle = ENTER + ACT + 0.25

    # ── helpers ───────────────────────────────────────────────────────────────
    def _p(self, t: float) -> float:
        return motion.progress(t, ENTER, ACT)

    def _alive(self) -> bool:
        return bool(self.st.theme["motion"].get("ambient", True))

    def _hero(self, cv: Canvas, name: str, x: float, y: float, size: float, t: float, active: float = 0.0,
              ground: bool = True) -> None:
        if ground:
            kit.ground(cv, self.st, x, y + size * 0.5, size * 0.8)
        with kit.boiled(cv, self.st, sum(map(ord, name)), t, x, y):
            draw_icon(cv, self.st, name, x, y, size, icon_ink(self.st, name)[0], t, active=active)

    def _color(self, name: str) -> str:
        return icon_ink(self.st, name)[0]

    # ── drawing ───────────────────────────────────────────────────────────────
    def draw(self, cv: Canvas, t: float) -> None:
        st = self.st
        pop = motion.stamp_scale(motion.progress(t, 0.0, 0.35), 0.7)
        kit.glow(cv, st, self.cx, self.cy, self.S * 0.75, self._color(self.subject), t)
        with saved(cv):
            cv.scale(pop, pop, self.cx, self.cy)
            getattr(self, "_" + self.action)(cv, t)
        if self.rows:
            since = t - 0.2
            kit.poster(cv, st, self.rows, *self.text_c, self.size, self.ink_name,
                       scale=motion.stamp_scale(motion.progress(t, 0.0, 0.35)) + 0.03 * motion.shake(since),
                       shake=motion.shake(since))
        if self.kicker:
            kit.tag(cv, st, self.kicker, self.text_c[0], self.area[1] - self.z(30), self.z(36), anchor="center")
        x, y = self.cx - self.S / 2, self.cy - self.S / 2
        kit.sparkles(cv, st, (x, y, self.S, self.S), t, seed=13, size=self.z(30), appear=motion.progress(t, ENTER + ACT * 0.6, 0.3))

    def _swipe(self, cv: Canvas, t: float) -> None:
        """The app leaves the phone: it is pushed up and off to the side, with speed lines, and is gone."""
        S, cx, cy, z = self.S, self.cx, self.cy, self.z
        self._hero(cv, self.target or "phone", cx, cy, S, t)
        p = self._p(t)
        a = motion.action(p, 0.25, 0.08, 1.2)
        size = S * 0.36
        x = cx + a * S * 0.55
        y = cy - a * S * 0.75
        if p < 1:
            slot = round_rect(cx - size / 2, cy - size / 2, size, size, size * 0.24)
            cv.stroke(slot, self.st.theme["colors"]["paper"], self.st.px(4), 0.6 * min(a * 3, 1))
            for k in range(3):                                         # speed lines
                off = (k - 1) * size * 0.28
                tail = (x - S * 0.22 * a, y + S * 0.3 * a + off)
                cv.stroke(poly([tail, (x - size * 0.3, y + size * 0.3 + off)], closed=False),
                          self.st.theme["colors"]["paper"], self.st.px(6), 0.7 * (1 - p))
            with saved(cv):
                cv.rotate(28 * a, x, y)
                draw_icon(cv, self.st, self.subject, x, y, size * (1 - 0.35 * a), self._color(self.subject), t)
        else:                                                          # gone: an empty place that waits, dashes marching
            ring = [(cx - size / 2 + size * 0.5 * (1 + math.cos(math.radians(d))), cy + size * 0.5 * math.sin(math.radians(d)))
                    for d in range(0, 361, 15)]
            kit.rail(cv, self.st, ring, t, color=self.st.theme["colors"]["paper"], width=self.st.px(4), alpha=0.7)
            kit.burst(cv, self.st, cx + S * 0.55, cy - S * 0.75, z(20), z(20), z(34), self._color(self.subject),
                      appear=motion.progress(t, ENTER + ACT, 0.2), t=t, rays=4)

    def _open(self, cv: Canvas, t: float) -> None:
        """The app starts from nothing: it grows out of the phone and a spinner keeps turning (loading)."""
        S, cx, cy = self.S, self.cx, self.cy
        self._hero(cv, self.target or "phone", cx, cy, S, t)
        g = motion.action(self._p(t), 0.15, 0.04, 1.3)
        size = S * (0.12 + 0.32 * max(g, 0))
        draw_icon(cv, self.st, self.subject, cx, cy - S * 0.05, size, self._color(self.subject), t, active=1 - self._p(t))
        r = S * 0.3
        turn = motion.loop(t) * 360 if self._alive() else 90
        arc = [(cx + r * math.cos(math.radians(turn + d)), cy - S * 0.05 + r * math.sin(math.radians(turn + d)))
               for d in range(0, 271, 15)]
        appear = motion.progress(t, ENTER + ACT * 0.5, 0.3)
        if appear > 0:
            cv.stroke(poly(arc, closed=False), self.st.theme["colors"]["ink"], self.st.px(16), appear)
            cv.stroke(poly(arc, closed=False), self.st.theme["colors"]["paper"], self.st.px(10), appear)

    def _sleep(self, cv: Canvas, t: float) -> None:
        """Asleep: it breathes slowly, its light dims, and Zs float up from it."""
        S, cx, cy, z, st = self.S, self.cx, self.cy, self.z, self.st
        breathe = 1 + 0.025 * (motion.wave(t) if self._alive() else 0)
        self._hero(cv, self.subject, cx, cy, S * 0.8 * breathe, t)
        cv.fill(circle(cx, cy, S * 0.42), st.theme["colors"]["ink"], 0.18 * self._p(t))   # the light goes down
        moon = (cx - S * 0.42, cy - S * 0.42)
        shown = motion.progress(t, ENTER + 0.2, 0.4)
        if shown > 0:
            with saved(cv):
                cv.scale(shown, shown, *moon)
                cv.fill(circle(*moon, z(46)), st.theme["colors"]["paper"])
                cv.fill(circle(moon[0] + z(20), moon[1] - z(12), z(40)), st.theme["colors"]["bg"])
        for k in range(3):                                             # Z, z, z: up and away, one after the other
            phase = motion.loop(t, k / 3) if self._alive() else 0.3 + 0.25 * k
            come = motion.progress(t, ENTER + 0.3 * k, 0.3)
            if come <= 0:
                continue
            size = z(70) * (0.6 + 0.5 * phase)
            x = cx + S * 0.28 + phase * S * 0.22 + k * z(6)
            y = cy - S * 0.2 - phase * S * 0.5
            alpha = come * (1 - max(phase - 0.75, 0) * 4)
            if alpha > 0.05:
                kit.text(cv, st, "Z", x, y, "display", size, st.theme["colors"]["paper"], "center", alpha=alpha)

    def _battery(self, cv: Canvas, level: float, color: str, t: float, bolt: bool) -> None:
        S, cx, cy, st = self.S, self.cx, self.cy, self.st
        w, h = S * 0.48, S * 0.82
        body = round_rect(cx - w / 2, cy - h / 2 + S * 0.06, w, h, S * 0.06)
        cap = round_rect(cx - w * 0.2, cy - h / 2 - S * 0.02, w * 0.4, S * 0.09, S * 0.02)
        kit.ground(cv, st, cx, cy + h / 2 + S * 0.08, w * 1.4)
        kit.shape(cv, st, cap, st.theme["colors"]["muted"])
        kit.shape(cv, st, body, st.theme["colors"]["paper"])
        pad = S * 0.045
        inner_h = (h - 2 * pad) * max(min(level, 1.0), 0.0)
        if inner_h > 1:
            cv.fill(round_rect(cx - w / 2 + pad, cy + h / 2 + S * 0.06 - pad - inner_h, w - 2 * pad, inner_h, S * 0.03), color)
        for k in range(1, 4):                                          # the marks of a gauge
            y = cy - h / 2 + S * 0.06 + h * k / 4
            cv.stroke(poly([(cx - w / 2 + pad, y), (cx - w / 2 + pad + w * 0.18, y)], closed=False),
                      st.theme["colors"]["ink"], st.px(3), 0.5)
        if bolt:
            b = [(0.08, -0.2), (-0.12, 0.04), (-0.01, 0.04), (-0.06, 0.24), (0.13, -0.02), (0.02, -0.02)]
            path = poly([(cx + x * S, cy + S * 0.06 + y * S) for x, y in b])
            cv.stroke(path, st.theme["colors"]["ink"], st.px(10))
            cv.fill(path, st.theme["colors"]["paper"])

    def _drain(self, cv: Canvas, t: float) -> None:
        """The battery empties; once low it blinks, and drops of energy keep falling from it."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        p = motion.ease_in_out(self._p(t))
        level = 0.92 - 0.75 * p
        low = level < 0.3
        blink = not self._alive() or motion.loop(t) < 0.6
        color = st.theme["colors"]["alert"] if low and blink else self._color("battery")
        if self.subject and self.subject != "battery":              # who drains it, beside it
            draw_icon(cv, st, self.subject, cx - S * 0.36, cy - S * 0.3, S * 0.3, self._color(self.subject), t)
        self._battery(cv, level, color, t, bolt=False)
        for k in range(3):
            phase = motion.loop(t, k / 3) if self._alive() else 0.4
            if p <= 0:
                break
            x = cx + (k - 1) * S * 0.12
            y = cy + S * 0.5 + phase * S * 0.3
            r = S * 0.03 * (1 - phase)
            if r > 1:
                cv.fill(circle(x, y, r), self._color("battery"), 0.9)

    def _charge(self, cv: Canvas, t: float) -> None:
        """The battery fills, a bolt on it, and sparks pop around."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        p = motion.ease_in_out(self._p(t))
        self._battery(cv, 0.15 + 0.8 * p, self._color("battery"), t, bolt=True)
        kit.burst(cv, st, cx, cy, S * 0.34, S * 0.45, S * 0.12, self._color("battery"),
                  appear=motion.progress(t, ENTER + ACT * 0.8, 0.2), t=t)

    def _between(self, cv: Canvas, t: float) -> list:
        """The two illustrations and the path between them (send / store)."""
        S = self.S
        (ax, ay), (bx, by) = self.a, self.b
        self._hero(cv, self.subject, ax, ay, S, t)
        self._hero(cv, self.target, bx, by, S, t, active=motion.shake(t - ENTER - ACT))
        side = 1 if self.st.vertical else -1
        mid = ((ax + bx) / 2 + side * S * 0.55, (ay + by) / 2 - (0 if self.st.vertical else S * 0.45))
        return smooth_points([(ax + S * 0.3 * (1 if bx > ax else -1) * (0 if self.st.vertical else 1), ay + S * 0.35 * (1 if self.st.vertical else 0)),
                              mid, (bx, by - S * 0.55 if self.st.vertical else by)], tension=0.6)

    def _send(self, cv: Canvas, t: float) -> None:
        """A packet travels from one to the other, again and again."""
        st = self.st
        pts = self._between(cv, t)
        drawn = motion.ease_out(self._p(t))
        kit.thread(cv, st, pts, drawn)
        if drawn >= 1:
            kit.rail(cv, st, pts, t, color=self._color(self.subject), width=st.px(4))
        for k in range(2):
            phase = motion.loop(t, k / 2) if self._alive() else 0.5
            if t < ENTER + ACT * 0.5:
                break
            (x, y), _ = _at(pts, phase)
            w = self.S * 0.22
            card = round_rect(x - w / 2, y - w * 0.32, w, w * 0.64, w * 0.12)
            kit.shape(cv, st, card, st.theme["colors"]["paper"])
            for j in range(2):
                cv.fill(round_rect(x - w * 0.34, y - w * 0.12 + j * w * 0.2, w * (0.55 - 0.2 * j), w * 0.08, w * 0.04),
                        st.theme["colors"]["ink"], 0.5)

    def _store(self, cv: Canvas, t: float) -> None:
        """It drops into the place that keeps it, which lights up and shows a check."""
        st, S = self.st, self.S
        (ax, ay), (bx, by) = self.a, self.b
        self._hero(cv, self.target, bx, by, S, t, active=motion.shake(t - ENTER - ACT))
        p = motion.ease_in_out(self._p(t))
        x, y = ax + (bx - ax) * p, ay + (by - S * 0.2 - ay) * p
        size = S * (0.7 - 0.35 * p)
        if p < 1:
            kit.thread(cv, st, [(ax, ay + S * 0.4), (bx, by - S * 0.55)], 1.0, alpha=0.5)
            draw_icon(cv, st, self.subject, x, y, size, self._color(self.subject), t)
        else:
            r = S * 0.13
            kit.badge(cv, st, bx + S * 0.42, by - S * 0.36, r, st.theme["colors"]["paper"],
                      scale=motion.stamp_scale(motion.progress(t, ENTER + ACT, 0.25), 0.5))
            kit.draw_element("check", cv, st, bx + S * 0.42, by - S * 0.36, t, p=motion.progress(t, ENTER + ACT + 0.05, 0.2),
                             size=r * 1.1)

    def _freeze(self, cv: Canvas, t: float) -> None:
        """Frost grows from its corners, it shakes once and stops; snowflakes keep falling."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        p = self._p(t)
        shake = motion.shake(t - ENTER - ACT * 0.6) * self.z(10)
        self._hero(cv, self.subject, cx + shake, cy, S, t)
        frost = st.theme["colors"]["paper"]
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):         # frost: crystals that grow in from the corners
            r = S * 0.2 * motion.ease_out(p)
            if r < 1:
                continue
            ox, oy = cx + sx * S * 0.23, cy + sy * S * 0.4
            for k, (a, length) in enumerate(((0, 1.0), (30, 0.7), (60, 0.85), (90, 0.6))):
                ang = math.radians(a)
                tip = (ox - sx * r * length * math.cos(ang), oy - sy * r * length * math.sin(ang))
                cv.stroke(poly([(ox, oy), tip], closed=False), st.theme["colors"]["ink"], st.px(9), 0.5)
                cv.stroke(poly([(ox, oy), tip], closed=False), frost, st.px(5), 0.9)
                kit.sparkle(cv, st, *tip, self.z(12), t, seed=k * 0.2 + sx)
        for k in range(5):
            phase = motion.loop(t, k / 5) if self._alive() else 0.2 * k
            if p <= 0:
                break
            x = cx + (k - 2) * S * 0.24 + math.sin(phase * 6.28 + k) * S * 0.03
            y = cy - S * 0.6 + phase * S * 1.2
            kit.sparkle(cv, st, x, y, self.z(18), t, seed=k * 0.3)

    def _clean(self, cv: Canvas, t: float) -> None:
        """A bright sweep passes over it and leaves it shining."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        self._hero(cv, self.subject, cx, cy, S, t)
        p = self._p(t)
        if 0 < p < 1:
            x = cx - S * 0.6 + S * 1.2 * motion.ease_in_out(p)
            band = poly([(x - S * 0.08, cy - S * 0.55), (x + S * 0.08, cy - S * 0.55), (x - S * 0.02, cy + S * 0.55),
                         (x - S * 0.18, cy + S * 0.55)])
            cv.fill(band, st.theme["colors"]["paper"], 0.55)
        kit.sparkles(cv, st, (cx - S * 0.45, cy - S * 0.5, S * 0.9, S), t, seed=21, size=self.z(40),
                     appear=motion.progress(t, ENTER + ACT * 0.7, 0.3))

    def _work(self, cv: Canvas, t: float) -> None:
        """It works hard: it shakes a little, heat waves rise and drops of sweat fly."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        jiggle = (motion.wave(t, 0, 4) - 0.5) * self.z(6) if self._alive() else 0.0
        self._hero(cv, self.subject, cx + jiggle, cy, S * 0.85, t, active=0.5)
        heat = st.theme["colors"]["alert"]
        come = self._p(t)
        for k in range(3):
            phase = motion.loop(t, k / 3) if self._alive() else 0.3
            x = cx + (k - 1) * S * 0.2
            pts = [(x + math.sin((phase * 2 + j / 3) * math.pi) * S * 0.03, cy - S * 0.36 - j * S * 0.03 - phase * S * 0.06)
                   for j in range(5)]
            if come > 0:
                cv.stroke(poly(pts, closed=False), heat, st.px(7), come * (1 - phase * 0.6))
        for side in (-1, 1):
            phase = motion.loop(t, 0.5 if side > 0 else 0.0) if self._alive() else 0.5
            if come > 0.5:
                x, y = cx + side * (S * 0.4 + phase * S * 0.1), cy - S * 0.2 + phase * S * 0.15
                cv.fill(circle(x, y, S * 0.025), st.theme["colors"]["paper"], 1 - phase)

    def _wait(self, cv: Canvas, t: float) -> None:
        """It waits: a clock beside it whose hand keeps going round."""
        st, S, cx, cy, z = self.st, self.S, self.cx, self.cy, self.z
        self._hero(cv, self.subject, cx - S * 0.18, cy + S * 0.1, S * 0.62, t)
        r = S * 0.2
        ox, oy = cx + S * 0.3, cy - S * 0.3
        face = circle(ox, oy, r)
        kit.shape(cv, st, face, st.theme["colors"]["paper"])
        for k in range(12):
            a = math.radians(k * 30)
            cv.stroke(poly([(ox + r * 0.78 * math.cos(a), oy + r * 0.78 * math.sin(a)),
                            (ox + r * 0.9 * math.cos(a), oy + r * 0.9 * math.sin(a))], closed=False),
                      st.theme["colors"]["ink"], st.px(3))
        turn = (motion.loop(t) if self._alive() else 0.2) * 360 + 360 * self._p(t)
        a = math.radians(turn - 90)
        cv.stroke(poly([(ox, oy), (ox + r * 0.75 * math.cos(a), oy + r * 0.75 * math.sin(a))], closed=False),
                  self._color(self.subject), st.px(9))
        cv.stroke(poly([(ox, oy), (ox, oy - r * 0.5)], closed=False), st.theme["colors"]["ink"], st.px(9))
        cv.fill(circle(ox, oy, z(8)), st.theme["colors"]["ink"])

    def _speed(self, cv: Canvas, t: float, up: bool) -> None:
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        self._hero(cv, self.subject, cx - S * 0.14, cy, S * 0.82, t)
        color = self._color(self.subject) if up else st.theme["colors"]["alert"]
        p = motion.action(self._p(t), 0.2, 0.05, 1.3)
        x = cx + S * 0.3
        top, bottom = cy - S * 0.4, cy + S * 0.4
        head = (top + (bottom - top) * (1 - p)) if up else (bottom - (bottom - top) * (1 - p))
        head = min(max(head, top - S * 0.05), bottom + S * 0.05)
        tail = bottom if up else top
        cv.stroke(poly([(x, tail), (x, head)], closed=False), st.theme["colors"]["ink"], st.px(34))
        cv.stroke(poly([(x, tail), (x, head)], closed=False), color, st.px(22))
        d = -1 if up else 1
        tip = poly([(x - S * 0.1, head - d * S * 0.0), (x + S * 0.1, head), (x, head + d * S * 0.12)])
        cv.stroke(tip, st.theme["colors"]["ink"], st.px(10))
        cv.fill(tip, color)
        for k in range(3):                                            # speed (or drag) lines
            phase = motion.loop(t, k / 3) if self._alive() else 0.5
            y = cy - S * 0.2 + k * S * 0.2
            x0 = cx - S * 0.5 - phase * S * 0.1
            cv.stroke(poly([(x0, y), (x0 + S * 0.14, y)], closed=False), st.theme["colors"]["paper"], st.px(6),
                      0.6 * self._p(t))

    def _faster(self, cv: Canvas, t: float) -> None:
        self._speed(cv, t, True)

    def _slower(self, cv: Canvas, t: float) -> None:
        self._speed(cv, t, False)

    def sounds(self):
        return [("whoosh", 0.0), ("pop", ENTER + ACT)]

    def lines(self):
        return [x for x in (self.kicker, self.textv) if x]


def _at(pts, p):
    from .paths import point_at

    return point_at(pts, p)
