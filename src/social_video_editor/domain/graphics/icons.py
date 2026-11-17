"""Illustrations of the things a tech explainer talks about (phone, router, the internet, a server,
a chip…), drawn in the theme's style with their small details and a little life of their own:
lights that blink, waves that go out, a satellite that orbits.

Each one is drawn in a 100 × 100 box centered on the origin through a Pen; draw_icon places it.
`find_icon("Celular")` gives the illustration a word asks for (Spanish and English), so a diagram
written with plain names gets pictures instead of boxes; `describe_icon` says what each one shows (for
the decider, domain/decide.py). Packs add or replace illustrations:

    from social_video_editor.domain.graphics.icons import icon

    @icon("printer", words=("impresora", "printer"))
    def printer(pen): ...
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable

from . import motion
from .canvas import Canvas, saved
from .paths import Path, circle, oval, poly, rect, round_rect, smooth
from .scene import Stage
from .theme import ink as resolve_ink

ICONS: dict[str, Callable] = {}
WORDS: dict[str, str] = {}             # normalized word → icon name
ALONE = {"tu", "yo", "you", "la", "el"}  # words that only name an illustration when they are the whole label
PUNCTUATION = "¿?¡!.,;:«»\"'()…“”‘’-"


def icon(name: str, words: tuple[str, ...] = (), named: bool = True):
    """Registers an illustration and the words that select it (`named`: its own name is one of them;
    False for a name that is also a common word in another sense, like "tap" or "key")."""
    def register(fn: Callable) -> Callable:
        ICONS[name] = fn
        for w in ((name,) if named else ()) + tuple(words):
            WORDS[_norm(w)] = name
        return fn
    return register


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").strip()


def _tokens(text: str) -> list[str]:
    """The words of a label, normalized, without the punctuation around them ("¿Celular?" → celular;
    "wi-fi" stays one word, then is tried as two)."""
    return [t for t in re.split(r"[^0-9a-zñ'’]+", text.replace("’", "'")) if t.strip("'")]


def _forms(word: str) -> list[str]:
    """A word and the singulars it may be the plural of (Spanish and English): servidores → servidor,
    luces → luz, batteries → battery, apps → app. Only whole words are ever compared."""
    out = [word]
    if len(word) > 3 and word.endswith("ces"):
        out.append(word[:-3] + "z")
    if len(word) > 4 and word.endswith("ies"):
        out.append(word[:-3] + "y")
    if len(word) > 3 and word.endswith("es"):
        out.append(word[:-2])
    if len(word) > 2 and word.endswith("s"):
        out.append(word[:-1])
    return out


def find_icon(label: str, among=None) -> str | None:
    """The illustration for a label: the label itself or the first of its words (or two- and three-word
    names: "hora pico", "disco duro") that names one ("Tu celular" → phone). Whole words only, in their
    plural too; a word that only starts like a name is not it ("cargando" is not "carga"), and a label
    with no such word has no illustration (None). `among`: the illustrations allowed (default: all)."""
    def ok(name: str | None) -> str | None:
        return name if name is not None and (among is None or name in among) else None

    whole = _norm(label).strip(PUNCTUATION)
    if whole in WORDS and ok(WORDS[whole]):
        return WORDS[whole]
    words = _tokens(_norm(label))
    for i, word in enumerate(words):
        for n in (3, 2):                                   # "llave de agua", "hora pico", "torres celulares"
            if len(words) - i >= n:
                head = " ".join(words[i:i + n - 1])
                for form in _forms(words[i + n - 1]):
                    if (found := ok(WORDS.get(f"{head} {form}"))) is not None:
                        return found
        if any(f in ALONE for f in _forms(word)):          # "tu celular", "tus fotos": a phone, photos, not a person
            continue
        for form in _forms(word):
            if (found := ok(WORDS.get(form))) is not None:
                return found
    return None


DESCRIPTIONS = {        # icon → what it shows, for a model that picks one (Spanish, English); packs may add theirs
    "phone": ("un teléfono celular", "a mobile phone"),
    "router": ("un router de wifi", "a wifi router"),
    "globe": ("internet o el mundo", "the internet or the world"),
    "cloud": ("la nube (servicios en internet)", "the cloud (online services)"),
    "server": ("un servidor", "a server"),
    "laptop": ("una computadora", "a computer"),
    "chip": ("un procesador", "a processor chip"),
    "ram": ("la memoria RAM", "memory (RAM)"),
    "disk": ("el almacenamiento o un disco", "storage or a disk"),
    "battery": ("una batería o la energía", "a battery or energy"),
    "tower": ("una antena de telefonía", "a cell tower"),
    "app": ("una aplicación", "an app"),
    "user": ("una persona", "a person"),
    "lock": ("un candado o la seguridad", "a lock or security"),
    "signal": ("las rayitas de señal del celular", "the signal bars of a phone"),
    "building": ("un edificio de departamentos u oficinas", "an apartment or office building"),
    "shower": ("una ducha", "a shower"),
    "tap": ("un grifo o el agua", "a tap or water"),
    "clock": ("un reloj, la hora o el tiempo", "a clock, the time of day or time"),
    "moon": ("la luna o la noche", "the moon or the night"),
    "download": ("una descarga", "a download"),
    "gauge": ("un velocímetro o la velocidad", "a speedometer or speed"),
    "crowd": ("mucha gente o los usuarios", "many people or the users"),
    "video": ("un video que se reproduce o carga", "a video playing or loading"),
    "cable": ("un cable o un enchufe", "a cable or a plug"),
    "satellite": ("un satélite", "a satellite"),
    "keyboard": ("un teclado", "a keyboard"),
    "shield": ("un escudo o la protección", "a shield or protection"),
    "key": ("una llave o una clave", "a key"),
    "bug": ("un bicho: un error o un virus", "a bug: an error or a virus"),
    "folder": ("una carpeta", "a folder"),
    "file": ("un archivo o un documento", "a file or a document"),
    "photo": ("una foto", "a photo"),
    "camera": ("una cámara", "a camera"),
    "mic": ("un micrófono o la voz", "a microphone or the voice"),
    "speaker": ("un parlante o el sonido", "a speaker or sound"),
    "message": ("un mensaje o un chat", "a message or a chat"),
    "bell": ("una campana: una notificación", "a bell: a notification"),
    "charger": ("un cargador", "a charger"),
    "trash": ("un bote de basura", "a trash can"),
    "search": ("una lupa: buscar", "a magnifier: searching"),
    "gear": ("un engranaje: los ajustes", "a gear: the settings"),
    "money": ("dinero o un precio", "money or a price"),
    "calendar": ("un calendario o una fecha", "a calendar or a date"),
    "pin": ("un mapa o un lugar", "a map or a place"),
    "rocket": ("un cohete", "a rocket"),
    "idea": ("una idea o un consejo", "an idea or a tip"),
    "warning": ("una alerta o un peligro", "a warning or a danger"),
}
NO_ICON = ("ninguna de estas cosas", "none of these things")


def describe_icon(name: str, language: str = "es") -> str:
    """A short description of an illustration ("none" too) in the video's language; its name when it has none."""
    pair = NO_ICON if name == "none" else DESCRIPTIONS.get(name, (name, name))
    return pair[0] if language == "es" else pair[1]


