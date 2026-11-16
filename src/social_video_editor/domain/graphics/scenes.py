"""Illustrated scenes: a short animation of the thing a sentence talks about doing what the sentence
says — an app swiped away, a battery that drains, an app asleep, something stored in memory, a
message that travels — drawn by the engine when no footage shows it well.

    scene action="swipe" subject="app" text="La cierras y empieza de cero"

action: swipe · open · sleep · drain · charge · send · store · freeze · clean · work · wait · faster · slower
subject: the illustration that acts (phone, app, battery, ram…; a word naming one works too)
target: a second illustration for send / store (where it goes)
text: what is said, as a short poster above the scene (optional) · kicker · color

`action_of(words)` reads the action from the verbs of a sentence (Spanish and English), so the
director can propose one; `actions_in` / `action_text` give its candidates and their descriptions for
the decider (domain/decide.py). Every scene: the subject large on a glow, the action played once (about a
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
from .paths import circle, cut, poly, rect, round_rect, smooth_points
from .scene import Scene, template

DEFAULTS = {         # action → (default subject, default target)
    "swipe": ("app", "phone"), "open": ("app", "phone"), "sleep": ("app", None), "drain": ("battery", None),
    "charge": ("battery", None), "send": ("phone", "globe"), "store": ("app", "disk"), "freeze": ("phone", None),
    "clean": ("phone", None), "work": ("chip", None), "wait": ("app", None), "faster": ("phone", None),
    "slower": ("phone", None), "compare": ("phone", "laptop"), "search": ("globe", None), "connect": ("phone", "router"),
    "delete": ("file", "trash"), "download": ("cloud", "phone"), "share": ("tower", "user"), "queue": ("tower", None),
}
# What a sentence says to ask for each action (accents removed). Spanish verb stems match only with a verb
# ending after them ("cierr" → cierras, ciérrala, but not a noun or an adjective that starts the same:
# "abr" is not "abril", "trab" is not "trabajo"); English verbs match in their forms (close, closes,
# closed, closing); the last tuple holds words matched as they are (adjectives, nouns, irregular forms).
SAID = {
    "swipe": (("cierr", "cerr", "desliz", "quit"), ("close", "swipe", "kill", "dismiss"), ()),
    "open": (("abr", "lanz", "lanc", "arranc", "arranqu", "empiez", "empez", "empec", "inici", "reabr"),
             ("open", "launch", "start", "reopen", "relaunch"), ()),
    "sleep": (("dorm", "duerm", "durm", "descans", "paus", "suspend", "hibern"),
              ("sleep", "nap", "pause", "suspend", "hibernate"), ("asleep", "idle", "slept", "reposo", "inactiva",
                                                                  "inactivas", "inactivo", "inactivos", "inactive")),
    "drain": (("gast", "consum", "agot", "chup", "dren"), ("drain", "waste", "consume", "deplete"), ()),
    "charge": (("carg", "cargu", "recarg", "recargu", "ahorr"), ("charge", "recharge", "save"), ()),
    "send": (("envi", "mand", "viaj", "lleg", "llegu", "transmit"), ("send", "travel", "arrive", "transmit", "deliver"),
             ("sent",)),
    "store": (("guard", "almacen"), ("store", "stash"), ("bodega", "bodegas", "meter", "meterla", "meterlo", "meterlas",
                                                          "meterlos", "mete", "meten", "metes")),
    "freeze": (("congel", "trab", "cuelg", "colg", "bloque"), ("freeze", "hang", "crash"), ("froze", "frozen", "hung", "stuck")),
    "clean": (("limpi", "liber", "despej", "vaci"), ("clean", "clear", "tidy", "declutter"), ()),
    "work": (("trabaj", "esforz", "esfuerz", "proces", "calient", "calent"),
             ("work", "process", "heat", "compute", "crunch", "overheat"), ()),
    "wait": (("esper", "tard", "demor"), ("wait", "delay"), ()),
    "faster": (("aceler",), ("accelerate", "speed", "boost"),
               ("rapido", "rapida", "rapidos", "rapidas", "rapidamente", "rapidez", "agil", "agiles", "veloz", "veloces",
                "fast", "faster", "fastest", "quick", "quicker", "quickly")),
    "slower": (("ralentiz",), ("slow", "lag"), ("lento", "lenta", "lentos", "lentas", "lentamente", "lentitud", "pesado",
                                                "pesada", "pesados", "pesadas", "laggy", "sluggish", "slower", "slowly")),
    "compare": (("compar",), ("compare", "contrast"), ("versus", "vs", "comparacion", "comparison")),
    "search": (("busc", "busqu", "encontr", "encuentr", "googl"), ("search", "find", "seek", "google"), ("found",)),
    "connect": (("conect", "enlaz", "enlac", "vincul", "enchuf"), ("connect", "link", "pair", "plug"), ("conexion",
                                                                                                    "connection")),
    "delete": (("borr", "elimin", "suprim", "desinstal"), ("delete", "erase", "remove", "uninstall", "wipe"), ()),
    "download": (("descarg", "descargu"), ("download",),
                 ("bajar", "bajarla", "bajarlo", "bajarlas", "bajarlos", "bajando", "bajala", "bajalo", "bajalas",
                  "bajalos", "bajate", "bajarte")),
    "share": (("compart", "repart", "divid"), ("share", "split", "divide"), ()),
    "queue": (("atasc", "atasqu", "embotell", "satur", "congestion"), ("queue", "jam", "congest"),
              ("fila", "filas", "cola", "colas", "turno", "turnos", "trafico", "traffic", "congestion", "bottleneck",
               "bottlenecks", "embotellamiento", "atasco", "atascos", "queueing", "saturacion")),
}
ACTIONS = {name: (*DEFAULTS[name], tuple(w for group in SAID[name] for w in group)) for name in DEFAULTS}
TWO = ("send", "store", "connect", "delete", "download")      # actions between two illustrations placed apart
TARGETED = (*TWO, "swipe", "open", "compare", "share", "queue")  # actions that take a target
ENTER = 0.25                               # the stage comes in, then the action
ACT = 1.2                                  # seconds the action takes


def _plain(word: str) -> str:
    w = unicodedata.normalize("NFKD", str(word).lower())
    return "".join(c for c in w if not unicodedata.combining(c)).strip("¿?¡!.,;:«»\"'()…“”‘’")


ACTION_TEXTS = {     # action → what a scene shows, said for a model that picks one (Spanish, English)
    "swipe": ("cerrar o quitar algo", "closing or removing something"),
    "open": ("abrir o empezar algo", "opening or starting something"),
    "sleep": ("algo que duerme o descansa", "something asleep or resting"),
    "drain": ("gastar o agotar energía", "using up energy"),
    "charge": ("cargar o ahorrar energía", "charging or saving energy"),
    "send": ("enviar algo o viajar de un lugar a otro", "sending something or travelling from one place to another"),
    "store": ("guardar algo en un lugar", "storing something somewhere"),
    "freeze": ("algo que se congela o se traba", "something that freezes or gets stuck"),
    "clean": ("limpiar o liberar espacio", "cleaning or freeing up space"),
    "work": ("trabajar duro o procesar", "working hard or processing"),
    "wait": ("esperar o tardar", "waiting or taking a long time"),
    "faster": ("ir más rápido", "going faster"),
    "slower": ("ir más lento", "going slower"),
    "compare": ("comparar dos cosas", "comparing two things"),
    "search": ("buscar o encontrar algo", "searching for or finding something"),
    "connect": ("conectar dos cosas", "connecting two things"),
    "delete": ("borrar o eliminar algo", "deleting or erasing something"),
    "download": ("descargar algo", "downloading something"),
    "share": ("repartir o compartir entre muchos", "sharing or splitting among many"),
    "queue": ("hacer fila: un atasco", "waiting in a queue: a traffic jam"),
}
NO_ACTION = ("ninguna de estas acciones", "none of these actions")
ENDINGS_EN = ("", "s", "es", "ed", "d", "ing", "e")      # kept for packs that read them
INFINITIVE_ES = ("ar", "er", "ir", "arla", "arlo", "arlas", "arlos", "erla", "erlo", "erlas", "erlos", "irla", "irlo",
                 "irlas", "irlos", "arse", "erse", "irse")
# Spanish verb endings after a stem (present, past, imperfect, future, conditional, subjunctive, infinitive,
# gerund, participle), and the pronouns that may follow ("ciérrala", "guardarlas", "cerrándolas")
ES_ENDINGS = set("""a as an amos ais o e es en emos eis imos is i aste asteis aron io iste isteis ieron
    aba abas aban abamos ia ias ian iamos ara aras aran aremos are areis aria arias arian ariamos era eras eran eremos
    ere eria erias erian ira iras iran iremos ire iria irias irian iera ieras ieran ase ases asen iese
    ar er ir ando iendo yendo ado ada ados adas ido ida idos idas ad ed id""".split())
CLITICS = ("la", "lo", "las", "los", "le", "les", "se", "me", "te", "nos", "selo", "sela", "selos", "selas", "melo",
           "mela", "telo", "tela")
ES_FORMS = ES_ENDINGS | {e + c for e in ES_ENDINGS for c in CLITICS}
ES_PLAIN = {e + c for e in ("ar", "er", "ir") for c in ("", *CLITICS)}
NEVER = {"tarde", "tardes", "guardia", "guardias", "inicio", "inicios", "cargo", "cargos", "quite", "abrigo", "paso",
         "colgante"}      # words that look like a verb form here but are something else
ENERGY = {"bateria", "baterias", "pila", "pilas", "energia", "cargador", "cargadores", "enchufe", "battery", "batteries",
          "charger", "power", "plug", "porcentaje", "percent"}
ENERGY_ONLY = {"carg": "wait", "cargu": "wait", "save": "store"}   # without energy in the sentence: loading, saving a file
NEGATIONS = {"no", "nunca", "jamas", "ni", "tampoco", "sin", "not", "never", "without", "nor", "dont", "doesnt", "didnt",
             "cant", "cannot", "wont", "isnt", "arent", "wasnt", "werent", "shouldnt", "wouldnt", "couldnt", "havent",
             "hasnt", "neednt", "mustnt"}
CLAUSE = {"pero", "sino", "y", "e", "o", "u", "porque", "cuando", "si", "aunque", "mientras", "entonces", "pues",
          "but", "and", "or", "because", "when", "if", "although", "while", "then", "so"}
NEGATION_REACH = 5          # words a negation reaches forward ("no tienes que limpiarlo", "you don't have to clean it")


def _en_forms(verb: str) -> set[str]:
    """The forms of a regular English verb: close → closes, closed, closing; stop → stopped."""
    out = {verb}
    if verb.endswith("ee"):
        out |= {verb + "s", verb + "d", verb + "ing"}
    elif verb.endswith("e"):
        out |= {verb + "s", verb + "d", verb[:-1] + "ing"}
    elif verb.endswith("y") and verb[-2:-1] not in ("a", "e", "i", "o", "u"):
        out |= {verb[:-1] + "ies", verb[:-1] + "ied", verb + "ing"}
    else:
        out |= {verb + "s", verb + "ed", verb + "ing"}
        if verb.endswith(("s", "x", "z", "sh", "ch", "o")):
            out.add(verb + "es")
        if len(verb) >= 3 and verb[-1] not in "aeiouwxy" and verb[-2] in "aeiou" and verb[-3] not in "aeiou":
            out |= {verb + verb[-1] + "ed", verb + verb[-1] + "ing"}
    return out


EN_FORMS = {name: {f: v for v in en for f in _en_forms(v)} for name, (_, en, _) in SAID.items()}
EXACT = {name: set(words) for name, (_, _, words) in SAID.items()}


def action_text(action: str, language: str = "es") -> str:
    """A short description of an action ("none" too) in the video's language, for a model that picks one."""
    pair = ACTION_TEXTS.get(action, NO_ACTION)
    return pair[0] if language == "es" else pair[1]


