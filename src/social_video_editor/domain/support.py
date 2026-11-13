"""Support visuals proposed by the engine: while the presenter is alone on screen, small notes pop
up around them as things are named (the `callouts` overlay), so the picture never sits still.

Rules, per sentence said on camera (no cue of its own), using the script's words and when each was
said:
  1. a figure ("20", "2 minutes") becomes a bold label;
  2. a word that names an illustration (phone, memory, battery…) becomes that illustration with the
     word as its label;
  3. a noun — a word right after an article or a possessive ("the warehouse", "tu mesa") — becomes a
     label, where there is room between the figures and illustrations;
  4. a long word that reads well alone ("asleep", "dormidas") becomes a label too, where there is still
     room: the words that carry the sentence pop up as they are said. A sentence with nothing at all
     to show, after a quiet moment, settles for a shorter one.
Notes are grouped in one overlay per stretch (up to six notes, a few seconds) and each one appears
when its word is said. Every proposal carries "_auto": "support" so it can be told apart, edited or
removed in plan.json; `script.support: false` turns them off.
"""
from __future__ import annotations

import re

from .graphics.icons import ALONE, find_icon
from .script import tokens

MAX_ITEMS = 6               # notes in one overlay (the callouts template has six places)
MAX_PER_SENTENCE = 4
MIN_GAP = 0.6               # seconds between two notes
GROUP_SECONDS = 7.0         # an overlay holds its notes at most this long before starting over
QUIET_SECONDS = 1.0         # a sentence with nothing to show gets a key word when nothing appeared for this long
ORDER = (("figure", "icon"), ("word",), ("emphasis",))   # who takes their place first; the rest fill the room left
SHORT_KEY_WORD = 5          # letters of the word a sentence with nothing else to show may use
KEY_WORD_LETTERS = 6        # shortest word that can stand alone as a note…
NOUN_LETTERS = 4            # …unless it follows an article or a possessive (a noun)
DETERMINERS = {"es": set("el la los las un una unos unas tu tus su sus mi mis este esta estos estas ese esa cada otro otra".split()),
               "en": set("the a an your my his her its our their this that these those each another".split())}
# "la", "las", "los" are also pronouns ("si las cierras", "no los uses"): after one of these words they are
PRONOUN_AFTER = {"es": (set("la las los".split()), set("si que no se y ya cuando como nunca siempre pero porque me te nos yo".split())),
                 "en": (set(), set())}
OPENING_SECONDS = 1.0       # nothing over the presenter's face at the very start of the video
LAST_SECONDS = 0.5          # a note needs at least this long before the presenter stops being alone on screen
_FIGURE = re.compile(r"^[¿¡(\"']*([-+]?\d[\d.,]*\s?%?)[)\"'.,;:!?]*$")
_EDGES = "¿?¡!.,;:()\"'«»…"
# words that read badly alone: verbs with a pronoun stuck to them, gerunds, adverbs ("traerlas", "abriendo", "really")
_WEAK = {"es": re.compile(r"(?:[aei]r(?:l[aeo]s?|[mts]e|nos)|[ai]ndo|iendo|mente)$"),
         "en": re.compile(r"(?:ing|ly)$")}
STOPWORDS = {
    "es": set("""a y e u al algo ahora ahi alli aqui asi aun aunque cada casi como con contra cual cuando cuanto cuantas cuantos de del
desde donde dos el ella ellas ellos en entonces entre era eres es esa esas ese eso esos esta estan estas este esto estos
fue ha hace hacer hacia han hasta hay la las le les lo los mas me mi mientras mis mucho muy nada ni no nos nosotros o os
otra otro para pero poco por porque que quien se sea ser si siempre sin sobre solo son su sus tambien tan tanto te tiene
tienen tienes toda todas todo todos tu tus un una uno unas unos usted ustedes va vamos van vez ya yo cuesta puede pueden
tener tenemos seguro realmente simplemente despues antes mismo misma tantas tantos tambien ademas
ningun ninguna alguna alguno algunos algunas nuestro nuestra nuestros nuestras cualquier ustedes
todavia quiza quizas mientras durante aunque""".split()),
    "en": set("""a to of or an about after again all also an and any are as at be because been before being but by can could did do does
doing down each even every for from had has have having he her here him his how i if in into is it its just like more
most much my no not now of off on once one only or other our out over really same she so some such than that the their
them then there these they this those through to too under until up very was we were what when where which while who
why will with would you your yours actually simply always never another because between
without against during however although anything something everything nothing themselves yourself""".split()),
}