def match_icon(label: str) -> tuple[str, bool] | None:
    """find_icon with how it matched: (icon, exact) — exact when the label (one word) is a name of the
    illustration as it is, not a plural or a longer label around it."""
    found = find_icon(label)
    if found is None:
        return None
    return found, _norm(label).strip(PUNCTUATION) in WORDS


def icon_ink(st: Stage, name: str, given: str | None = None) -> tuple[str, str | None]:
    """(color, ink name) an illustration is drawn with: the one given, else the theme's ink for it
    (theme icon_inks), else the accent."""
    return resolve_ink(st.theme, given or st.theme.get("icon_inks", {}).get(name))


class Pen:
    """Draws the parts of an illustration in the theme's style. Units are those of the 100-box."""

    def __init__(self, cv: Canvas, st: Stage, k: float, color: str, t: float = 0.0, active: float = 0.0):
        c = st.theme["colors"]
        self.cv, self.st, self.k, self.color, self.t, self.active = cv, st, k, color, t, active
        self.ink, self.paper, self.dark, self.metal, self.muted = c["ink"], c["paper"], c["device"], c["metal"], c["muted"]
        self.alert, self.skin = c["alert"], c.get("skin", "#F2D9BF")
        self.line = max(st.px(float(st.theme["stroke"]["outline"])), st.px(3.2)) / k
        self.halo = st.px(float(st.theme["stroke"]["halo"])) / k
        self.alive = bool(st.theme["motion"].get("ambient", True))

    def outline(self, *paths: Path) -> None:
        """The silhouette: a light halo (so dark things read on a dark stage) and the ink contour.
        Draw it first, then the fills on top."""
        if self.halo > 0:
            for p in paths:
                self.cv.stroke(p, self.paper, 2 * self.line + 2 * self.halo)
        for p in paths:
            self.cv.stroke(p, self.ink, 2 * self.line)

    def fill(self, path: Path, color: str, alpha: float = 1.0) -> None:
        self.cv.fill(path, color, alpha)

    def part(self, path: Path, color: str, alpha: float = 1.0, thin: bool = True) -> None:
        """An inner part: fill with a thin ink edge."""
        self.cv.fill(path, color, alpha)
        self.cv.stroke(path, self.ink, self.line * (0.55 if thin else 1.0))

    def stroke(self, path: Path, color: str | None = None, width: float | None = None, alpha: float = 1.0,
               cap: str = "round") -> None:
        self.cv.stroke(path, color or self.ink, width or self.line, alpha, cap=cap)

    def cable(self, path: Path, width: float = 5.0, color: str | None = None) -> None:
        """A thick line with an ink edge (antennas, shackles, masts)."""
        if self.halo > 0:
            self.cv.stroke(path, self.paper, width + 2 * self.line + 2 * self.halo)
        self.cv.stroke(path, self.ink, width + 2 * self.line)
        self.cv.stroke(path, color or self.dark, width)

    def shade(self, path: Path, alpha: float = 0.5) -> None:
        """Dotted shading inside a part."""
        h = self.st.theme["halftone"]
        self.cv.halftone(path, self.ink, max(self.st.px(h["cell"]) / self.k, 2.2), h["angle"],
                         self.st.px(float(h["radius"])) / self.k * 0.8, alpha)

    def on(self, offset: float = 0.0, duty: float = 0.5) -> bool:
        """A light that blinks, out of step by `offset`."""
        return not self.alive or motion.loop(self.t, offset) < duty

    def phase(self, offset: float = 0.0) -> float:
        return motion.loop(self.t, offset) if self.alive else 0.75

    def led(self, x: float, y: float, r: float = 3.2, offset: float = 0.0, color: str | None = None) -> None:
        lit = self.on(offset) or self.active > 0.2
        self.cv.fill(circle(x, y, r + self.line * 0.5), self.ink)
        self.cv.fill(circle(x, y, r), color or self.color, 1.0 if lit else 0.28)

    def flash(self, path: Path) -> None:
        """What lights up when the thing is in use (a marker arrives, it is the one being talked about)."""
        if self.active > 0.02:
            self.cv.fill(path, self.paper, min(0.45 * self.active, 0.45))