def _read(word: str) -> tuple[str, str, bool] | None:
    """(action, what matched, said plainly) for one plain word, or None. The longest match wins (compart-
    "share" over compar- "compare")."""
    if len(word) < 2 or word in NEVER:
        return None
    best = None
    for name, (stems, _, _) in SAID.items():
        hits = []
        if word in EXACT[name]:
            hits.append((len(word), word, False))
        if word in EN_FORMS[name]:
            hits.append((len(word), EN_FORMS[name][word], True))
        for s in stems:
            if word.startswith(s) and word[len(s):] in ES_FORMS:
                hits.append((len(s), s, word[len(s):] in ES_PLAIN))
        for size, what, plain in hits:
            if best is None or size > best[0]:
                best = (size, name, what, plain)
    return None if best is None else (best[1], best[2], best[3])


def negated(words, i: int) -> bool:
    """Whether word i is under a negation said before it in the same clause ("no tienes que limpiarlo",
    "sin cerrarla", "you don't have to clean it"); a comma, a period or a word that starts a new clause
    ("pero", "y", "but") ends its reach."""
    for j in range(i - 1, max(i - 1 - NEGATION_REACH, -1), -1):
        raw = str(words[j])
        if raw.rstrip()[-1:] in ",.;:!?…":
            return False
        w = _plain(raw).replace("'", "").replace("’", "")
        if w in NEGATIONS:
            return True
        if w in CLAUSE:
            return False
    return False