def _clean(word: str) -> str:
    return word.strip(_EDGES + " ")


def _label(word: str) -> str:
    w = _clean(word)
    return w[:1].upper() + w[1:]


def sentence_notes(words: list[list], language: str = "es", key_word: bool = False) -> list[dict]:
    """Notes a sentence offers, in the order they are said: [{"label", "icon" | None, "t", "kind"}]
    (t = source seconds its word starts; kind = figure | icon | word (a noun) | emphasis (a long word)).
    words: [[written word, start, end]] (plan sentences' "words"). key_word: when it offers nothing,
    its longest word of SHORT_KEY_WORD letters or more."""
    stop = STOPWORDS.get(language, STOPWORDS["en"])
    weak = _WEAK.get(language, _WEAK["en"])
    named = DETERMINERS.get(language, DETERMINERS["en"])
    pronouns, openers = PRONOUN_AFTER.get(language, PRONOUN_AFTER["en"])
    lower = [_clean(w).lower() for w, _s, _e in words]                    # as written (accents kept: "tú" is not "tu")
    plain = ["".join(tokens(w)) if w.isalpha() else "" for w in lower]
    out, used, unit = [], set(), -1
    for i, (word, start, _end) in enumerate(words):
        if i == unit:                                         # already shown as the unit of a figure
            continue
        figure = _FIGURE.match(word)
        if figure:
            label = figure.group(1).strip()
            if i + 1 < len(words) and lower[i + 1].isalpha() and len(lower[i + 1]) >= 2 and plain[i + 1] not in stop:
                label, unit = f"{label} {_clean(words[i + 1][0])}", i + 1     # "2 minutes", "20 apps"
            out.append({"label": label, "icon": None, "t": float(start), "kind": "figure"})
            continue
        token = "".join(tokens(lower[i]))
        if len(token) < 3 or token in stop or token in ALONE:
            continue
        icon = find_icon(lower[i])
        if icon:
            if icon not in used:
                used.add(icon)
                out.append({"label": _label(word), "icon": icon, "t": float(start), "kind": "icon"})
            continue
        before = lower[i - 1] if i else ""
        noun = (before in named and len(token) >= NOUN_LETTERS and plain[i] and not weak.search(token)
                and not (before in pronouns and i >= 2 and lower[i - 2] in openers))
        if noun and token not in used:
            used.add(token)
            out.append({"label": _label(word), "icon": None, "t": float(start), "kind": "word"})
    taken = {n["t"] for n in out}
    good = [(len(t), i) for i, t in enumerate(plain) if t and t not in stop and not weak.search(t)
            and float(words[i][1]) not in taken and i != unit]
    out += [{"label": _label(words[i][0]), "icon": None, "t": float(words[i][1]), "kind": "emphasis"}
            for n, i in good if n >= KEY_WORD_LETTERS]
    if not out and key_word:
        short = [x for x in good if x[0] >= SHORT_KEY_WORD]
        if short:
            i = max(short)[1]                                 # the longest; the later one among equals
            out.append({"label": _label(words[i][0]), "icon": None, "t": float(words[i][1]), "kind": "emphasis"})
    return sorted(out, key=lambda n: n["t"])


def _pick(notes: list[dict], last: float | None) -> list[dict]:
    """The notes of a sentence that fit: figures and illustrations first, then nouns, then long words,
    each where there is MIN_GAP of room on both sides; at most MAX_PER_SENTENCE, in the order said.
    last: when the previous note appeared (same source), or None."""
    chosen: list[dict] = []

    def room(note: dict) -> bool:
        if last is not None and 0 <= note["t"] - last < MIN_GAP:
            return False
        return all(abs(note["t"] - c["t"]) >= MIN_GAP for c in chosen)

    for kinds in ORDER:
        for note in notes:
            if note["kind"] in kinds and len(chosen) < MAX_PER_SENTENCE and room(note):
                chosen.append(note)
    return sorted(chosen, key=lambda n: n["t"])


