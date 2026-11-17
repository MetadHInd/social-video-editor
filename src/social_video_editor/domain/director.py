"""The director: keeps the presenter from staying alone on camera too long by cutting to footage.

The script's cues decide most of what is on screen. Between them the presenter talks to the camera;
in a vertical video a face that stays more than a few seconds loses people. The director walks the
sentences in order with a clock of "camera seconds since something else was on screen" and, when a
sentence would push it past the limit, proposes a b-roll cutaway that starts **at a word of that
sentence** (the first one that names something, after the limit) and lasts to its end:

  - the opening: the face first (`script.opening` seconds, the hook), then footage;
  - afterwards: at most `script.max_camera` seconds of camera in a row;
  - the last sentence (the closing, a question to the viewer) stays on camera;
  - a cutaway is never shorter than `script.auto_min` seconds, and the face stays at least MIN_FACE
    seconds before footage covers it: when the word after the limit is too close to the end of its
    sentence, the closest word before it is used.

The search is built from what is being said: a word that names an illustration (phone, battery,
memory… the same vocabulary as the notes), at the cut or earlier in the sentence, gives a
stock-friendly English query, with variations so the same picture does not come back; without one,
the topic of the script (its most named thing).
Proposals carry "_auto": "director" (editable, removable) and a "fallback": an illustrated title card with
what is being said; `sve broll` fetches them like any other and, when no clip passes its checks, puts
the card instead, so the presenter is never alone too long even without a good clip.

The language judgments (which words make the title, whether figures are compared, the name of each
bar, what a scene shows the thing doing, which illustration, which drawing) are the hand rules' by
default; with a `decide` (domain/decide.py) each one is also a closed question whose options are what
the rules found, so a local model may pick a better one. A proposal a model answer changed carries
"_decided": the stage that answered ("nli" / "llm").
"""
from __future__ import annotations

import re
from collections import Counter

from . import flow as flows
from .decide import Answer, Decide, Option, Question, ask
from .graphics import suggest
from .graphics.icons import ALONE, ICONS, describe_icon, find_icon, match_icon
from .graphics.scene import dress
from .graphics.scenes import ACTIONS, TARGETED, action_of, action_text, actions_in
from .numbers import ALONE_NUMBERS, SCALES, SPELLED, _EDGE, _digits, spelled  # noqa: F401  (the figure parser lives there)
from .script import tokens

DEFAULTS = {"auto_broll": False, "max_camera": 4.0, "opening": 2.5, "auto_min": 1.8,
            "auto_line_chart": True, "auto_versus": True, "auto_code": True, "auto_screen": True,
            "auto_diagram": True, "figure_context": True, "scene_fit": True, "verb_bars": False}
# the graphics the director chooses by itself besides footage and its cards (each one a setting); all
# False = the director of before them, exactly
CHOICES = ("auto_line_chart", "auto_versus", "auto_code", "auto_screen", "auto_diagram", "figure_context", "scene_fit",
           "verb_bars")
OFF = {k: False for k in CHOICES}
LEAD_IN = 0.3               # a cutaway never starts in the first moment of a sentence (the word before it is heard)
MIN_FACE = 1.5              # the presenter is on screen at least this long before footage covers them
QUERIES = {                 # what stock libraries show well for each illustration, in turns
    "phone": ["smartphone in hand", "person scrolling a phone", "phone screen close up"],
    "app": ["smartphone apps on screen", "opening an app on a phone", "phone home screen"],
    "battery": ["phone battery charging", "smartphone low battery"],
    "ram": ["computer memory module", "ram chip close up"],
    "disk": ["hard drive storage", "ssd close up"],
    "chip": ["computer processor chip", "circuit board macro"],
    "globe": ["internet network connection", "world network lights"],
    "router": ["wifi router lights", "home router"],
    "cloud": ["data center servers", "cloud computing"],
    "server": ["server room", "data center"],
    "laptop": ["laptop typing", "laptop screen"],
    "tower": ["cell tower antenna", "telecommunication tower"],
    "user": ["person using a smartphone", "people looking at phones"],
    "lock": ["phone security lock screen", "password on a phone"],
    "printer": ["printer printing", "office printer"],
}
TOPIC_DEFAULT = "phone"
TOPIC_ICON = {"app": "phone"}   # a sentence that names nothing shows the topic's illustration; the app's alone reads
                                # as an unrelated photo, so the phone it lives in (measured: right 57 → 60 %)
POSSESSIVES = {"tus", "sus", "mis", "your", "yours"}       # "tus apps" names the apps, not a person
DETERMINERS = set("el la los las un una unos unas tu tus su sus mi mis este esta estos estas the a an your my this these".split())
ICON_DEVICES = ("phone", "app", "laptop", "chip", "ram", "disk", "router", "server", "cloud", "globe", "tower", "lock", "battery",
                "signal", "satellite", "camera", "keyboard", "mic", "speaker", "charger", "cable", "building", "video")
OWNERS = {"swipe": ("app",), "open": ("app",), "sleep": ("app", "phone", "laptop"), "wait": ("app", "phone", "laptop", "user"),
          "store": ("app", "phone", "laptop"), "work": ("chip", "phone", "laptop", "server", "ram"),
          "faster": ("phone", "laptop", "app"), "slower": ("phone", "laptop", "app"),
          "delete": ("photo", "file", "folder", "app", "video", "message"),        # what is deleted
          "download": ("cloud", "globe", "server")}                                 # where it comes from
OBJECTS = {"open": ("app", "phone", "laptop", "video", "photo", "file", "folder", "message", "camera"),
           "swipe": ("app", "phone", "laptop", "video", "photo", "file", "folder", "message", "camera")}
# script.scene_fit: what these actions are done to, when the word after the verb names an illustration
TARGET_OWN = {"download": ("phone", "laptop", "disk", "ram", "camera", "speaker"),   # where it comes down to
              "delete": ("trash",)}   # the target is one of these when one is named
CONNECTORS = set("pero si cuando porque entonces aunque como mientras but if when because then so although".split())
LOCAL_STOP = set("cada vez algo nada todo todos todas otra otro misma mismo cosa cosas forma manera thing things way".split())
PHRASE_WORDS = 6
FEWEST = 3
CLAUSE_END = (",", ";", ":")
LEADING = set("y e pero que o u entonces asi and but so or that then".split())
COVERING = ("graphic", "photo", "broll")
TRAILING = LEADING | DETERMINERS | set("de del a al en con por para sin to of in on with for at".split())
TITLE_CANDIDATES = 4
PRONOUNS = set("it they this that these those he she we one ello eso esto esta este estos estas el ella ellos ellas "
               "lo le les yo nosotros".split())
NAMELESS = PRONOUNS | TRAILING | set("by from into than desde hasta sobre".split())     # never a bar's name alone
ES_WORDS = set("el la los las de del que y en un una unos unas es son por para con se su sus tu tus pero si lo al mas "
               "muy como".split())
EN_WORDS = set("the an of to and in is are it you your that for on with but this these was were".split())
PROMPTS = {                 # topic → the question (Spanish, English)
    "title_span": ("¿Cuál de estos textos cortos funciona mejor como título en pantalla para la frase? "
                   "Debe entenderse solo.",
                   "Which of these short texts works best as an on-screen title for the sentence? "
                   "It must make sense on its own."),
    "is_comparison": ("¿La frase compara entre sí estas cifras: {}?", "Does the sentence compare these figures: {}?"),
    "bar_labels": ("En un gráfico de barras de la frase, ¿qué nombre corto lleva la barra de «{}»?",
                   "In a bar chart of the sentence, which short name goes on the bar for “{}”?"),
    "action": ("¿Qué acción describe la frase? Elige la que se puede dibujar como una escena animada.",
               "Which action does the sentence describe? Pick the one that can be drawn as an animated scene."),
    "icon": ("¿Qué ilustración muestra mejor de qué habla la frase?",
             "Which illustration best shows what the sentence is about?"),
    "card_type": ("¿Qué dibujo explica mejor la frase en pantalla?", "Which drawing best explains the sentence on screen?"),
}
WHERE = ("Un dibujo tapa al presentador desde que dice «{}» hasta el final de la frase.",
         "A drawing covers the presenter from the moment he says “{}” to the end of the sentence.")
TOPIC = ("El video trata de: «{}».", "The video is about: “{}”.")
# Questions that say where the drawing comes in / that get the script's opening line as its topic. Both
# measured on the labelled set and both made the language model worse (titles 85 % → 74 % with the cut,
# 72 % with the topic; the kind of drawing 68 % → 55 % / 69 %, still under the rules' 87 %): off.
CUT_TOPICS: set[str] = set()
TOPIC_TOPICS: set[str] = set()
CUT_WORDS = 6
ABOUT = ("Esta frase habla de {}.", "This sentence is about {}.")            # zero-shot templates
COMPARES = ("Esta frase {}.", "This sentence {}.")
COMPARE_OPTIONS = (("compara cifras entre sí", "compares figures with each other"),
                   ("dice cifras sin compararlas", "states figures without comparing them"))


def settings(cfg: dict) -> dict:
    s = (cfg or {}).get("script") or {}
    return {k: s.get(k, v) if s.get(k) is not None else v for k, v in DEFAULTS.items()}


# ── the decider's questions ─────────────────────────────────────────────────────────────────────