def actions_in(words) -> list[tuple[str, int, bool]]:
    """Every (action, index of the word, said plainly) a sentence's verbs name, in the order said (one per
    word). A verb under a negation ("no tienes que limpiarlo") names nothing; a participle or a word that only
    starts like a verb is not one ("abiertas", "abril", "cuesta"). Said plainly: the verb's infinitive
    (Spanish, also with a pronoun: "guardarla") or an English form of it ("drains"), not a conjugated
    Spanish form ("guardan") nor a word taken as it is ("rápido")."""
    plain = [_plain(w) for w in words]
    energy = any(w in ENERGY for w in plain)
    out = []
    for i, w in enumerate(plain):
        found = _read(w)
        if found is None or negated(words, i):
            continue
        name, what, clear = found
        if not energy and what in ENERGY_ONLY:
            name = ENERGY_ONLY[what]
        out.append((name, i, clear))
    return out


def action_of(words) -> tuple[str, int] | None:
    """(action, index of the word) a sentence asks for: the first verb that names one and is not negated.
    `words`: the sentence's words, as written."""
    found = actions_in(words)
    return (found[0][0], found[0][1]) if found else None


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
        self.target = _icon(sp.get("target"), target) if self.action in TARGETED else None
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
        self.arrange(area)
        self.block = (st.safe_w, st.safe_h * 0.9)        # it fills the safe zone by construction
        self.settle = ENTER + ACT + 0.25

    def arrange(self, area: tuple[float, float, float, float], tall: bool | None = None) -> None:
        """Places the action inside `area` (x0, y0, x1, y1): its middle, its size and, for an action between
        two things, where each one stands — one above the other when `tall` (default: a vertical frame),
        else side by side. A template that draws the action somewhere else (a pack's place) calls it again."""
        tall = self.st.vertical if tall is None else tall
        self.tall, self.area = tall, area
        aw, ah = area[2] - area[0], area[3] - area[1]
        self.cx, self.cy = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
        two = self.action in TWO
        self.S = min(aw, ah * 0.86) * (0.78 if two else 1.12)     # an illustration fills about half to 0.9 of its box
        if two:                                    # one above the other (vertical) or side by side (landscape)
            d = (ah if tall else aw) * 0.25
            self.a = (self.cx - aw * 0.12, self.cy - d) if tall else (self.cx - d, self.cy - ah * 0.05)
            self.b = (self.cx + aw * 0.12, self.cy + d) if tall else (self.cx + d, self.cy + ah * 0.05)

    def play(self, cv: Canvas, t: float) -> None:
        """The action alone at instant t, where arrange() put it (no glow, poster or sparkles)."""
        getattr(self, "_" + self.action)(cv, t)

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
            self.play(cv, t)
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
        side = 1 if self.tall else -1
        mid = ((ax + bx) / 2 + side * S * 0.55, (ay + by) / 2 - (0 if self.tall else S * 0.45))
        return smooth_points([(ax + S * 0.3 * (1 if bx > ax else -1) * (0 if self.tall else 1), ay + S * 0.35 * (1 if self.tall else 0)),
                              mid, (bx, by - S * 0.55 if self.tall else by)], tension=0.6)

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
        """It drops into the place that keeps it, which lights up and shows a check; it stays there, small,
        beside what was stored before, and the place it left keeps an empty, dashed outline."""
        st, S = self.st, self.S
        c = st.theme["colors"]
        (ax, ay), (bx, by) = self.a, self.b
        p = motion.ease_in_out(self._p(t))
        if p > 0.25:                                                   # where it was: empty now
            r = S * 0.2
            ring = [(ax + r * math.cos(math.radians(d)), ay + r * math.sin(math.radians(d))) for d in range(0, 361, 15)]
            kit.rail(cv, st, ring, t, color=c["paper"], width=st.px(4), alpha=0.55 * min((p - 0.25) / 0.3, 1.0))
        self._hero(cv, self.target, bx, by, S, t, active=motion.shake(t - ENTER - ACT))
        x, y = ax + (bx - ax) * p, ay + (by - S * 0.2 - ay) * p
        size = S * (0.7 - 0.35 * p)
        if p < 1:
            kit.thread(cv, st, [(ax, ay + S * 0.4), (bx, by - S * 0.55)], 1.0, alpha=0.5)
            draw_icon(cv, st, self.subject, x, y, size, self._color(self.subject), t)
            return
        landed = motion.progress(t, ENTER + ACT, 0.3)
        for k, side in enumerate((-1, 1)):                            # what was already kept there
            room = motion.ease_out(landed)
            w = S * 0.2
            px_, py_ = bx + side * S * (0.1 + 0.08 * room), by - S * 0.24 + k * S * 0.02
            with saved(cv):
                cv.rotate(side * 14 * room, px_, py_)
                _packet(cv, st, px_, py_, w)
        bob = S * 0.012 * motion.wave(t) if self._alive() else 0.0
        with saved(cv):
            grow = motion.stamp_scale(landed, 0.8)
            cv.scale(grow, grow, x, y)
            draw_icon(cv, st, self.subject, x, y - bob, size, self._color(self.subject), t)
        r = S * 0.13
        kit.badge(cv, st, bx + S * 0.42, by - S * 0.36, r, c["paper"],
                  scale=motion.stamp_scale(motion.progress(t, ENTER + ACT, 0.25), 0.5))
        kit.draw_element("check", cv, st, bx + S * 0.42, by - S * 0.36, t, p=motion.progress(t, ENTER + ACT + 0.05, 0.2),
                         size=r * 1.1)

    def _freeze(self, cv: Canvas, t: float) -> None:
        """It freezes: frost ferns grow in from its corners, an icy glaze covers it, icicles hang from it and
        drip, it shakes once and stops; snowflakes keep falling around it."""
        st, S, cx, cy, z = self.st, self.S, self.cx, self.cy, self.z
        c = st.theme["colors"]
        p = self._p(t)
        shake = motion.shake(t - ENTER - ACT * 0.6) * z(10)
        self._hero(cv, self.subject, cx + shake, cy, S, t)
        w, h = S * 0.5, S * 0.9
        x0, y0 = cx + shake - w / 2, cy - h / 2
        glaze = motion.ease_out(motion.progress(t, ENTER + ACT * 0.45, ACT * 0.55))
        if glaze > 0:                                                  # the icy glaze, frosted
            box = round_rect(x0, y0, w, h, S * 0.07)
            cv.fill(box, c["paper"], 0.16 * glaze)
            h_ = st.theme["halftone"]
            cv.halftone(box, c["paper"], st.px(h_["cell"]), h_["angle"] + 30, st.px(float(h_["radius"])) * 0.9, 0.45 * glaze)
            for k, f in enumerate((0.2, 0.32, 0.7)):                   # glints across the ice
                gx = x0 + w * f
                cv.stroke(poly([(gx, y0 + h * 0.1), (gx + w * 0.18, y0 + h * 0.3)], closed=False), c["paper"],
                          st.px(6 if k != 1 else 3), 0.7 * glaze)
        grow = motion.ease_out(p)
        if grow > 0:
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):        # frost ferns from the corners
                ox, oy = cx + shake + sx * w * 0.46, cy + sy * h * 0.46
                main = math.degrees(math.atan2(-sy * h, -sx * w))
                for k, (da, length) in enumerate(((0, 1.0), (-28, 0.62), (28, 0.62))):
                    _fern(cv, st, ox, oy, main + da, S * 0.2 * length * grow, t, seed=k * 0.3 + sx + sy * 0.5)
            n = 6                                                      # icicles hanging from the bottom
            for k in range(n):
                ix = x0 + w * (0.12 + 0.76 * k / (n - 1))
                length = S * (0.05 + 0.06 * motion.noise(k * 3.1)) * grow
                half = S * 0.026
                icicle = poly([(ix - half, y0 + h - S * 0.01), (ix + half, y0 + h - S * 0.01), (ix, y0 + h + length)])
                kit.shape(cv, st, icicle, c["paper"])
                if k in (1, 4) and grow >= 1 and self._alive():        # a drop runs off and falls
                    f = motion.loop(t, k * 0.37)
                    if f < 0.6:
                        dy = (f / 0.6) ** 2 * S * 0.25
                        cv.fill(circle(ix, y0 + h + length + dy, S * 0.01), c["paper"], 1 - f / 0.6)
        for k in range(6):                                             # snowflakes that fall and turn
            if p <= 0:
                break
            phase = motion.loop(t, k / 6) if self._alive() else 0.17 * k
            x = cx + (k - 2.5) * S * 0.2 + math.sin(phase * 6.28 + k) * S * 0.035
            y = cy - S * 0.6 + phase * S * 1.2
            fade = min(1.0, (1 - phase) * 5, phase * 5)
            _snowflake(cv, st, x, y, z(22 + 8 * (k % 3)), 360 * phase * (1 if k % 2 else -1) * 0.25, fade * min(p * 2, 1))

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

    # ── the newer actions ───────────────────────────────────────────────────
    def _compare(self, cv: Canvas, t: float) -> None:
        """The two things on the pans of a balance: it tips, sways and settles; a VS stamp on the pivot."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        c = st.theme["colors"]
        L = S * 0.31
        top = cy - S * 0.2
        kit.ground(cv, st, cx, cy + S * 0.43, S * 0.55)
        post = poly([(cx - S * 0.045, cy + S * 0.38), (cx + S * 0.045, cy + S * 0.38), (cx + S * 0.016, top),
                     (cx - S * 0.016, top)])
        kit.shape(cv, st, post, c["device"])
        kit.shape(cv, st, round_rect(cx - S * 0.17, cy + S * 0.37, S * 0.34, S * 0.06, S * 0.025), c["muted"])
        p = self._p(t)
        tilt = 8 * motion.action(p, 0.15, 0.0, 1.6)
        if p >= 1 and self._alive():
            tilt += 1.6 * math.sin(2 * math.pi * motion.loop(t))
        a = math.radians(tilt)
        ends = [(cx - L * math.cos(a), top - L * math.sin(a)), (cx + L * math.cos(a), top + L * math.sin(a))]
        beam = poly(ends, closed=False)
        cv.stroke(beam, c["ink"], st.px(30))
        cv.stroke(beam, c["metal"], st.px(18))
        for (ex, ey), name in zip(ends, (self.subject, self.target)):
            pan_y = ey + S * 0.38
            for side in (-1, 1):
                cv.stroke(poly([(ex, ey), (ex + side * S * 0.15, pan_y)], closed=False), c["ink"], st.px(5))
            self._hero(cv, name, ex, pan_y - S * 0.17, S * 0.34, t, ground=False)
            bowl = smooth_points([(ex - S * 0.17, pan_y), (ex, pan_y + S * 0.065), (ex + S * 0.17, pan_y)], per=8, tension=0.5)
            kit.shape(cv, st, poly(bowl), c["paper"])
            cv.fill(circle(ex, ey, S * 0.018), c["ink"])
        r = S * 0.075
        kit.badge(cv, st, cx, top, r, c["paper"], scale=motion.stamp_scale(motion.progress(t, ENTER, 0.3), 0.6))
        kit.text(cv, st, "VS", cx, top + r * 0.36, "display", r * 0.95, c["ink"], "center")

    def _search(self, cv: Canvas, t: float) -> None:
        """A magnifier sweeps over it, stops on a spot that pings, and keeps looking around it."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        c = st.theme["colors"]
        self._hero(cv, self.subject, cx, cy, S * 0.72, t)
        p = motion.ease_in_out(self._p(t))
        spot = (cx + S * 0.1, cy - S * 0.06)
        r = S * 0.24 * (1 - p)
        ang = math.pi + 2.5 * math.pi * p
        if p >= 1 and self._alive():
            ang, r = 2 * math.pi * motion.loop(t), S * 0.015
        lx, ly = spot[0] + r * math.cos(ang), spot[1] + r * 0.7 * math.sin(ang)
        found = motion.progress(t, ENTER + ACT, 0.3)
        if found > 0:                                                  # found: rings go out from the spot
            for k in range(2):
                f = motion.loop(t, k / 2) if self._alive() else 0.5
                cv.stroke(circle(*spot, S * (0.05 + 0.16 * f)), self._color("search"), st.px(8), (1 - f) * found)
            cv.fill(circle(*spot, S * 0.035), c["paper"], found)
            cv.stroke(circle(*spot, S * 0.035), c["ink"], st.px(5), found)
        size = S * 0.46
        draw_icon(cv, st, "search", lx + size * 0.08, ly + size * 0.08, size, self._color("search"), t)
        if found > 0:
            br = S * 0.08
            bx, by = lx + size * 0.32, ly - size * 0.36
            kit.badge(cv, st, bx, by, br, c["paper"], scale=motion.stamp_scale(found, 0.5))
            kit.draw_element("check", cv, st, bx, by, t, p=motion.progress(t, ENTER + ACT + 0.05, 0.2), size=br * 1.1)

    def _connect(self, cv: Canvas, t: float) -> None:
        """A cable reaches from one to the other and plugs in with a spark; pulses keep running along it and
        a chain link sits in the middle."""
        st, S = self.st, self.S
        c = st.theme["colors"]
        (ax, ay), (bx, by) = self.a, self.b
        ax, bx = (ax - S * 0.05, bx + S * 0.05) if not self.tall else (ax - S * 0.04, bx + S * 0.04)
        self._hero(cv, self.subject, ax, ay, S * 0.78, t)
        self._hero(cv, self.target, bx, by, S * 0.78, t, active=motion.shake(t - ENTER - ACT))
        d = 1 if bx > ax else -1                                       # it hangs between them like a cable
        if self.tall:
            start, end = (ax + d * S * 0.06, ay + S * 0.36), (bx - d * S * 0.33, by + S * 0.08)
        else:
            start, end = (ax + d * S * 0.2, ay + S * 0.25), (bx - d * S * 0.33, by + S * 0.08)
        mid = ((start[0] + end[0]) / 2, max(start[1], end[1]) + S * 0.12)
        pts = smooth_points([start, mid, end], tension=0.6)
        drawn = motion.ease_out(self._p(t))
        part = cut(pts, drawn)
        if len(part) >= 2:
            line = poly(part, closed=False)
            cv.stroke(line, c["paper"], st.px(30))
            cv.stroke(line, c["ink"], st.px(24))
            cv.stroke(line, self._color(self.subject), st.px(13))
            tip = part[-1]
            if drawn < 1:
                kit.shape(cv, st, circle(*tip, S * 0.04), c["metal"])
        if drawn >= 1:
            kit.rail(cv, st, pts, t, color=c["paper"], width=st.px(5))
            (ex, ey) = pts[-1]
            kit.burst(cv, st, ex, ey, S * 0.06, S * 0.06, S * 0.08, c["paper"], appear=motion.progress(t, ENTER + ACT, 0.2),
                      t=t, rays=4)
            (mx, my), _ = _at(pts, 0.5)
            r = S * 0.08
            kit.badge(cv, st, mx, my, r, c["paper"], scale=motion.stamp_scale(motion.progress(t, ENTER + ACT, 0.3), 0.5))
            for side in (-1, 1):                                       # two chain links
                link = round_rect(mx + side * r * 0.28 - r * 0.42, my - r * 0.22, r * 0.84, r * 0.44, r * 0.22)
                with saved(cv):
                    cv.rotate(-35, mx, my)
                    cv.stroke(link, c["ink"], st.px(9))
                    cv.stroke(link, self._color(self.target), st.px(4))

    def _delete(self, cv: Canvas, t: float) -> None:
        """It flies into the trash turning and shrinking, with a puff of dust; where it was stays an empty,
        dashed place with a cross, and bits keep crumbling out of the bin."""
        st, S = self.st, self.S
        c = st.theme["colors"]
        (ax, ay), (bx, by) = self.a, self.b
        p = motion.ease_in_out(self._p(t))
        if p > 0.3:                                                    # where it was: gone
            r = S * 0.2
            ring = [(ax + r * math.cos(math.radians(d)), ay + r * math.sin(math.radians(d))) for d in range(0, 361, 15)]
            kit.rail(cv, st, ring, t, color=c["paper"], width=st.px(4), alpha=0.55 * min((p - 0.3) / 0.3, 1.0))
            br = S * 0.075
            kit.badge(cv, st, ax + r * 0.75, ay - r * 0.75, br, c["alert"],
                      scale=motion.stamp_scale(motion.progress(t, ENTER + ACT * 0.6, 0.3), 0.5))
            for sgn in (-1, 1):
                cv.stroke(poly([(ax + r * 0.75 - br * 0.4, ay - r * 0.75 - sgn * br * 0.4),
                                (ax + r * 0.75 + br * 0.4, ay - r * 0.75 + sgn * br * 0.4)], closed=False), c["paper"],
                          st.px(7))
        self._hero(cv, self.target, bx, by, S * 0.9, t, active=motion.shake(t - ENTER - ACT))
        mouth = (bx, by - S * 0.32)
        if p < 1:
            x = ax + (mouth[0] - ax) * p
            y = ay + (mouth[1] - ay) * p - S * 0.25 * math.sin(math.pi * p)
            with saved(cv):
                cv.rotate(220 * p, x, y)
                draw_icon(cv, st, self.subject, x, y, S * (0.62 - 0.52 * p), self._color(self.subject), t)
            return
        puff = motion.progress(t, ENTER + ACT, 0.45)
        if 0 < puff < 1:                                               # a puff of dust
            for k in range(6):
                a = math.radians(-160 + k * 28)
                d = S * (0.08 + 0.14 * puff)
                cv.fill(circle(mouth[0] + d * math.cos(a), mouth[1] + d * math.sin(a) * 0.7, S * 0.03 * (1 - puff) + 1),
                        c["paper"], 1 - puff)
        for k in range(4):                                             # bits that crumble away
            f = motion.loop(t, k / 4) if self._alive() else 0.4
            x = mouth[0] + (k - 1.5) * S * 0.07 + math.sin(f * 6.28 + k) * S * 0.02
            y = mouth[1] - f * S * 0.3
            s = S * 0.028 * (1 - f)
            if s > 1:
                cv.fill(rect(x - s / 2, y - s / 2, s, s), self._color(self.subject), 1 - f)

    def _download(self, cv: Canvas, t: float) -> None:
        """Pieces fall from where it is to the device below, whose progress bar fills; then a check, and
        pieces keep coming."""
        st, S = self.st, self.S
        c = st.theme["colors"]
        (ax, ay), (bx, by) = self.a, self.b
        self._hero(cv, self.subject, ax, ay - S * 0.06, S * 0.6, t)
        self._hero(cv, self.target, bx, by + S * 0.08, S * 0.78, t, active=motion.shake(t - ENTER - ACT))
        pts = [(ax, ay + S * 0.12), (bx, by - S * 0.28)]
        kit.rail(cv, st, pts, t, color=c["paper"], width=st.px(4), alpha=0.5 * motion.progress(t, ENTER, 0.3))
        if t >= ENTER + ACT * 0.15:
            for k in range(3):                                         # the pieces that come down
                f = motion.loop(t, k / 3) if self._alive() else 0.3 + 0.3 * k
                (x, y), _ = _at(pts, f)
                w = S * 0.07
                piece = poly([(x - w / 2, y - w), (x + w / 2, y - w), (x + w / 2, y), (x + w, y), (x, y + w), (x - w, y),
                               (x - w / 2, y)])
                kit.shape(cv, st, piece, self._color(self.subject), alpha=min(1.0, f * 6, (1 - f) * 6))
        p = motion.ease_in_out(self._p(t))
        bw, bh = S * 0.34, S * 0.06
        x0, y0 = bx - bw / 2, by + S * 0.2
        kit.shape(cv, st, round_rect(x0, y0, bw, bh, bh / 2), c["paper"])
        if p > 0:
            cv.fill(round_rect(x0 + bh * 0.15, y0 + bh * 0.15, max((bw - bh * 0.3) * p, bh * 0.7), bh * 0.7, bh * 0.35),
                    self._color(self.target))
        done = motion.progress(t, ENTER + ACT, 0.25)
        if done > 0:
            r = S * 0.1
            kit.badge(cv, st, bx + S * 0.36, by - S * 0.3, r, c["paper"], scale=motion.stamp_scale(done, 0.5))
            kit.draw_element("check", cv, st, bx + S * 0.36, by - S * 0.3, t, p=motion.progress(t, ENTER + ACT + 0.05, 0.2),
                             size=r * 1.1)

    def _share(self, cv: Canvas, t: float) -> None:
        """One thing feeds many: a thick stream comes out of it and splits into thin ones, one per person;
        small packets keep flowing down each, and a pie cut in parts sits where it splits."""
        st, S, cx, cy = self.st, self.S, self.cx, self.cy
        c = st.theme["colors"]
        n = 3
        self._hero(cv, self.subject, cx, cy - S * 0.3, S * 0.5, t)
        split = (cx, cy + S * 0.02)
        trunk = [(cx, cy - S * 0.07), split]
        ends = [(cx + (k - (n - 1) / 2) * S * 0.32, cy + S * 0.17) for k in range(n)]
        branches = [smooth_points([split, (split[0] + (ex - split[0]) * 0.6, split[1] + S * 0.04), (ex, ey)], per=10)
                    for ex, ey in ends]
        p = self._p(t)
        color = self._color(self.subject)
        first = motion.ease_out(min(p * 2, 1.0))
        part = cut(trunk, first)
        if len(part) >= 2:
            cv.stroke(poly(part, closed=False), c["ink"], st.px(34))
            cv.stroke(poly(part, closed=False), color, st.px(22))
        second = motion.ease_out(max(p * 2 - 1, 0.0))
        for b in branches:
            bit = cut(b, second)
            if len(bit) >= 2:
                cv.stroke(poly(bit, closed=False), c["ink"], st.px(17))
                cv.stroke(poly(bit, closed=False), color, st.px(8))
        for k, (ex, ey) in enumerate(ends):
            self._hero(cv, self.target or "user", ex, ey + S * 0.15, S * 0.3, t)
        if p >= 1:
            for k, b in enumerate(branches):                           # a small share for each
                f = motion.loop(t, k / n) if self._alive() else 0.5
                (x, y), _ = _at(b, f)
                s = S * 0.022
                kit.shape(cv, st, rect(x - s, y - s, 2 * s, 2 * s), c["paper"])
        r = S * 0.08
        pie = (cx + S * 0.16, split[1] - S * 0.05)
        grow = motion.stamp_scale(motion.progress(t, ENTER + ACT * 0.5, 0.3), 0.5) * min(p * 3, 1.0)
        if grow > 0:
            kit.badge(cv, st, *pie, r, c["paper"], scale=grow)
            for k in range(n):
                a0 = -90 + k * 360 / n
                wedge = [pie] + [(pie[0] + r * 0.78 * grow * math.cos(math.radians(a0 + d)),
                                  pie[1] + r * 0.78 * grow * math.sin(math.radians(a0 + d))) for d in range(0, 121, 15)]
                cv.fill(poly(wedge), color if k == 0 else (c["muted"] if k == 1 else c["paper"]))
                cv.stroke(poly(wedge), c["ink"], st.px(3))

    def _queue(self, cv: Canvas, t: float) -> None:
        """A line waits before a gate: one goes through at a time while the barrier lifts, the others step
        forward; a clock beside the line keeps turning."""
        st, S, cx, cy, z = self.st, self.S, self.cx, self.cy, self.z
        c = st.theme["colors"]
        gate = (cx + S * 0.27, cy - S * 0.1)
        self._hero(cv, self.subject, *gate, S * 0.5, t)
        ground_y = cy + S * 0.24
        step = S * 0.15
        slot = [(cx + S * 0.06 - k * step, ground_y) for k in range(6)]
        p = self._p(t)
        q = motion.loop(t) if p >= 1 and self._alive() else 0.6
        s = motion.ease_in_out(min(q / 0.35, 1.0))
        lift = math.sin(math.pi * min(q / 0.35, 1.0))
        pivot = (gate[0] - S * 0.12, ground_y + S * 0.02)
        arm = math.radians(180 + 75 * lift)                            # the barrier
        tip = (pivot[0] + S * 0.11 * math.cos(arm), pivot[1] + S * 0.11 * math.sin(arm))
        cv.stroke(poly([pivot, tip], closed=False), c["ink"], st.px(18))
        cv.stroke(poly([pivot, tip], closed=False), c["alert"], st.px(9))
        kit.shape(cv, st, circle(*pivot, S * 0.025), c["device"])
        kit.ground(cv, st, cx - S * 0.12, ground_y + S * 0.06, S * 0.8)
        arrive = 1 - motion.ease_out(p)
        for k in range(6):
            pos = k - s                                                # where this one is now, in slots
            if pos < -0.999:
                continue
            if pos < 0:                                                # the first one goes through the gate
                f = -pos
                x = slot[0][0] + (gate[0] - slot[0][0]) * f
                y = ground_y - S * 0.06 - (ground_y - gate[1] - S * 0.05) * f
                alpha, size = 1 - f, 1 - 0.6 * f
            else:
                i, frac = int(pos), pos - int(pos)
                xa = slot[min(i, 5)][0] + (slot[min(i + 1, 5)][0] - slot[min(i, 5)][0]) * frac
                hop = S * 0.02 * math.sin(math.pi * frac) if s < 1 else 0.0
                x, y = xa - arrive * S * (0.6 + 0.12 * k), ground_y - S * 0.06 - hop
                alpha = min(1.0, (5 - pos) * 1.5) if pos > 4 else 1.0
                size = 1.0
            alpha *= motion.clamp01((x - self.area[0]) / (S * 0.12))      # it comes in from the edge of its area
            if alpha <= 0.02:
                continue
            if self.target:
                with saved(cv):
                    cv.push_alpha(alpha)
                    draw_icon(cv, st, self.target, x, y - S * 0.04, S * 0.16 * size, self._color(self.target), t)
            else:
                w = S * 0.13 * size
                kit.shape(cv, st, round_rect(x - w / 2, y - w * 0.32, w, w * 0.64, w * 0.12), c["paper"], alpha=alpha)
                for j in range(2):
                    cv.fill(round_rect(x - w * 0.34, y - w * 0.12 + j * w * 0.2, w * (0.55 - 0.2 * j), w * 0.08, w * 0.04),
                            c["ink"], 0.5 * alpha)
        draw_icon(cv, st, "clock", cx - S * 0.16, cy - S * 0.12, S * 0.27, self._color("clock"), t)
        dots = int(motion.loop(t) * 3) + 1 if self._alive() else 3
        for k in range(dots):
            cv.fill(circle(cx - S * 0.16 + (k - 1) * z(30), cy + S * 0.07, z(9)), c["paper"])

    def sounds(self):
        return [("whoosh", 0.0), ("pop", ENTER + ACT)]

    def lines(self):
        return [x for x in (self.kicker, self.textv) if x]