def draw_icon(cv: Canvas, st: Stage, name: str, cx: float, cy: float, size: float, color: str | None = None,
              t: float = 0.0, active: float = 0.0) -> None:
    """Illustration `name` centered on (cx, cy), `size` pixels across."""
    k = size / 100
    with saved(cv):
        cv.translate(cx, cy)
        cv.scale(k)
        ICONS[name](Pen(cv, st, k, color or icon_ink(st, name)[0], t, active))


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 10) -> Path:
    pts = [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
           for i in range(n + 1)]
    return poly(pts, closed=False)


# ── the illustrations ────────────────────────────────────────────────────────
@icon("phone", words=("celular", "telefono", "movil", "smartphone", "iphone", "android", "cel"))
def phone(pen: Pen) -> None:
    body = round_rect(-25, -46, 50, 92, 9)
    buttons = [rect(24, -24, 3.5, 13), rect(-27.5, -18, 3.5, 8), rect(-27.5, -7, 3.5, 8)]
    pen.outline(body, *buttons)
    for b in buttons:
        pen.fill(b, pen.dark)
    pen.fill(body, pen.dark)
    screen = round_rect(-21, -41.5, 42, 83, 6)
    pen.part(screen, pen.color)
    pen.fill(round_rect(-7, -38.5, 14, 4, 2), pen.dark)                    # camera pill
    for row in range(2):                                                    # app icons
        for col in range(3):
            pen.fill(round_rect(-16.5 + col * 12, -29 + row * 12, 9, 9, 2.5), pen.paper, 0.92)
    pen.fill(round_rect(-16.5, -3, 33, 17, 3), pen.paper, 0.92)             # a card with its lines
    pen.fill(rect(-13, 1, 18, 2.6), pen.ink, 0.55)
    pen.fill(rect(-13, 6.5, 26, 2.6), pen.ink, 0.35)
    pen.fill(round_rect(-16.5, 18, 15, 9, 2.5), pen.paper, 0.6)
    pen.fill(round_rect(1.5, 18, 15, 9, 2.5), pen.paper, 0.6)
    pen.fill(round_rect(-8, 35, 16, 2.4, 1.2), pen.paper, 0.85)             # home bar
    pen.flash(screen)
    if pen.on(0.15, 0.6):                                                   # a notification
        pen.fill(circle(15.5, -31, 4.6), pen.ink)
        pen.fill(circle(15.5, -31, 3.4), pen.alert)