class _Asker:
    """ask() for one proposal, keeping the stages whose answer differed from the rules'. about: the
    script's opening line (its topic), given to the questions of TOPIC_TOPICS."""

    def __init__(self, decide: Decide, language: str, about: str | None = None):
        self.decide, self.language, self.sources, self.about = decide, language, [], about

    def __call__(self, topic: str, kind: str, options, context: str, default: str, confidence: float,
                 hypothesis: tuple[str, str] | None = None, fill: str | None = None, where: str | None = None) -> Answer:
        prompt = self.text(PROMPTS[topic])
        prompt = prompt.format(fill) if fill is not None else prompt
        if where and topic in CUT_TOPICS:
            prompt = f"{self.text(WHERE).format(where)} {prompt}"
        if self.about and topic in TOPIC_TOPICS:
            prompt = f"{self.text(TOPIC).format(self.about)}\n{prompt}"
        q = Question(topic, kind, prompt, tuple(options), context,
                     (default,), round(float(confidence), 4), self.language,
                     None if hypothesis is None else self.text(hypothesis))
        a = ask(self.decide, q)
        if a.source != "rules" and tuple(a.values) != q.default:
            self.sources.append(a.source)
        return a

    def text(self, pair: tuple[str, str]) -> str:
        return pair[0] if self.language == "es" else pair[1]


def language_of(words, language: str | None = None) -> str:
    """"es" or "en": the video's language when given, else what the words look like (Spanish when unsure)."""
    lang = str(language or "").strip().lower()
    if lang and lang != "auto":
        return "es" if lang.startswith("es") else "en"
    plain = ["".join(tokens(str(w[0] if isinstance(w, (list, tuple)) else w))) for w in words]
    return "en" if sum(w in EN_WORDS for w in plain) > sum(w in ES_WORDS for w in plain) else "es"


def _said(words) -> str:
    return " ".join(str(w) for w, _ in words).strip()


def _from(words: list[tuple[str, float]], at: float) -> str:
    """What is said from the cut on (its first CUT_WORDS words), for a question that says where it is."""
    cut = next((i for i, (_, t) in enumerate(words) if t >= at - 1e-6), 0)
    said = [_clean(w) for w, _ in words[cut:] if _clean(w)]
    return " ".join(said[:CUT_WORDS]) + ("…" if len(said) > CUT_WORDS else "")


def _decided(got: dict | None, rules: dict | None, asker: _Asker) -> dict | None:
    """The proposal, marked with the stage that changed it when a model answer did."""
    if got is not None and got != rules and asker.sources:
        return {**got, "_decided": asker.sources[0]}
    return got


def _icon_of(word: str) -> str | None:
    t = "".join(tokens(word))
    return None if len(t) < 3 or t in ALONE or t in POSSESSIVES else find_icon(word)


def topic(sentences: list[dict]) -> str:
    """The thing the script names most (an illustration name), for searches with nothing better."""
    seen = Counter(i for s in sentences for w in s.get("words") or [] if (i := _icon_of(str(w[0]))))
    return seen.most_common(1)[0][0] if seen else TOPIC_DEFAULT


def phrase(words: list[tuple[str, float]], at: float, most: int = PHRASE_WORDS, decide: Decide | None = None,
           language: str | None = None) -> str:
    """What is said around the cut, as a short title (the illustrated card used when no footage is found):
    the clause the cut falls in (its last `most` words when it is longer), at least FEWEST words, without
    a leading "and / but / then"; capitalized (after an opening ¿ or ¡), no final stop. With `decide`,
    a model may pick another short span of the sentence (`title_span`)."""
    return _phrase(words, at, most, _Asker(decide, language_of(words, language)) if decide else None)


def _title(words: list[tuple[str, float]], closes: list[bool], begin: int, end: int) -> str:
    text = " ".join(w for w, _ in words[begin:end + 1]).strip(" ,;:.…")
    if end < len(words) - 1 and not closes[end]:
        text += "…"
    lead = len(text) - len(text.lstrip("¿¡\"'«("))
    return text[:lead] + text[lead:lead + 1].upper() + text[lead + 1:]


def _phrase(words: list[tuple[str, float]], at: float, most: int, asker: _Asker | None) -> str:
    if not words:
        return ""
    ends = CLAUSE_END + (".", "?", "!", "…")
    closes = [w.rstrip().endswith(ends) for w, _ in words]
    cut = next((i for i, (_, t) in enumerate(words) if t >= at - 1e-6), 0)
    begin = cut
    while begin > 0 and not closes[begin - 1]:
        begin -= 1
    stop = cut
    while stop < len(words) - 1 and not closes[stop]:
        stop += 1
    begin = max(begin, stop - most + 1)                     # a long clause: the words that close it
    while begin < len(words) - 1 and "".join(tokens(words[begin][0])) in LEADING:
        begin += 1
    if begin > stop:                                        # the clause was only a connector: the next one
        stop = begin
        while stop < len(words) - 1 and not closes[stop]:
            stop += 1
    end = max(begin, min(stop, begin + most - 1))
    if end - begin + 1 < FEWEST:                             # too short to read as a title: the next clause too
        while end < len(words) - 1 and end - begin + 1 < most:
            end += 1
            if closes[end] and end - begin + 1 >= FEWEST:
                break
    text = _title(words, closes, begin, end)
    if asker is None:
        return text
    plain = ["".join(tokens(w)) for w, _ in words]

    def clause(i: int) -> tuple[int, int]:
        a, b = i, i
        while a > 0 and not closes[a - 1]:
            a -= 1
        while b < len(words) - 1 and not closes[b]:
            b += 1
        return a, b

    def fit(a: int, b: int) -> str | None:                  # FEWEST to `most` words, from the start of [a, b]
        while a <= b and plain[a] in LEADING:
            a += 1
        last = min(b, a + most - 1)
        while a < last < b and plain[last] in TRAILING:     # a cut short ends on a word that says something
            last -= 1
        return _title(words, closes, a, last) if FEWEST <= last - a + 1 <= most else None

    ca, cb = clause(cut)
    lead = ca
    while lead < cb and plain[lead] in LEADING:
        lead += 1
    full = begin == lead and end == cb
    spans = [fit(ca, cb)]
    joints = [k for k in range(ca + 1, cb + 1) if plain[k] in LEADING | CONNECTORS]
    for a, b in zip([ca, *joints], [k - 1 for k in joints] + [cb]):          # its parts, at "y / pero / que…"
        spans.append(fit(a, b))
    if ca > 0:
        spans.append(fit(*clause(ca - 1)))
    if cb < len(words) - 1:
        spans.append(fit(*clause(cb + 1)))
    texts = [text]
    for t in spans:
        if t and t not in texts and len(texts) < TITLE_CANDIDATES:
            texts.append(t)
    if len(texts) < 2:
        return text
    a = asker("title_span", "choice", [Option(t, t) for t in texts], _said(words), text, 0.9 if full else 0.5,
              where=_from(words, at))
    return a.value or text


NUMBER_WORDS = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "cien": 100,
    "mil": 1000, "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100, "thousand": 1000,
}                           # "un" / "one" and "cero" ("de cero") are left out: they are rarely figures


def _number(words: list[str], plain: list[str], i: int) -> tuple[float, int] | None:
    """A figure starting at word i (spelled()), but "un / uno / cero" only with a unit after them."""
    got = spelled(words, i)
    if got is None:
        return None
    if got[1] == i + 1 and plain[i] in ALONE_NUMBERS:
        nxt = words[got[1]] if got[1] < len(words) and not words[i].rstrip().endswith(_EDGE) else ""
        key = _unit_key(nxt) if nxt else ""
        if key not in TIME_UNITS and key not in SHORT_UNITS:
            return None
    return got