def propose(plan: dict, language: str = "es") -> list[dict]:
    """Cutaway entries (`callouts` overlays) for the sentences said on camera with nothing else on
    screen. The plan is not changed. Sentences under a cutaway of the plan are left alone; a sentence
    whose cutaway starts at one of its words (from_word) keeps notes on its part said on camera."""
    taken: set[int] = set()
    cover_from: dict[int, tuple[int, float]] = {}          # sentence → (source, second) a cutaway covers it from
    for c in plan.get("cutaways") or []:
        if c.get("at_sentence") is None or c.get("_auto") == "support":
            continue
        a = int(c["at_sentence"])
        u = int(c.get("until_sentence", a))
        if c.get("from_word"):
            src, t = int(c["from_word"][0]), float(c["from_word"][1])
            if a not in cover_from or t < cover_from[a][1]:
                cover_from[a] = (src, t)
            taken.update(range(a + 1, u + 1))
        else:
            taken.update(range(a, u + 1))
    out: list[dict] = []
    group: dict | None = None
    recent: set[str] = set()               # labels of the previous overlay: not repeated right away
    last_note = None                       # (source, seconds) of the last note placed
    clock = 0.0                            # seconds of speech since the start (sentences found, in order)

    def flush() -> None:
        nonlocal group, recent
        if group and group["items"]:
            out.append({"type": "graphic", "template": "callouts", "items": group["items"],
                        "icons": [n or "-" for n in group["icons"]], "item_times": group["times"],
                        "at_sentence": group["first"], "until_sentence": group["last"], "_auto": "support"})
            recent = {x.lower() for x in group["items"]}
        group = None

    def on_camera(s) -> bool:
        return (isinstance(s, dict) and not s.get("missing") and bool(s.get("words")) and s.get("start") is not None
                and int(s["n"]) not in taken)

    sentences = plan.get("sentences") or []
    for k, s in enumerate(sentences):
        if not on_camera(s):
            flush()
            if isinstance(s, dict) and s.get("start") is not None and s.get("end") is not None:
                clock += float(s["end"]) - float(s["start"])
            continue
        n = int(s["n"])
        cut = cover_from.get(n)
        until = cut[1] if cut and cut[0] == s["source"] else float(s["end"])   # a cutaway covers the rest from there
        length = until - float(s["start"])
        alone_after = cut is None and k + 1 < len(sentences) and on_camera(sentences[k + 1])   # the next one starts on camera
        if group and (clock - group["since"] > GROUP_SECONDS or len(group["items"]) >= MAX_ITEMS):
            flush()
        same = last_note is not None and last_note[0] == s["source"]
        quiet = not same or float(s["start"]) - last_note[1] > QUIET_SECONDS
        shown = {x.lower() for x in group["items"]} if group else set()
        offered = [note for note in sentence_notes(s["words"], language, key_word=quiet and length >= 1.2)
                   if clock + (note["t"] - float(s["start"])) >= OPENING_SECONDS
                   and note["t"] < until
                   and (alone_after or until - note["t"] >= LAST_SECONDS)
                   and note["label"].lower() not in recent and note["label"].lower() not in shown]
        for note in _pick(offered, last_note[1] if same else None):
            if group and len(group["items"]) >= MAX_ITEMS:
                flush()
            if group is None:
                group = {"items": [], "icons": [], "times": [], "first": n, "last": n, "since": clock}
            group["items"].append(note["label"])
            group["icons"].append(note["icon"])
            group["times"].append([s["source"], round(note["t"], 3)])
            last_note = (s["source"], note["t"])
        if group:
            group["last"] = n
        clock += float(s["end"]) - float(s["start"])
        if cut is not None:                    # the picture changes inside this sentence: the notes end with it
            flush()
    flush()
    return out