@icon("router", words=("wifi", "wi-fi", "wi fi", "modem", "enrutador", "routers", "modems"))
def router(pen: Pen) -> None:
    body = round_rect(-42, 12, 84, 27, 7)
    left = poly([(-30, 14), (-37, -32)], closed=False)
    right = poly([(30, 14), (37, -32)], closed=False)
    pen.cable(left, 5)
    pen.cable(right, 5)
    for x in (-37, 37):
        pen.part(circle(x, -33, 4.4), pen.paper)
    feet = [round_rect(-34, 37, 12, 6, 2), round_rect(22, 37, 12, 6, 2)]
    pen.outline(body, *feet)
    for f in feet:
        pen.fill(f, pen.dark)
    pen.fill(body, pen.paper)
    pen.shade(rect(-42, 30, 84, 9), 0.35)
    pen.fill(rect(-36, 18, 46, 2.2), pen.ink, 0.3)
    for i in range(4):
        pen.led(-30 + i * 11, 27, 3.2, offset=i * 0.23)
    pen.part(round_rect(18, 21, 17, 10, 2), pen.dark)                      # port
    pen.fill(rect(21, 24, 11, 3), pen.metal)
    shown = 3 if pen.active > 0.2 else int(pen.phase() * 4)                 # the signal goes out, arc by arc
    for i in range(min(shown, 3)):
        pen.stroke(_arc(0, 6, 12 + i * 10, 218, 322), pen.color, 4.6)
    pen.fill(circle(0, 5, 3.6), pen.color)


@icon("globe", words=("internet", "web", "network", "world", "online"))
def globe(pen: Pen) -> None:
    ball = circle(0, 0, 38)
    pen.outline(ball)
    pen.fill(ball, pen.color)
    pen.shade(_lune(38), 0.4)
    for path in (oval(0, 0, 15, 38), poly([(-38, 0), (38, 0)], closed=False),
                 smooth([(-33.5, -18), (0, -13), (33.5, -18)], closed=False, tension=0.9),
                 smooth([(-33.5, 18), (0, 13), (33.5, 18)], closed=False, tension=0.9),
                 poly([(0, -38), (0, 38)], closed=False)):
        pen.stroke(path, pen.paper, 2.4, 0.9)
    pen.flash(ball)
    pen.stroke(_arc(0, 0, 31, 200, 250), pen.paper, 3.2)                    # a shine
    a = 2 * math.pi * pen.phase()                                           # something in orbit
    orbit = oval(0, 0, 49, 15)
    pen.stroke(orbit, pen.paper, 1.6, 0.45)
    x, y = 49 * math.cos(a), 15 * math.sin(a)
    pen.fill(circle(x, y, 6.2), pen.ink)
    pen.fill(circle(x, y, 4.6), pen.paper)


def _lune(r: float) -> Path:
    """The shaded side of a ball (lower right)."""
    return smooth([(r * 0.2, r * 0.97), (r * 0.78, r * 0.6), (r * 0.98, -r * 0.1), (r * 0.62, r * 0.28), (r * 0.36, r * 0.62)])