def _shown(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


UNIT_STOP = set("de del la el los las por y que al a en con the of a an and per".split())


def _clean(word: str) -> str:
    return str(word).strip("¿?¡!.,;:«»\"'()…")


def figure(words: list[tuple[str, float]], context: bool = False) -> dict | None:
    """A figure the sentence says ("20", "dos minutos", "17 mil apps", "ciento sesenta y uno") as a `number`
    graphic: the value, its unit (the word after it), what comes before it in its clause as the label and
    after it as the line under it. A figure with a unit is preferred ("dos minutos" over "veinte al día").
    context (script.figure_context): a range without a unit ("de siete a nueve") is a time, not a figure."""
    plain = ["".join(tokens(w)) for w, _ in words]
    said = [w for w, _ in words]
    found = []
    skip = -1
    for i, (w, _) in enumerate(words):
        if i < skip:
            continue
        got = _number(said, plain, i)
        if got is None:
            continue
        number, after = got
        skip = after
        if context and (ends := _range_end(said, plain, after)) is not None:
            skip = ends
            continue
        if context and plain[i] in NOT_IN_ENGLISH and language_of(words) == "en":
            continue                                        # "at once": not eleven
        raw = _clean(w)
        value = raw if after == i + 1 and raw.replace(".", "").replace(",", "").replace("%", "").isdigit() else _shown(number)
        unit = ""
        if after < len(words) and plain[after] and plain[after] not in UNIT_STOP and not plain[after].isdigit() \
                and not words[after - 1][0].rstrip().endswith((",", ";", ":", ".")):
            unit, after = _clean(words[after][0]), after + 1
        begin = i
        while begin > 0 and i - begin < 4 and not words[begin - 1][0].rstrip().endswith((",", ";", ":", ".", "?", "!")):
            begin -= 1
        end = after
        while end < len(words) and end - after < 4 and not words[end - 1][0].rstrip().endswith((",", ";", ":", ".", "?", "!")):
            end += 1
        before = [_clean(x) for x, _ in words[begin:i] if _clean(x)]
        rest = [_clean(x) for x, _ in words[after:end] if _clean(x)] if end > after else []
        spec = {"template": "number", "value": str(value)}
        if unit:
            spec["unit"] = unit
        if before:
            label = " ".join(before)
            spec["label"] = label[:1].upper() + label[1:]
        if rest:
            spec["sub"] = " ".join(rest)
        found.append((not unit, i, spec))
    return min(found, key=lambda x: (x[0], x[1]))[2] if found else None


TIME_UNITS = {"ms": 0.001, "milisegundo": 0.001, "millisecond": 0.001, "s": 1, "seg": 1, "segundo": 1, "second": 1,
              "min": 60, "minuto": 60, "minute": 60, "h": 3600, "hora": 3600, "hour": 3600, "dia": 86400, "day": 86400}
SHORT_UNITS = {"milisegundo": "ms", "millisecond": "ms", "segundo": "s", "second": "s", "seg": "s", "minuto": "min",
               "minute": "min", "hora": "h", "hour": "h", "dia": "días", "day": "days", "porciento": "%", "percent": "%",
               "gigabyte": "GB", "gb": "GB", "megabyte": "MB", "mb": "MB"}
MARKERS = set("antes ahora hoy ayer before now today yesterday".split())     # a bar named by when
COMPARING = set("vs versus contra frente comparado mientras compared than while".split())
LABEL_STOP = UNIT_STOP | set("son es eran fue is are was were vs versus contra frente tiene tienen tenia has have had tarda "
                             "tardaba tardan takes took gasta gastan usa usan uses use pesa pesan cuesta cuestan costs cost "
                             "mide miden".split())
LABEL_WORDS = 3
NOT_IN_ENGLISH = {"once"}   # a Spanish number word that English uses otherwise ("at once")
RANGE_JOINS = set("a al hasta to till until".split())
VERB_SUBJECTS = set("tú yo nosotros you i we".split())     # "tú duermes", "you sleep": the bar is the verb
PERIOD_HEADS = set("al por cada a per each every an".split())
PERIOD_NOUNS = set("dia dias semana semanas mes meses ano anos hora horas minuto minutos segundo segundos "
                   "day days week weeks month months year years hour hours minute minutes second seconds".split())
PREPOSITIONS = set("al a en de del por para con sin desde hasta sobre entre in on at for with by from to of per".split())
SUBJECTS = set("yo tú él ella ellos ellas nosotros usted ustedes lo le les me te se nos eso esto "
               "i you he she it we they".split())          # a bar's name never starts with one ("Lo medí", "It")
QUOTE_LEAD = ("según", "segun", "according to")
QUOTE_LEADS = (("segun",), ("according", "to"), ("de", "acuerdo", "con"), ("de", "acuerdo", "a"))
QUOTE_VERBS = set("publico dice dicen dijo dijeron revelo informo informa asegura aseguro afirma afirmo anuncio reporto "
                  "reporta encontro encontraron descubrio descubrieron estima estimo calcula calculo concluye concluyo "
                  "muestra mostro advierte advirtio explica "
                  "says said reported reports announced published claims claimed found finds discovered revealed "
                  "estimates estimated shows showed concluded warns warned explains".split())
STUDIES = set("estudio estudios informe reporte encuesta investigacion analisis articulo "
              "study studies report survey paper analysis article research".split())
STUDY_BY = set("de del by from".split())
QUOTE_SUBJECTS = SUBJECTS | set("mi mis tu tus su sus my your his her our their alguien nadie todos todo gente mucha "
                                 "mucho muchos muchas everyone someone somebody nobody people many most".split())   # "Mi mamá dice que…"
QUOTE_WORDS = 8             # words before the verb: "Un estudio de la Universidad de X encontró que"


def _unit_key(word: str) -> str:
    """'minutos' → 'minuto', 'GB' → 'gb', '%' → 'porciento': the same unit however it is said."""
    w = "".join(tokens(word)) or word.strip()
    if w in ("%", "porciento", "percent") or word.strip() == "%":
        return "porciento"
    for key in (w, w[:-2] if w.endswith("es") else w, w[:-1] if w.endswith("s") else w):
        if key in TIME_UNITS or key in SHORT_UNITS:
            return key
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def _range_end(said: list[str], plain: list[str], after: int) -> int | None:
    """When the figure that ends before word `after` opens a range without a unit ("de siete a nueve de
    la noche", "from 7 to 9"): the index after the range's second figure, else None."""
    if after + 1 >= len(plain) or plain[after] not in RANGE_JOINS or said[after - 1].rstrip().endswith(_EDGE):
        return None
    got = spelled(said, after + 1)
    return got[1] if got and _elided(said, plain, got[1]) else None


def _elided(said: list[str], plain: list[str], after: int) -> bool:
    """Whether the figure that ends before word `after` leaves its unit unsaid ("…y el Pixel, 18.",
    "a cincuenta y cuatro."): it ends its clause, or a preposition / article follows it ("cinco en el celular")."""
    return after >= len(plain) or said[after - 1].rstrip().endswith(_EDGE) or plain[after] in UNIT_STOP | PREPOSITIONS


def _figures(words: list[tuple[str, float]], context: bool = False, prev: dict | None = None) -> list[dict]:
    """Every figure said with its unit: [{"i", "end" (index after the unit), "value", "unit", "said"}].
    context (script.figure_context): a figure whose unit is left unsaid takes the unit of the figure said
    before it (in the sentence, else `prev`, the previous sentence's), marked "elided"; a range of figures
    without a unit ("de siete a nueve") is not a figure."""
    plain = ["".join(tokens(w)) for w, _ in words]
    out = []
    said_words = [w for w, _ in words]
    skip = -1
    for i, (w, _) in enumerate(words):
        if i < skip:
            continue
        raw = _clean(w)
        pct = raw.endswith("%")
        got = _number(said_words, plain, i)
        if got is None:
            continue
        value, after = got
        skip = after
        if context and plain[i] in NOT_IN_ENGLISH and language_of(words) == "en":
            continue
        if context and not pct and (after >= len(words) or _clean(words[after][0]) not in ("%", "por ciento")):
            ends = _range_end(said_words, plain, after)
            if ends is not None:
                skip = ends                                 # "de siete a nueve de la noche": when, not how much
                continue
            before = out[-1] if out else prev
            if before and _elided(said_words, plain, after):
                out.append({"i": i, "end": after, "value": float(value), "unit": before["unit"], "said": before["said"],
                            "elided": True})
                continue
        unit, said, end = ("porciento", "%", after) if pct else ("", "", after)
        if not pct and end < len(words) and _clean(words[end][0]) in ("%", "por ciento"):
            unit, said, end = "porciento", "%", end + 1
        elif not pct and end < len(words) and plain[end] and plain[end] not in UNIT_STOP and not plain[end].isdigit() \
                and not words[after - 1][0].rstrip().endswith((",", ";", ":", ".")):
            unit, said, end = _unit_key(words[end][0]), _clean(words[end][0]), end + 1
            if unit == "por" or said.lower() in ("por", "per"):
                unit, said, end = "", "", after
        if unit:
            out.append({"i": i, "end": end, "value": float(value), "unit": unit, "said": said})
    return out


def comparison(words: list[tuple[str, float]], decide: Decide | None = None, language: str | None = None,
               context: bool = False, verbs: bool = False) -> dict | None:
    """Two to six figures of the same kind compared in one sentence ("dos minutos por semana, al año dos
    horas"; "de 15 % antes a 40 % hoy") as `bars`: times are put in the smallest unit said; each bar is
    named by a word such as "antes / hoy" next to it, else by the words after it (or before it).
    → the spec plus "at" (the second the first figure is said), or None. With `decide`, a model may
    say whether figures the rules are unsure of are compared (`is_comparison`) and name a bar whose
    name the rules guessed (`bar_labels`). context (script.figure_context): a figure whose unit is left
    unsaid takes the one said before it ("duermes ocho horas y pasas cinco en el celular"); verbs
    (script.verb_bars): a bar whose only name is the verb it is said with is named by it ("Duermes")."""
    rules = _comparison(words, None, context, verbs)
    if decide is None:
        return rules
    asker = _Asker(decide, language_of(words, language))
    return _decided(_comparison(words, asker, context, verbs), rules, asker)


def _bar_label(words: list[tuple[str, float]], plain: list[str], pick: list[int]) -> tuple[str, list[int]]:
    """A bar's name from some words (and the words it uses): no leading connector or article, no trailing
    verb or preposition, at most LABEL_WORDS words, capitalized."""
    while pick and (plain[pick[0]] in LEADING or plain[pick[0]] in DETERMINERS):
        pick = pick[1:]
    while pick and plain[pick[-1]] in LABEL_STOP:
        pick = pick[:-1]
    pick = pick[-LABEL_WORDS:]
    label = " ".join(_clean(words[k][0]) for k in pick).strip()
    return (_capital(label) if label else ""), pick


def _period(plain: list[str], ks: list[int]) -> list[int]:
    """The words of ks that say a period ("al año", "por semana", "a la semana", "per week", "each day"), or []."""
    for j, k in enumerate(ks):
        if plain[k] in PERIOD_HEADS:
            for m in range(j + 1, min(j + 3, len(ks))):
                if plain[ks[m]] in PERIOD_NOUNS:
                    return ks[j:m + 1]
                if plain[ks[m]] not in DETERMINERS:
                    break
    return []


def _names_thing(words: list[tuple[str, float]], plain: list[str], pick: list[int], sure: bool) -> bool:
    """Whether a bar's name (its words, as picked) names the thing measured: not a pronoun with its verb
    ("Lo medí", "It"), no other figure in it ("Veinte al día"); a guess (not `sure`) must also start like a
    noun phrase: an article, a possessive or a preposition ("la app", "en el celular"), a word such as
    "antes / hoy", or a name written with a capital inside the sentence ("iPhone")."""
    ks = list(pick)
    while ks and plain[ks[0]] in LEADING:
        ks = ks[1:]
    if not ks:
        return False
    said = [w for w, _ in words]
    if any(_number(said, plain, k) is not None for k in ks):
        return False
    if _clean(said[ks[0]]).lower() in SUBJECTS:
        return False
    if sure or any(plain[k] in MARKERS for k in ks):
        return True
    first = plain[ks[0]]
    named = any(k > 0 and _clean(said[k])[:1].isupper() for k in ks)
    return first in DETERMINERS or first in PREPOSITIONS or first in POSSESSIVES or named


def _verb_label(words: list[tuple[str, float]], plain: list[str], closes: list[bool], pick: list[int]) -> str:
    """A bar named by the verb it is said with, when nothing else names it: one word that opens its clause,
    or comes right after "tú / yo / you / I / we" ("Duermes ocho horas", "You sleep eight hours" → "Sleep");
    not a word that only links ("es", "tiene", "uses"), a pronoun or a past form ("medí"). Else ""."""
    ks = list(pick)
    while ks and (plain[ks[0]] in LEADING or _clean(words[ks[0]][0]).lower() in VERB_SUBJECTS):
        ks = ks[1:]
    if len(ks) != 1:
        return ""
    k = ks[0]
    word = _clean(words[k][0])
    opens = k == 0 or closes[k - 1] or plain[k - 1] in LEADING or _clean(words[k - 1][0]).lower() in VERB_SUBJECTS
    if not opens or len(plain[k]) < 4 or plain[k] in LABEL_STOP | SUBJECTS | DETERMINERS | PRONOUNS | MARKERS \
            or word[-1:].lower() in "áéíóú" or not word.isalpha():
        return ""
    return _capital(word)


def _comparison(words: list[tuple[str, float]], asker: _Asker | None, context: bool = False,
                verbs: bool = False) -> dict | None:
    found = _figures(words, context)
    for f in found:
        f["base"] = TIME_UNITS.get(f["unit"])
    groups: dict[str, list[dict]] = {}
    for f in found:
        groups.setdefault("time" if f["base"] else f["unit"], []).append(f)
    best = max(groups.values(), key=len, default=[])
    if not 2 <= len(best) <= 6 or len({(f["value"], f["unit"]) for f in best}) < len(best):
        return None
    closes = [w.rstrip().endswith(CLAUSE_END + (".", "?", "!")) for w, _ in words]
    plain = ["".join(tokens(w)) for w, _ in words]
    apart = any(closes[k] for f, g in zip(best, best[1:]) for k in range(f["end"] - 1, g["i"]))
    other = any(plain[k] in ("y", "e", "and") and k + 1 < len(words) and plain[k + 1] in DETERMINERS
                for f, g in zip(best, best[1:]) for k in range(f["end"], g["i"]))
    sure = apart or any(w in MARKERS | COMPARING for w in plain)
    compared = bool(sure or best[0]["base"] or other)
    if asker is not None and not sure:                      # times or "y el / and the" only: the rules guess
        said = ", ".join(" ".join(_clean(w) for w, _ in words[f["i"]:f["end"]]) for f in best)
        a = asker("is_comparison", "yesno", [Option(v, asker.text(t)) for v, t in zip(("yes", "no"), COMPARE_OPTIONS)],
                  _said(words), "yes" if compared else "no", 0.5, COMPARES, said)
        compared = a.value == "yes"
    if not compared:
        return None                                         # figures one after another, not set against each other
    used = {k for f in best for k in range(f["i"], f["end"])}
    labels = []
    for n, f in enumerate(best):
        lo = best[n - 1]["end"] if n else 0
        hi = best[n + 1]["i"] if n + 1 < len(best) else len(words)
        before = []
        for k in range(f["i"] - 1, lo - 1, -1):              # its own clause, right before it
            if k in used or closes[k]:
                break
            before.insert(0, k)
        after = []
        for k in range(f["end"], hi):
            if k in used:
                break
            after.append(k)
            if closes[k]:
                break
        marked = [k for k in reversed(before) if plain[k] in MARKERS] or [k for k in after if plain[k] in MARKERS]
        own = before and len(before) <= LABEL_WORDS and before[0] > 0 and closes[before[0] - 1]
        ahead = next((after[:j] for j, k in enumerate(after) if plain[k] in LEADING), after)   # not the next one's
        weak = False
        if marked:
            pick = marked[:1]
        elif own:
            pick = before                                   # ", al año dos horas" → "Al año"
        elif after and len(after) <= LABEL_WORDS and closes[after[-1]]:
            pick = after                                    # "dos minutos por semana," → "Por semana"
        elif _period(plain, ahead) or _period(plain, before):
            pick = _period(plain, ahead) or _period(plain, before)      # "dos minutos por semana y…" → "Por semana"
        elif before:
            pick, weak = before[-LABEL_WORDS - 1:], True
        else:
            pick, weak = after[:LABEL_WORDS], True
        whole = pick
        period = bool(_period(plain, pick)) and _period(plain, pick) == pick
        label, pick = _bar_label(words, plain, pick)
        if period and pick != whole:                        # "a la semana" keeps its head (not "Semana")
            pick = whole
            label = _capital(" ".join(_clean(words[k][0]) for k in pick))
        good = bool(label) and _names_thing(words, plain, whole, not weak)
        if verbs and not good and (verb := _verb_label(words, plain, closes, whole)):
            label, pick, good = verb, [k for k in whole if _clean(words[k][0]).lower() not in VERB_SUBJECTS], True
        label = label or f"{n + 1}"
        pronoun = len(label.split()) == 1 and "".join(tokens(label)) in PRONOUNS | DETERMINERS
        if asker is not None and (weak or pronoun or not good):     # a guess: the words near its figure may name it better
            windows = [x for k in range(1, LABEL_WORDS + 1) for x in (before[-k:], before[:k], ahead[:k])]
            options = {label: pick}
            fine = {label: good}
            for w in windows:
                free = [k for k in w if k not in used]
                text, ks = _bar_label(words, plain, free)
                if text and text not in options and text not in labels and \
                        not (len(ks) == 1 and (plain[ks[0]] in NAMELESS or len(plain[ks[0]]) < 3)) \
                        and _names_thing(words, plain, free, False):
                    options[text] = ks
                    fine[text] = True
            if len(options) >= 2:
                said = " ".join(_clean(w) for w, _ in words[f["i"]:f["end"]])
                a = asker("bar_labels", "choice", [Option(t, t) for t in options], _said(words), label,
                          0.3 if pronoun or not good else 0.5, None, said)
                label = a.value or label
                pick = options[label]
                good = fine[label]
        if not good:
            return None                                     # no name for this bar: another drawing, not a wrong label
        used.update(pick)
        labels.append(label)
    if len(set(labels)) < len(labels):
        return None
    if best[0]["base"]:
        small = min(best, key=lambda f: f["base"])
        unit = SHORT_UNITS.get(small["unit"], small["said"])
        values = [f["value"] * f["base"] / small["base"] for f in best]
    else:
        unit = SHORT_UNITS.get(best[0]["unit"], best[0]["said"])
        values = [f["value"] for f in best]
    data = ", ".join(f"{label}:{v:g}" for label, v in zip(labels, values))
    return {"template": "bars", "data": data, "unit": unit, "at": words[best[0]["i"]][1]}


def _capital(text: str) -> str:
    """First letter up, unless the first word is written that way on purpose ("iPhone")."""
    first = text.split()[0] if text.split() else ""
    return text if any(c.isupper() for c in first[1:]) else text[:1].upper() + text[1:]


def said_data(words: list[tuple[str, float]], decide: Decide | None = None, language: str | None = None,
              opts: dict | None = None, screens=()) -> dict | None:
    """What a sentence says that is shown as data rather than searched for: figures compared → `bars`,
    a source quoted → `headline`. The spec plus "at" (when it starts being said), or None.
    opts: the director's settings (settings(cfg)); with them, first (each when its setting is on) a
    screenshot of the project it names → `screen`, a command → `code`, figures over three or more
    years → `line_chart`, two named things with a figure each → `versus`. Without opts: none of these."""
    p = opts or OFF
    first = _chosen(words, p, screens)
    if first is not None:
        return first
    return comparison(words, decide, language, bool(p.get("figure_context")), bool(p.get("verb_bars"))) or quote(words)


def _chosen(words: list[tuple[str, float]], p: dict, screens=()) -> dict | None:
    """The newer graphics a sentence asks for, in order of preference (each when its setting is on)."""
    for key, rule in (("auto_screen", lambda: screen_for(words, screens)), ("auto_code", lambda: code_for(words)),
                      ("auto_line_chart", lambda: line_chart_for(words)), ("auto_versus", lambda: versus_for(words))):
        if p.get(key) and (got := rule()) is not None:
            return got
    return None


def _said_data(words: list[tuple[str, float]], asker: _Asker | None, p: dict | None = None, screens=()) -> dict | None:
    p = p or OFF
    return _chosen(words, p, screens) or _comparison(words, asker, bool(p.get("figure_context")),
                                                     bool(p.get("verb_bars"))) or quote(words)


def quote(words: list[tuple[str, float]]) -> dict | None:
    """A quoted source as a `headline`: what was said, and who said it as the outlet:
    "Según X, …" / "De acuerdo con X, …" / "According to X, …";
    "X dice / afirma / reportó / encontró que …" / "X says / reported / found that …";
    "Un estudio de X encontró que …" / "A study by X found that …" (the outlet is X).
    The source must have a name (a word written with a capital, not a person such as "mi mamá" or "I").
    → the spec plus "at" (the second the source is named), or None."""
    plain = ["".join(tokens(w)) for w, _ in words]
    text = [w for w, _ in words]

    def named(ks: range) -> bool:
        cleaned = [_clean(text[k]) for k in ks if _clean(text[k])]
        if not cleaned or cleaned[0].lower() in QUOTE_SUBJECTS:
            return False
        if any(w[:1].isupper() for k, w in zip(ks, cleaned) if k > 0):
            return True                                 # "la OMS", "un estudio de Stanford"
        return ks[0] == 0 and (len(cleaned) == 1 or all(w[:1].isupper() for w in cleaned)) \
            and cleaned[0].lower() not in DETERMINERS   # "Reuters dice…" (a name at the start), not "Mucha gente…"

    def spec(said: str, outlet: str, at: float) -> dict | None:
        said = said.strip(" .,")
        if len(said.split()) < 3 or "?" in said:
            return None
        return {"template": "headline", "text": said[:1].upper() + said[1:], "outlet": outlet, "at": at}

    for k, w in enumerate(plain):
        lead = next((len(x) for x in QUOTE_LEADS if tuple(plain[k:k + len(x)]) == x), 0)
        if lead:
            stop = k + lead
            while stop < len(words) and stop - k - lead < 4 and not text[stop].rstrip().endswith(CLAUSE_END):
                stop += 1
            if stop >= len(words) or stop - k - lead >= 4:
                continue
            outlet = " ".join(_clean(x) for x in text[k + lead:stop + 1])
            said = " ".join(text[stop + 1:]).strip(" .")
            before = " ".join(text[:k]).strip(" ,")
            got = spec(said or before, outlet, words[k][1]) if any(x[:1].isupper() for x in outlet.split()) else None
            if got:
                return got
        if w in QUOTE_VERBS and 0 < k <= QUOTE_WORDS and k + 1 < len(plain) and plain[k + 1] in ("que", "that") \
                and not any(text[j].rstrip().endswith(CLAUSE_END) for j in range(k)):
            source = range(0, k)
            study = next((j for j in range(k - 1) if plain[j] in STUDIES and plain[j + 1] in STUDY_BY), None)
            if study is not None:                       # "un estudio de X" → X
                source = range(study + 2, k)
                while source and plain[source[0]] in DETERMINERS and len(source) > 1:
                    source = range(source[0] + 1, k)
            elif k > 4:
                continue
            if not named(source):
                continue
            got = spec(" ".join(text[k + 2:]), " ".join(_clean(text[j]) for j in source), words[0][1])
            if got:
                return got
    return None


LIST_JOINS = ("y", "e", "o", "u", "ni", "and", "or", "nor")
OPENERS = CONNECTORS | set("que ni ya asi por sino salvo excepto pues that so nor yet or".split())   # a clause, not an item
RESULT = set("por asi entonces luego so then".split())     # …but the last item may say what follows ("y por eso…")


def enumeration(text: str) -> list[str] | None:
    """3 to 6 short items said in a row ("A, B y C", "A, B, and C") or None. The shape of a list: commas
    between the items and one "y / o / and / or" before the last one only; no item opens like a clause
    ("pero…", "si…", "por eso…", "but…"), so a run of clauses is not taken for a list."""
    body = str(text).strip(" .?!¿¡…")
    chunks = [x.strip() for x in re.split(r"[,;]", body)]
    if any(not x for x in chunks) or len(chunks) < 2:
        return None
    join = re.compile(r"\s+(?:" + "|".join(LIST_JOINS) + r")\s+", re.IGNORECASE)
    last = chunks[-1]
    lead = re.match(r"(?:" + "|".join(LIST_JOINS) + r")\s+", last, re.IGNORECASE)   # "A, B, and C"
    result = "".join(tokens(last.split()[0])) in RESULT if last.split() else False
    first = [x.strip() for x in join.split(f" {chunks[0]} ") if x.strip()]
    if len(chunks) == 2 and result and len(first) == 2:         # "A y B, por eso C"
        parts = [*first, last]
    elif any(join.search(f" {x} ") for x in chunks[:-1]):        # "lo miras y nada, …": a join inside an item
        return None
    elif lead:
        parts = [*chunks[:-1], last[lead.end():].strip()]
    else:
        tail = [x.strip() for x in join.split(f" {last} ") if x.strip()]
        if len(tail) != 2:                                       # the last item comes after one "y / and"
            return None
        parts = [*chunks[:-1], *tail]
    if not 3 <= len(parts) <= 6 or any(len(x.split()) > 5 for x in parts):
        return None
    heads = ["".join(tokens(x.split()[0])) for x in parts]
    if any(h in OPENERS for h in heads[:-1]) or (heads[-1] in OPENERS and heads[-1] not in RESULT):
        return None
    if all(h in DETERMINERS for h in heads[1:]) and heads[0] not in DETERMINERS:     # "Necesitas una pantalla, un…"
        lead_in = parts[0].split()
        at = next((j for j, w in enumerate(lead_in) if "".join(tokens(w)) in DETERMINERS), None)
        if at:
            parts[0] = " ".join(lead_in[at:])                # the items are the things: "Una pantalla"
    return [x[:1].upper() + x[1:] for x in parts]


# ── the newer graphics, chosen by the director (each one a setting; domain/graphics/suggest.py) ────
CLI = set("sudo cd ls git npm npx pip pip3 python python3 brew apt curl wget grep mkdir chmod docker kubectl yarn "
          "cargo ssh".split())     # a command said aloud starts with one of these (not "make", "go", "and", "in"…)
SCREENSHOT_NAMES = ("screenshot", "screen shot", "captura", "pantallazo")     # a photo's name that says it is one
CAMERA_NAMES = ("img", "dsc", "pxl", "mvimg", "photo", "foto")                # a camera's own file names
SCREEN_SHAPE = 0.5          # width / height under this: a phone's screenshot (a camera's portrait is 0.5625 or 0.75)
SCREEN_STOP = set("screenshot screen shot captura capturas pantallazo pantalla de del la el los las at the a en".split())
SIDE_STOP = UNIT_STOP | LABEL_STOP | PRONOUNS | SUBJECTS | POSSESSIVES | PERIOD_NOUNS | set("todo todos toda todas".split())
TIME_ICONS = ("moon", "clock", "calendar")     # an illustration of when, not of a thing set against another
LABEL_MOST = 7              # words of a bar named by the clause before its figure, across two sentences…
LABEL_CHARS = 24            # …and letters (figures in digits: "De 7 a 9 de la noche")
CHART_SENTENCES = 3         # a line chart's years may be said over this many sentences


def _digit_words(words: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """The words with every figure said in words written in digits, as one word ("ciento sesenta y un"
    → "161", "dos mil quince" → "2015"), keeping its time and the punctuation after it (for the helpers
    of graphics/suggest.py, which read digits); "doce millones" stays "12 millones"."""
    said = [w for w, _ in words]
    plain = ["".join(tokens(w)) for w in said]
    out, i = [], 0
    while i < len(words):
        got = None if _digits(_clean(said[i]).rstrip("%")) is not None else _number(said, plain, i)
        if got is None:
            out.append(words[i])
            i += 1
            continue
        value, end = got
        tail = said[end - 1][len(said[end - 1].rstrip(".,;:?!…")):]
        scale = SCALES.get(plain[end - 1], 1) if end - 1 > i else 1
        if scale >= 10 ** 6 and value % scale == 0:
            out.extend([(_shown(value / scale), words[i][1]), words[end - 1]])
        else:
            out.append((_shown(value) + tail, words[i][1]))
        i = end
    return out


def code_for(words: list[tuple[str, float]]) -> dict | None:
    """A command said or written in the sentence → a `code` spec (with "at"): text between backticks, or
    a shell command (CLI: git, npm, brew, pip…) with its arguments; None otherwise."""
    if "`" not in " ".join(w for w, _ in words) and not any(_clean(w) in CLI for w, _ in words):
        return None
    return suggest.code_from_words(words)


def line_chart_for(words: list[tuple[str, float]]) -> dict | None:
    """Figures tied to three or more years / months in order (said in digits or in words) → a
    `line_chart` spec (with "at"), else None."""
    clean = [_clean(w) for w, _ in words]
    if not any(suggest.time_rank(w) for w in clean) and not any(p in SPELLED or p in SCALES for w in clean
                                                                 for p in tokens(w)):
        return None
    spec = suggest.line_chart_from_words(_digit_words(words))
    if spec is not None and not spec.get("unit"):              # "12 millones en 2015…": in millions
        scale = next((_clean(w) for w, _ in words if "".join(tokens(w)) in SCALES and SCALES["".join(tokens(w))] >= 10 ** 6), None)
        if scale:
            spec["unit"] = scale.lower()
    return spec


def _thing(words: list[tuple[str, float]], k: int) -> str | None:
    """Word k as (the start of) the name of a thing set against another: a name written with a capital
    inside the sentence ("iPhone", "Pixel") or a word with an illustration that is not a time
    ("celular", not "noche"); None otherwise."""
    raw = _clean(words[k][0])
    low = "".join(tokens(raw))
    if len(low) < 2 or low in SIDE_STOP or low in MARKERS or not raw.isalnum() or _digits(raw) is not None:
        return None
    capital = raw[:1].isupper() and (k > 0 and not words[k - 1][0].rstrip().endswith((".", "?", "!")))
    icon = _icon_of(raw)
    if capital or any(c.isupper() for c in raw[1:]) or (icon and icon not in TIME_ICONS):
        return raw
    return None


def _sides(words: list[tuple[str, float]], figures: list[dict]) -> list[str] | None:
    """The thing each figure is about, named in its own clause: the first thing named before it (with
    the name or illustrated word right after it: "el internet móvil" → "Internet móvil"), else the
    first one after it; None when one is not named or two are the same."""
    names = []
    closes = [w.rstrip().endswith(CLAUSE_END + (".", "?", "!")) for w, _ in words]
    for j, f in enumerate(figures):
        lo = figures[j - 1]["end"] if j else 0
        hi = figures[j + 1]["i"] if j + 1 < len(figures) else len(words)
        begin = f["i"]
        while begin > lo and not closes[begin - 1]:
            begin -= 1
        stretch = list(range(begin, f["i"]))
        if not any(_thing(words, k) for k in stretch):
            stretch = []
            for k in range(f["end"], hi):
                if closes[k - 1] if k > f["end"] else closes[f["end"] - 1]:
                    break
                stretch.append(k)
        first = next((k for k in stretch if _thing(words, k)), None)
        if first is None:
            return None
        picked = [first]
        while len(picked) < 2 and picked[-1] + 1 in stretch and not closes[picked[-1]] and _thing(words, picked[-1] + 1):
            picked.append(picked[-1] + 1)
        names.append(_capital(" ".join(_clean(words[k][0]) for k in picked)))
    return names if len({n.lower() for n in names}) == len(names) else None


def versus_for(words: list[tuple[str, float]]) -> dict | None:
    """Two named things set against each other with a figure each → a `versus` spec (with "at"):
    "el celular dura 20 horas frente a 9 horas del portátil" (vs, contra, frente a…: graphics/suggest.py),
    or two things with a proper name next to figures of the same kind ("El iPhone dura 20 horas y el
    Pixel 18"; "tu celular tiene 8 gigas y la app 2" stays bars: a part of a whole, not a match);
    None otherwise (bars, a figure or nothing)."""
    marked = suggest.versus_from_words(_digit_words(words))
    if marked is not None:
        return marked
    found = _figures(words, True)
    if len(found) != 2 or found[0]["unit"] != found[1]["unit"] or found[0]["value"] == found[1]["value"]:
        return None
    names = _sides(words, found)
    if not names or not all(_proper(n) for n in names):
        return None
    return _versus_rows(names, [(found[0], found[1])], words[found[0]["i"]][1])


def _proper(name: str) -> bool:
    """A name written with a capital on purpose ("iPhone", "Pixel", "Android"), not a noun capitalized
    as a label ("Celular")."""
    return any(any(c.isupper() for c in w[1:]) or w[:1].isupper() and not _icon_of(w) for w in name.split())


def _versus_rows(names: list[str], pairs: list[tuple[dict, dict]], at: float) -> dict | None:
    rows = []
    for a, b in pairs:
        label = _capital(a["said"] or a["unit"])
        rows.append((label, _shown(a["value"]), _shown(b["value"])))
    if len({r[0].lower() for r in rows}) < len(rows):
        return None
    spec = suggest.versus_spec(names[0], names[1], rows)
    if spec is not None:
        spec["at"] = at
    return spec


def _clause_label(words: list[tuple[str, float]], f: dict) -> str | None:
    """A bar's name across two sentences: the clause right before the figure's own one ("a otras horas,
    el internet va a 161 megas" → "Otras horas"; "De siete a nueve de la noche, a 54" → "De 7 a 9 de
    la noche"), else what is said before the figure in its clause; None when nothing names it."""
    ends = [k for k in range(f["i"]) if words[k][0].rstrip().endswith(CLAUSE_END + (".", "?", "!"))]
    own = ends[-1] + 1 if ends else 0
    plain = ["".join(tokens(w)) for w, _ in words]
    if ends and not words[ends[-1]][0].rstrip().endswith(":"):
        start = ends[-2] + 1 if len(ends) > 1 else 0
        pick = list(range(start, ends[-1] + 1))
        while pick and (plain[pick[0]] in LEADING or plain[pick[0]] in ("a", "en", "at", "in") and len(pick) > 1
                        and plain[pick[1]] not in DETERMINERS):      # "a otras horas" → "Otras horas", not "In the…"
            pick = pick[1:]
        text = " ".join(_clean(w) for w, _ in _digit_words([words[k] for k in pick]) if _clean(w))
        return _capital(text) if text and len(text.split()) <= LABEL_MOST and len(text) <= LABEL_CHARS else None
    pick = list(range(own, f["i"]))
    label, ks = _bar_label(words, plain, pick)
    return label if label and _names_thing(words, plain, ks, False) else None


def across(first: dict, second: dict, p: dict) -> dict | None:
    """Data said over two sentences in a row, shown once over both (with "at", the first figure's
    second): a figure and, in the next sentence, one whose unit is left unsaid or repeated ("a otras
    horas va a 161 megas. / De siete a nueve, a 54.") → `bars` named by their clauses, or `versus` when
    both are things with a name; years over the sentences → `line_chart`. None otherwise."""
    a = [(str(w[0]), float(w[1])) for w in first.get("words") or []]
    b = [(str(w[0]), float(w[1])) for w in second.get("words") or []]
    if not a or not b:
        return None
    fa = _figures(a, True)
    if p.get("figure_context") and len(fa) == 1:
        fb = _figures(b, True, prev=fa[0])
        plain_b = ["".join(tokens(w)) for w, _ in b]
        parallel = bool(fb) and (fb[0].get("elided") or plain_b[0] in LEADING | CONNECTORS
                                 or any("".join(tokens(w)) in MARKERS | COMPARING for w, _ in a + b))
        if len(fb) == 1 and fb[0]["unit"] == fa[0]["unit"] and fb[0]["value"] != fa[0]["value"] and parallel:
            if p.get("auto_versus"):
                names = [_sides(a, fa), _sides(b, fb)]
                if names[0] and names[1] and names[0][0].lower() != names[1][0].lower() \
                        and _proper(names[0][0]) and _proper(names[1][0]):
                    got = _versus_rows([names[0][0], names[1][0]], [(fa[0], fb[0])], a[fa[0]["i"]][1])
                    if got:
                        return got
            labels = [_clause_label(a, fa[0]), _clause_label(b, fb[0])]
            if all(labels) and labels[0].lower() != labels[1].lower():
                unit = SHORT_UNITS.get(fa[0]["unit"], fa[0]["said"])
                data = ", ".join(f"{lb}:{f['value']:g}" for lb, f in zip(labels, (fa[0], fb[0])))
                return {"template": "bars", "data": data, "unit": unit, "at": a[fa[0]["i"]][1]}
    if p.get("auto_versus"):                                # the same two things measured twice: rows of a versus
        rows = [_two(x) for x in (a, b)]
        if rows[0] and rows[1] and rows[0][2]["unit"] != rows[1][2]["unit"]:
            (n0, n1, f0, g0), (m0, m1, f1, g1) = rows
            if (m0.lower(), m1.lower()) == (n1.lower(), n0.lower()):
                m0, m1, f1, g1 = m1, m0, g1, f1
            if (m0.lower(), m1.lower()) == (n0.lower(), n1.lower()):
                return _versus_rows([n0, n1], [(f0, g0), (f1, g1)], a[f0["i"]][1])
    return None


def _two(words: list[tuple[str, float]]) -> tuple[str, str, dict, dict] | None:
    """Two named things with a figure each, of the same kind, in one sentence → (name, name, figure, figure)."""
    found = _figures(words, True)
    if len(found) != 2 or found[0]["unit"] != found[1]["unit"]:
        return None
    names = _sides(words, found)
    return (names[0], names[1], found[0], found[1]) if names else None


def chart_across(group: list[dict]) -> dict | None:
    """Years with their figures said over two or three sentences in a row (the first and the last of them
    each say a year) → a `line_chart` (with "at"), else None."""
    said = [[(str(w[0]), float(w[1])) for w in s.get("words") or []] for s in group]
    if len(group) < 2 or not all(any(suggest.time_rank(_clean(w)) for w, _ in x) for x in (said[0], said[-1])):
        return None
    return line_chart_for([w for x in said for w in x])


def screen_for(words: list[tuple[str, float]], screens) -> dict | None:
    """A screenshot of the project that the sentence names → a `screen` spec (with "at"). screens: the
    project's screenshots, names relative to the project ("captura_ajustes_wifi.png"). A file is named
    when every word of its name (without "captura", "screenshot", dates…) is said, and the name has two
    such words or the sentence also says "captura / pantalla / screenshot"; only one file may fit."""
    plain = ["".join(tokens(w)) for w, _ in words]
    said = set(plain) | {w[:-1] for w in plain if w.endswith("s")} | {w[:-2] for w in plain if w.endswith("es")}
    fits = []
    for name in screens or ():
        stem = str(name).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        need = [t for t in tokens(stem.replace("_", " ").replace("-", " ")) if len(t) >= 3 and not t.isdigit()
                and t not in SCREEN_STOP]
        if not need or not all(t in said for t in need):
            continue
        if len(need) < 2 and not said & {"captura", "pantalla", "pantallazo", "screenshot", "screen"}:
            continue
        at = next(t for (w, t), x in zip(words, plain) if x in need or x.rstrip("s") in need)
        fits.append((name, at))
    if len(fits) != 1:
        return None
    spec = suggest.screen_spec(fits[0][0])
    if spec is not None:
        spec["at"] = fits[0][1]
    return spec


def is_screenshot(name: str, size: tuple[int, int] | None = None) -> bool:
    """A picture that is a screenshot: its name says so ("Captura de pantalla…", "Screenshot…",
    "pantallazo") or it has a phone screen's shape (narrower than SCREEN_SHAPE) without a camera's name."""
    low = str(name).replace("\\", "/").rsplit("/", 1)[-1].lower().replace("_", " ").replace("-", " ")
    if not low.endswith(suggest.IMAGE_EXT):
        return False
    if any(w in low for w in SCREENSHOT_NAMES):
        return True
    return bool(size and size[1] and size[0] / size[1] < SCREEN_SHAPE and not low.startswith(CAMERA_NAMES))


def screen_cues(cutaways: list[dict], sizes: dict | None = None) -> list[dict]:
    """The plan's cutaways with every `photo` cue whose file is a screenshot (is_screenshot; sizes:
    file → (width, height) when known) turned into a `screen` graphic of that file ("_auto": "screen")."""
    out = []
    for c in cutaways:
        name = str(c.get("file") or "")
        if c.get("type") == "photo" and name and is_screenshot(name, (sizes or {}).get(name)):
            spec = suggest.screen_spec(name)
            if spec is not None:
                keep = {k: v for k, v in c.items() if k not in ("type", "file", "motion")}
                out.append({"type": "graphic", **spec, **keep, "_auto": "screen"})
                continue
        out.append(c)
    return out


def _node_icon(name: str) -> str | None:
    hits = [i for w in name.split() if (i := _icon_of(w)) in ICONS]
    return hits[-1] if hits else None


def diagram_for(words: list[tuple[str, float]]) -> dict | None:
    """A sentence that describes a flow between things → a `diagram` spec (with "at"): things that branch
    or loop back (graphics/suggest.flow_from_words), else the chain domain/flow.py reads ("tu celular
    manda la foto a la antena y de ahí al servidor"), when two or more of its stops have an illustration.
    What travels labels the first arrow. None otherwise."""
    spec = suggest.flow_from_words(words)
    if spec is not None:
        return spec
    found = flows.flow(words)
    if found is None or len(found.nodes) > 5:
        return None
    icons = [_node_icon(n) for n in found.nodes]
    if sum(i is not None for i in icons) < 2 or any(":" in n or ";" in n or "," in n for n in found.nodes):
        return None
    spec = {"template": "diagram", "nodes": ", ".join(found.nodes), "icons": ", ".join(i or "-" for i in icons)}
    if found.carries:
        pairs = list(zip(found.nodes, found.nodes[1:]))
        spec["edges"] = "; ".join(f"{x}>{y}" + (f":{found.carries}" if j == 0 else "") for j, (x, y) in enumerate(pairs))
    spec["at"] = words[found.at][1]
    return spec


def scene_for(words: list[tuple[str, float]], at: float, main: str, decide: Decide | None = None,
              language: str | None = None) -> dict | None:
    """An illustrated scene (graphics/scenes.py) when the sentence says what something does: the verb
    said at or after the cut first, else the one before it. With `decide`, a model may pick the action
    among all of them, or none (`action`), and the title (`title_span`)."""
    rules = _scene_for(words, at, main, None)
    if decide is None:
        return rules
    asker = _Asker(decide, language_of(words, language))
    return _decided(_scene_for(words, at, main, asker), rules, asker)


def _scene_for(words: list[tuple[str, float]], at: float, main: str, asker: _Asker | None, fit: bool = False) -> dict | None:
    cut = next((k for k, (_, t) in enumerate(words) if t >= at - 1e-6), 0)
    found = action_of([w for w, _ in words[cut:]])
    if found:
        found = (found[0], found[1] + cut)
    else:
        found = action_of([w for w, _ in words])
    if asker is not None:
        said = actions_in([w for w, _ in words])
        clear = bool(found) and any((a, i) == found and plain for a, i, plain in said)
        rules = found[0] if found else "none"
        a = asker("action", "choice", [Option(x, action_text(x, asker.language)) for x in (*ACTIONS, "none")],
                  _said(words), rules, 0.85 if clear else 0.5, ABOUT)
        if a.value != rules:
            where = [i for x, i, _ in said if x == a.value]
            found = None if a.value == "none" else (a.value, (where or [cut])[0])
    if not found:
        return None
    action, where = found
    subject, target, _ = ACTIONS[action]
    if fit and action in OBJECTS:                           # "abren la ducha": opening something else, not an app
        obj = next((k for k in range(where + 1, min(where + 4, len(words)))
                    if "".join(tokens(words[k][0])) not in DETERMINERS), None)
        thing = _icon_of(words[obj][0]) if obj is not None else None
        if thing and thing not in OBJECTS[action]:
            return None
    named = sorted((abs(k - where), i) for k, (w, _) in enumerate(words) if (i := _icon_of(w)))
    things = [i for _, i in named]
    if action == "drain":
        subject = next((i for i in things if i != "battery"), None)
    elif action != "charge":
        fit = [i for i in things if i in OWNERS.get(action, ICON_DEVICES)]
        subject = fit[0] if fit else subject
    if action in ("send", "store"):
        others = [i for i in things if i != subject]
        target = "ram" if action == "store" and "ram" in others else (others[0] if others else target)
    elif action in TARGETED and action not in ("swipe", "open"):    # the other thing named ("en el celular")
        others = [i for i in things if i != subject and i in TARGET_OWN.get(action, ICONS)]
        target = others[0] if others else target
    spec = {"template": "scene", "action": action, "text": _phrase(words, at, PHRASE_WORDS, asker)}
    if subject:
        spec["subject"] = subject
    if target and action in TARGETED:
        spec["target"] = target
    return spec


def local_query(words: list[tuple[str, float]], at: float, icon: str | None, language: str = "es") -> str:
    """A search in the video's own language from what is said: the words that name things (a word with an
    illustration, or a noun after an article or a possessive — the same reading as the notes), up to three."""
    from .support import sentence_notes

    notes = sentence_notes([[w, t, t] for w, t in words], language)
    found = []
    for n in notes:
        token = "".join(tokens(n["label"]))
        if n["kind"] in ("icon", "word") and token not in LOCAL_STOP and token not in found:
            found.append(token)
    return " ".join(found[:3])


def card(sentence: dict, at: float | None = None, main: str = TOPIC_DEFAULT, last: str | None = None,
         decide: Decide | None = None, language: str | None = None, about: str | None = None,
         opts: dict | None = None, screens=()) -> dict:
    """What the engine draws for a sentence when no footage is good enough, by what the sentence says:
    a figure → `number`; three or more items → `list`; something doing something → `scene` (an animated
    illustration); else a `title` with the illustration of what it names. `last`: the template used
    just before — another one is chosen when there is one, so the same kind does not come twice.
    With `decide`, a model may change each judgment (title, illustration, action, data) and pick the
    drawing among those that fit (`card_type`); language: the video's ("es", "en"; None = guessed).
    opts: the director's settings (settings(cfg)): the newer graphics it may choose (said_data) and a flow
    between illustrated things → `diagram`, before a scene; without opts, none of them."""
    rules = _card(sentence, at, main, last, None, opts, screens)
    if decide is None:
        return rules
    asker = _Asker(decide, language_of(sentence.get("words") or [], language), about)
    return _decided(_card(sentence, at, main, last, asker, opts, screens), rules, asker)


def describe(spec: dict, language: str = "es") -> str:
    """A drawing in one line, with what it says (for a model that picks one)."""
    es = language == "es"
    kind = spec.get("template")
    if kind == "number":
        said = " ".join(str(spec.get(k) or "") for k in ("label", "value", "unit", "sub")).split()
        return ("una cifra grande: " if es else "a big figure: ") + " ".join(said)
    if kind == "bars":
        return ("barras que comparan: " if es else "bars comparing: ") + f"{spec.get('data')} {spec.get('unit') or ''}".strip()
    if kind == "headline":
        return (f"un titular de noticia: «{spec.get('text')}» ({spec.get('outlet')})" if es
                else f"a news headline: “{spec.get('text')}” ({spec.get('outlet')})")
    if kind == "list":
        return ("una lista: " if es else "a list: ") + str(spec.get("items"))
    if kind == "diagram":
        return ("un diagrama de flechas: " if es else "a diagram with arrows: ") + str(spec.get("nodes")).replace(", ", " → ")
    if kind == "line_chart":
        return ("una línea en el tiempo: " if es else "a line over time: ") + str(spec.get("data"))
    if kind == "versus":
        return (f"{spec.get('left')} contra {spec.get('right')}: " if es else f"{spec.get('left')} versus {spec.get('right')}: ") \
            + str(spec.get("rows"))
    if kind == "code":
        return ("un comando: " if es else "a command: ") + str(spec.get("lines"))
    if kind == "screen":
        return ("una captura de pantalla: " if es else "a screenshot: ") + str(spec.get("image"))
    if kind == "scene":
        who = describe_icon(spec["subject"], language) if spec.get("subject") else ""
        what = action_text(str(spec.get("action")), language)
        return (f"una escena animada: {who}, {what}" if es else f"an animated scene: {who}, {what}") if who else \
            (f"una escena animada: {what}" if es else f"an animated scene: {what}")
    icon = f" ({describe_icon(spec['icon'], language)})" if spec.get("icon") else ""
    return (f"un título: «{spec.get('text')}»" if es else f"a title: “{spec.get('text')}”") + icon


def _title_icon(words: list[tuple[str, float]], at: float, main: str, asker: _Asker | None) -> str | None:
    """The illustration above a title card: the first thing named from the cut on, else in the sentence,
    else the script's topic (TOPIC_ICON: the app's picture alone reads as a photo, the phone is shown).
    With a decider, a model may pick another of the things named, the topic's, or none (`icon`)."""
    hits = [(w, i) for w, t in words if t >= at - 1e-6 and (i := _icon_of(w))] or \
        [(w, i) for w, t in words if (i := _icon_of(w))]
    if not hits:
        return TOPIC_ICON.get(main, main)
    icon = hits[0][1]
    if asker is None or icon not in ICONS:
        return icon
    confidence = 0.9 if (match_icon(hits[0][0]) or (None, False))[1] else 0.6
    named = list(dict.fromkeys(i for w, _ in words if (i := _icon_of(w)) in ICONS))
    shortlist = list(dict.fromkeys([*named, icon, *([main] if main in ICONS else [])]))
    a = asker("icon", "choice", [Option(x, describe_icon(x, asker.language)) for x in (*shortlist, "none")],
              _said(words), icon, confidence, ABOUT)
    return None if a.value == "none" else a.value


def _card(sentence: dict, at: float | None, main: str, last: str | None, asker: _Asker | None,
          opts: dict | None = None, screens=()) -> dict:
    p = opts or OFF
    words = [(str(w[0]), float(w[1])) for w in sentence.get("words") or []]
    at = float(sentence.get("start") or 0) if at is None else at
    title = {"template": "title", "text": _phrase(words, at, PHRASE_WORDS, asker) or str(sentence.get("text") or "")}
    icon = _title_icon(words, at, main, asker)
    if icon:
        title["icon"] = icon
    items = enumeration(sentence.get("text") or "")
    data = _said_data(words, asker, p, screens)
    data = {k: v for k, v in data.items() if k != "at"} if data else None
    flow = diagram_for(words) if p.get("auto_diagram") else None
    flow = {k: v for k, v in flow.items() if k != "at"} if flow else None
    options = [x for x in (data, figure(words, bool(p.get("figure_context"))),
                           {"template": "list", "items": "; ".join(items), "marks": "n"} if items else None,
                           flow, _scene_for(words, at, main, asker, bool(p.get("scene_fit"))), title) if x]
    pool = [x for x in options if x["template"] != last] or options
    if asker is None or len(pool) < 2:
        return pool[0]
    strong = [x for x in pool if x["template"] != "title"]
    a = asker("card_type", "choice", [Option(x["template"], describe(x, asker.language)) for x in pool],
              _said(words), pool[0]["template"], 0.85 if len(strong) <= 1 else 0.6, where=_from(words, at))
    return next((x for x in pool if x["template"] == a.value), pool[0])


def covered(plan: dict, overlay: set[str]) -> set[int]:
    """Sentences under a full-frame cutaway of the plan (overlays such as notes or captions do not count)."""
    out: set[int] = set()
    for c in plan.get("cutaways") or []:
        kind = str(c.get("type") or "graphic")
        if kind not in COVERING or (kind == "graphic" and str(c.get("template") or "") in overlay):
            continue
        if c.get("at_sentence") is not None:
            a = int(c["at_sentence"])
            out.update(range(a, int(c.get("until_sentence", a)) + 1))
    return out


SHOWN = ("number", "diagram", "line_chart", "versus", "code", "screen")   # drawn at once, never searched for


def pictureless_sources(plan: dict) -> set[int]:
    """The sources of the plan with sound and no picture (voice memos: "pictureless", written by the
    explainer plan from the media infos)."""
    return {int(k) for k in plan.get("pictureless") or []}


def propose(plan: dict, cfg: dict, overlay: set[str] = frozenset(), decide: Decide | None = None,
            screens=(), pictureless: set[int] | None = None) -> list[dict]:
    """b-roll cutaways for the stretches the presenter is alone on camera for too long. The plan is
    not changed. overlay: the templates drawn over the picture (they do not cover the presenter).
    screens: the project's screenshots (names relative to the project) a sentence may name.
    pictureless: the sources with no picture (a voice memo; default: the plan's "pictureless"): there
    is no presenter to show, so every sentence of theirs gets something on screen from its start, the
    closing ones too (no camera time at all)."""
    p = settings(cfg)
    limit_open, limit, shortest = float(p["opening"]), float(p["max_camera"]), float(p["auto_min"])
    sentences = [s for s in plan.get("sentences") or []
                 if isinstance(s, dict) and not s.get("missing") and s.get("start") is not None and s.get("words")]
    if not sentences:
        return []
    blind = pictureless_sources(plan) if pictureless is None else set(pictureless)
    under = covered(plan, overlay)
    main = topic(sentences)
    language = language_of([w for s in sentences for w in s["words"]], (cfg or {}).get("language"))
    about = str(sentences[0].get("text") or "").strip() or None      # the opening line says what the video is about
    turns: Counter = Counter()
    last_query = None
    last: str | None = None
    out: list[dict] = []
    clock, opening = 0.0, True
    done = -1                                               # sentences already shown by a graphic over several
    for k, s in enumerate(sentences):
        if k <= done:
            continue
        n, start, end = int(s["n"]), float(s["start"]), float(s["end"])
        if n in under:
            clock, opening = 0.0, False
            continue
        memo = int(s.get("source", 0)) in blind
        closing = k == len(sentences) - 1 and not memo
        allowed = 0.0 if memo else (limit_open if opening else limit)
        words = [(str(w[0]), float(w[1])) for w in s["words"]]
        data = said_data(words, decide, language, p, screens) if not closing else None
        until = k
        if data is None and not closing:                    # said over the next sentence(s): shown once over them
            data, until = _over_next(sentences, k, under, blind, p)
        if data and data["template"] != last:              # data said aloud is shown as it is said
            at = max(float(data.pop("at")), start + max(MIN_FACE - clock, LEAD_IN))
            due = start + max(allowed - clock, MIN_FACE - clock, LEAD_IN)
            if at > due + 1e-6:                             # waiting for the figure would keep the face too long
                at = next((t for w, t in words if due - 1e-6 <= t <= at), due)
            stop = float(sentences[until]["end"])
            if memo or stop - at < shortest:
                at = start if memo else start + LEAD_IN
            if stop - at >= shortest or memo:
                said = next((w for w, t in words if t >= at - 1e-6), words[0][0])
                out.append({"type": "graphic", **data, "at_sentence": n, "until_sentence": int(sentences[until]["n"]),
                            "from_word": [s["source"], round(at, 3)], "note": f"«{said.strip('¿?¡!.,;:')}»",
                            "_auto": "director"})
                last = data["template"]
                clock, opening = 0.0, False
                done = until
                continue
        if closing or (not memo and clock + (end - start) <= allowed):
            clock += end - start
            continue
        if memo and end - start < shortest and out and out[-1].get("_auto") == "director" and k \
                and int(out[-1]["until_sentence"]) == int(sentences[k - 1]["n"]) \
                and sentences[k - 1].get("source") == s.get("source"):
            out[-1]["until_sentence"] = n                   # too short to show on its own: the one before goes on
            continue
        earliest = start + max(allowed - clock, 0.0)
        if memo or (clock >= allowed and end - start >= shortest):   # already over (or no face): from its start
            room = [(words[0][0], start)]
        else:                                                       # a word after the face had its moment
            first = start + max(MIN_FACE - clock, LEAD_IN)
            room = [(w, t) for w, t in words if t >= first - 1e-6 and end - t >= shortest]
            if not room and clock >= MIN_FACE and end - start >= shortest:
                room = [(words[0][0], start)]                   # no word in reach: from the sentence's start
        if not room:
            clock += end - start
            continue
        late = [x for x in room if x[1] >= earliest - 1e-6]
        usable = late or [room[-1]]                                  # the closest one to the limit
        named = [(w, t, i) for w, t in usable if (i := _icon_of(w))]
        word, at, icon = named[0] if named else (*usable[0], None)
        if icon is None:                                    # what the sentence is about, named before the cut
            said = [i for w, t in words if t <= at and (i := _icon_of(w))]
            icon = said[-1] if said else main
        options = QUERIES.get(icon) or QUERIES[TOPIC_DEFAULT]
        query = options[turns[icon] % len(options)]
        if query == last_query and len(options) > 1:
            turns[icon] += 1
            query = options[turns[icon] % len(options)]
        turns[icon] += 1
        last_query = query
        drawn = card(s, at, main, last, decide=decide, language=language, about=about, opts=p, screens=screens)
        drawn = dress(drawn, [w for w, _ in words], language)      # a pack may complete it (the place of a scene…)
        stretch = n
        if memo and end - start < shortest and k + 1 < len(sentences) and int(sentences[k + 1]["n"]) not in under \
                and sentences[k + 1].get("source") == s.get("source"):
            stretch, done = int(sentences[k + 1]["n"]), k + 1      # too short alone: it goes on over the next one
        where = {"at_sentence": n, "until_sentence": stretch, "from_word": [s["source"], round(at, 3)],
                 "note": f"«{word.strip('¿?¡!.,;:')}»", "_auto": "director"}
        if drawn.get("_decided"):
            where["_decided"] = drawn["_decided"]
        if drawn["template"] in SHOWN:                      # a figure, a flow… is shown, not searched for
            out.append({"type": "graphic", **drawn, **where})
        else:
            out.append({"type": "broll", "query": query, "query_lang": "en",
                        "query_local": local_query(words, at, icon, str(cfg.get("language", "es"))),
                        "said": str(s.get("text") or ""), **where, "fallback": drawn})
        last = drawn["template"]
        clock, opening = 0.0, False
    return out


def _over_next(sentences: list[dict], k: int, under: set[int], blind: set[int], p: dict) -> tuple[dict | None, int]:
    """Data said over sentence k and the next one or two (across(), chart_across()) → (spec, index of the
    last sentence it covers), or (None, k). The closing sentence of a video with a presenter is left out."""
    last = len(sentences) - 1
    group = [k]
    for j in range(k + 1, min(k + CHART_SENTENCES, len(sentences))):
        nj = int(sentences[j]["n"])
        if nj in under or (j == last and int(sentences[j].get("source", 0)) not in blind) \
                or nj != int(sentences[j - 1]["n"]) + 1 or sentences[j].get("source") != sentences[k].get("source"):
            break
        group.append(j)
    if len(group) < 2:
        return None, k
    if p.get("auto_line_chart"):
        for size in range(2, len(group) + 1):
            got = chart_across([sentences[j] for j in group[:size]])
            if got is not None:
                return got, group[size - 1]
    got = across(sentences[k], sentences[group[1]], p)
    return (got, group[1]) if got is not None else (None, k)