def _at(pts, p):
    from .paths import point_at

    return point_at(pts, p)


def _packet(cv: Canvas, st, x: float, y: float, w: float, alpha: float = 1.0) -> None:
    """A small card of data with two lines on it."""
    kit.shape(cv, st, round_rect(x - w / 2, y - w * 0.32, w, w * 0.64, w * 0.12), st.theme["colors"]["paper"], alpha=alpha)
    for j in range(2):
        cv.fill(round_rect(x - w * 0.34, y - w * 0.12 + j * w * 0.2, w * (0.55 - 0.2 * j), w * 0.08, w * 0.04),
                st.theme["colors"]["ink"], 0.5 * alpha)


def _fern(cv: Canvas, st, x: float, y: float, degrees: float, length: float, t: float, seed: float = 0.0) -> None:
    """A frost fern: a stem with side branches that get shorter towards its tip, and a sparkle on it."""
    if length < 2:
        return
    c = st.theme["colors"]
    a = math.radians(degrees)
    tip = (x + length * math.cos(a), y + length * math.sin(a))
    lines = [[(x, y), tip]]
    for k in range(1, 4):
        f = k / 4
        bx, by = x + length * f * math.cos(a), y + length * f * math.sin(a)
        side = length * 0.32 * (1 - f * 0.6)
        for turn in (-50, 50):
            b = a + math.radians(turn)
            lines.append([(bx, by), (bx + side * math.cos(b), by + side * math.sin(b))])
    for pts in lines:
        cv.stroke(poly(pts, closed=False), c["ink"], st.px(8), 0.55)
    for pts in lines:
        cv.stroke(poly(pts, closed=False), c["paper"], st.px(4), 0.95)
    kit.sparkle(cv, st, *tip, length * 0.18 + 2, t, seed=seed)


def _snowflake(cv: Canvas, st, x: float, y: float, r: float, turn: float, alpha: float) -> None:
    """A six-armed snowflake with little branches, outlined in ink."""
    if alpha <= 0.02 or r < 1:
        return
    c = st.theme["colors"]
    lines = []
    for k in range(6):
        a = math.radians(turn + k * 60)
        lines.append([(x, y), (x + r * math.cos(a), y + r * math.sin(a))])
        mx, my = x + r * 0.6 * math.cos(a), y + r * 0.6 * math.sin(a)
        for d in (-45, 45):
            b = a + math.radians(d)
            lines.append([(mx, my), (mx + r * 0.32 * math.cos(b), my + r * 0.32 * math.sin(b))])
    for pts in lines:
        cv.stroke(poly(pts, closed=False), c["ink"], st.px(7), alpha)
    for pts in lines:
        cv.stroke(poly(pts, closed=False), c["paper"], st.px(3.5), alpha)