@icon("cloud", words=("nube", "icloud"))
def cloud(pen: Pen) -> None:
    parts = [circle(-20, 6, 18), circle(2, -9, 24), circle(25, 6, 16), round_rect(-38, 6, 79, 20, 10)]
    pen.outline(*parts)
    for p in parts:
        pen.fill(p, pen.paper)
    pen.shade(round_rect(-34, 16, 70, 10, 5), 0.3)
    up = poly([(-12, 16), (-12, 2), (-17, 2), (-8, -8), (1, 2), (-4, 2), (-4, 16)])
    down = poly([(8, -4), (8, 10), (3, 10), (12, 20), (21, 10), (16, 10), (16, -4)])
    lift = 2.5 * math.sin(2 * math.pi * pen.phase())
    pen.part(up.moved(0, -lift), pen.color)
    pen.part(down.moved(0, lift), pen.color)


@icon("server", words=("servidor", "servidores", "datacenter", "backend"))
def server(pen: Pen) -> None:
    body = round_rect(-30, -44, 60, 88, 6)
    pen.outline(body)
    pen.fill(body, pen.dark)
    for i in range(3):
        y = -38 + i * 26
        pen.part(round_rect(-24, y, 48, 20, 3), pen.paper)
        pen.led(-16, y + 10, 3.2, offset=i * 0.31)
        pen.fill(rect(-8, y + 5.5, 26, 2.6), pen.ink, 0.5)
        pen.fill(rect(-8, y + 11.5, 17, 2.6), pen.ink, 0.3)
    for i in range(5):                                                       # vents
        pen.fill(rect(-20 + i * 9, 38.5, 5, 2), pen.paper, 0.45)
    pen.flash(body)


@icon("laptop", words=("pc", "computador", "computadora", "portatil", "mac", "ordenador", "computer"))
def laptop(pen: Pen) -> None:
    lid = round_rect(-36, -36, 72, 50, 5)
    base = poly([(-37, 15), (37, 15), (48, 29), (-48, 29)])
    pen.outline(lid, base)
    pen.fill(lid, pen.dark)
    pen.fill(base, pen.paper)
    screen = round_rect(-31, -31, 62, 39, 2.5)
    pen.part(screen, pen.color)
    pen.fill(rect(-26, -26, 20, 2.6), pen.paper, 0.9)
    pen.fill(rect(-26, -20, 30, 2.6), pen.paper, 0.6)
    for i, h in enumerate((7, 12, 9, 16)):                                   # a little chart
        grow = h * (0.7 + 0.3 * math.sin(2 * math.pi * pen.phase(i * 0.2)))
        pen.fill(rect(-2 + i * 7.5, 4 - grow, 5, grow), pen.paper, 0.92)
    pen.flash(screen)
    pen.fill(rect(-9, 24, 18, 2.4), pen.ink, 0.4)
    pen.stroke(poly([(-40, 20), (40, 20)], closed=False), pen.ink, pen.line * 0.5, 0.5)
    pen.shade(poly([(-44, 24), (44, 24), (48, 29), (-48, 29)]), 0.35)


@icon("chip", words=("cpu", "procesador", "processor", "gpu", "soc"))
def chip(pen: Pen) -> None:
    body = round_rect(-30, -30, 60, 60, 6)
    pins = []
    for i in range(5):
        o = -22 + i * 11
        pins += [rect(o - 2.5, -41, 5, 11), rect(o - 2.5, 30, 5, 11), rect(-41, o - 2.5, 11, 5), rect(30, o - 2.5, 11, 5)]
    pen.outline(body, *pins)
    for p in pins:
        pen.fill(p, pen.metal)
    pen.fill(body, pen.dark)
    core = round_rect(-18, -18, 36, 36, 3)
    pen.part(core, pen.color)
    pen.shade(poly([(-18, 18), (18, -18), (18, 18)]), 0.3)
    pen.stroke(poly([(-10, -6), (10, -6)], closed=False), pen.ink, 2.4, 0.55)
    pen.stroke(poly([(-10, 2), (4, 2)], closed=False), pen.ink, 2.4, 0.4)
    pen.flash(core)
    pen.fill(circle(-23, -23, 2.6), pen.paper, 0.8)
    pen.led(23, 23, 2.8, offset=0.4)


@icon("ram", words=("memoria", "memory", "ddr"))
def ram(pen: Pen) -> None:
    board = round_rect(-47, -19, 94, 33, 3)
    pins = rect(-43, 14, 86, 8)
    pen.outline(board, pins)
    pen.fill(pins, pen.metal)
    for i in range(1, 17):
        pen.stroke(poly([(-43 + i * 5.06, 14.5), (-43 + i * 5.06, 22)], closed=False), pen.ink, 1.2, 0.6, cap="butt")
    pen.fill(rect(-5, 14, 5, 8.5), pen.ink)                                  # the notch
    pen.fill(board, pen.color)
    pen.shade(rect(-47, 6, 94, 8), 0.3)
    for i in range(4):
        x = -40 + i * 21.5
        pen.part(round_rect(x, -13, 16, 16, 2), pen.dark)
        pen.fill(circle(x + 3.5, -9.5, 1.5), pen.paper, 0.8)
        pen.fill(rect(x + 3, -2.5, 10, 1.8), pen.paper, 0.35)
    for x in (-44, 44):
        pen.fill(circle(x, -15, 1.8), pen.ink)
    pen.flash(board)


@icon("disk", words=("disco", "ssd", "almacenamiento", "storage", "hdd", "disco duro", "hard drive", "hard disk", "disk"))
def disk(pen: Pen) -> None:
    body = round_rect(-37, -29, 74, 55, 6)
    plug = rect(-22, 26, 44, 7)
    pen.outline(body, plug)
    pen.fill(plug, pen.metal)
    for i in range(1, 9):
        pen.stroke(poly([(-22 + i * 4.9, 26.5), (-22 + i * 4.9, 33)], closed=False), pen.ink, 1.2, 0.6, cap="butt")
    pen.fill(body, pen.dark)
    label = round_rect(-29, -21, 58, 30, 3)
    pen.part(label, pen.paper)
    pen.fill(rect(-29, -21, 58, 8.5), pen.color)
    pen.fill(rect(-24, -8, 30, 2.6), pen.ink, 0.5)
    pen.fill(rect(-24, -2, 20, 2.6), pen.ink, 0.3)
    pen.fill(round_rect(13, -9, 12, 12, 2), pen.ink, 0.55)                   # a code square on the label
    for x, y in ((-31, -23.5), (31, -23.5), (-31, 20.5), (31, 20.5)):
        pen.fill(circle(x, y, 1.9), pen.paper, 0.7)
    pen.led(-27, 17, 2.8, offset=0.1)
    pen.flash(label)


@icon("battery", words=("bateria", "battery", "batteries"))
def battery(pen: Pen) -> None:
    body = round_rect(-24, -36, 48, 78, 8)
    cap = round_rect(-10, -45, 20, 12, 3)
    pen.outline(body, cap)
    pen.fill(cap, pen.muted)
    pen.fill(body, pen.paper)
    level = 0.35 + 0.5 * pen.phase()                                          # it charges
    h = 66 * level
    pen.fill(round_rect(-18, 36 - h, 36, h, 4), pen.color)
    pen.shade(round_rect(-18, max(36 - h, 24), 36, min(h, 12), 4), 0.3)
    bolt = poly([(5, -17), (-11, 5), (-1, 5), (-5, 22), (12, -1), (2, -1)])
    pen.stroke(bolt, pen.paper, pen.line * 2.2)
    pen.part(bolt, pen.dark, thin=False)


@icon("tower", words=("antena", "antenas", "torre", "torres", "operador", "operadora", "4g", "5g", "antenna", "cell tower",
                       "torre celular", "torre de celular"))
def tower(pen: Pen) -> None:
    mast = poly([(0, -30), (-21, 44), (-12, 44), (0, 0), (12, 44), (21, 44)])
    base = round_rect(-30, 40, 60, 7, 3)
    pen.outline(mast, base)
    pen.fill(base, pen.muted)
    pen.fill(mast, pen.dark)
    for y, half in ((-6, 5.5), (12, 10.5), (29, 15.5)):
        pen.stroke(poly([(-half, y), (half, y)], closed=False), pen.paper, 2.6, 0.85)
    pen.stroke(poly([(-5.5, -6), (10.5, 12), (-15.5, 29)], closed=False), pen.paper, 1.8, 0.55)
    shown = 3 if pen.active > 0.2 else int(pen.phase() * 4)                  # the signal goes out, wave by wave
    for i in range(min(shown, 3)):
        r = 13 + i * 10
        pen.stroke(_arc(0, -33, r, -42, 42), pen.color, 4.6)
        pen.stroke(_arc(0, -33, r, 138, 222), pen.color, 4.6)
    pen.part(circle(0, -33, 6), pen.color, thin=False)


@icon("app", words=("aplicacion", "apps", "aplicaciones", "software"))
def app(pen: Pen) -> None:
    card = round_rect(-32, -32, 64, 64, 15)
    pen.outline(card)
    pen.fill(card, pen.color)
    pen.shade(poly([(-32, 32), (32, -6), (32, 32)]), 0.25)
    pen.fill(circle(-11, -9, 9.5), pen.paper)
    pen.fill(poly([(-22, 22), (-6, 2), (4, 12), (12, 5), (24, 22)]), pen.paper, 0.92)
    pen.flash(card)
    r = 9.5 * (0.9 + 0.1 * math.sin(2 * math.pi * pen.phase()))
    pen.fill(circle(27, -27, r + pen.line), pen.ink)
    pen.fill(circle(27, -27, r), pen.alert)


@icon("user", words=("tu", "usuario", "persona", "you", "cliente", "yo", "user", "person", "customer"))
def user(pen: Pen) -> None:
    shoulders = smooth([(-37, 44), (-33, 20), (-13, 8), (13, 8), (33, 20), (37, 44)], tension=0.45)
    head = circle(0, -15, 20)
    neck = rect(-7, 0, 14, 12)
    pen.outline(shoulders, head)
    pen.fill(neck, pen.skin)
    pen.fill(shoulders, pen.dark)
    pen.stroke(smooth([(-9, 9), (0, 16), (9, 9)], closed=False), pen.paper, 2.2, 0.7)      # collar
    pen.fill(head, pen.skin)
    hair = smooth([(-21, -12), (-20, -30), (-4, -39), (15, -34), (21, -14), (14, -24), (-2, -27), (-14, -21)], tension=0.5)
    pen.part(hair, pen.ink, thin=False)
    look = 1.2 * math.sin(2 * math.pi * pen.phase())
    for x in (-7, 7):
        pen.fill(circle(x + look, -12, 2.3), pen.ink)
    pen.stroke(smooth([(-6, -3), (0, 0.5), (6, -3)], closed=False), pen.ink, 2)
    pen.fill(circle(-12, -6, 3), pen.alert, 0.3)
    pen.fill(circle(12, -6, 3), pen.alert, 0.3)


@icon("lock", words=("candado", "seguridad", "contrasena", "contrasenas", "password", "passwords", "cifrado", "privacidad",
                      "security", "privacy", "encryption", "padlock"))
def lock(pen: Pen) -> None:
    shackle = poly([(-17, -4), (-17, -22)], closed=False)
    arc = _arc(0, -22, 17, 180, 360, 14)
    for part in (shackle, poly([(17, -4), (17, -22)], closed=False), arc):
        pen.stroke(part, pen.paper, 7 + 2 * pen.line + 2 * pen.halo) if pen.halo > 0 else None
    for part in (shackle, poly([(17, -4), (17, -22)], closed=False), arc):
        pen.stroke(part, pen.ink, 7 + 2 * pen.line)
    for part in (shackle, poly([(17, -4), (17, -22)], closed=False), arc):
        pen.stroke(part, pen.muted, 7)
    body = round_rect(-27, -9, 54, 48, 8)
    pen.outline(body)
    pen.fill(body, pen.color)
    pen.shade(round_rect(-27, 25, 54, 14, 6), 0.3)
    pen.fill(circle(0, 10, 6.5), pen.ink)
    pen.fill(poly([(-3, 12), (3, 12), (4.5, 26), (-4.5, 26)]), pen.ink)
    pen.flash(body)


from . import icons_extra  # noqa: E402,F401  (registers the rest of the illustrations)
