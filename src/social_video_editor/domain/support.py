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
The rules know no grammar, so a verb can pass for a noun or a long word ("they wait", "close it"). Each
candidate has a confidence (a figure or an illustration is sure; a noun that ends like a verb form, a
long word or a key word is not): when a sentence has unsure candidates, one `label_words` question asks
the decider (domain/decide.py) which of them make good notes. Without a decider the rules keep them all.
Notes are grouped in one overlay per stretch (up to six notes, a few seconds) and each one appears
when its word is said. Every proposal carries "_auto": "support" so it can be told apart, edited or
removed in plan.json; `script.support: false` turns them off.
"""
from __future__ import annotations

import re

from .decide import Decide, Option, Question, ask
from .graphics.icons import ALONE, find_icon
from .script import tokens

MAX_ITEMS = 6               # notes in one overlay (the callouts template has six places)
MAX_PER_SENTENCE = 4
MAX_EXTRA = 6               # other words of a sentence offered to the decider (to replace a verb with a noun)
EXTRA_CONFIDENCE = 0.6      # the rules leave those out; a question is asked when a sentence offers some
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

# how sure the rules are that a candidate makes a good note; under SURE the decider is asked about it
CONFIDENCE = {"figure": 1.0, "icon": 0.95, "word": 0.85, "verb_like": 0.5, "emphasis": 0.5, "key_word": 0.4}
SURE = 0.8
# endings of verb forms (and verbs with a pronoun stuck to them): a "noun" ending so may be a verb ("las guardan")
_VERB_LIKE = {"es": re.compile(r"(?:an|en|ar|er|ir|l[aeo]s?|te|se|me|nos)$")}
# The question is which words are conjugated verbs (a precise, grammatical question a language model
# answers well): every other word stays a note. Asking "which words make good notes" instead made the
# model keep one word or none, and the presenter went without notes.
PROMPT = {"es": "Marca TODAS las palabras de esta lista que en la frase funcionan como verbo conjugado (por ejemplo "
                "«corres», «guardan», «gasta», «vaya», «usan», «dímelo»); puede haber varias. Un sustantivo, un "
                "adjetivo, un participio usado como adjetivo, un infinitivo o un número no cuentan.",
          "en": "Mark EVERY word of this list that works as a conjugated verb in the sentence (for example «closes», "
                "«keeps», «spends», «uses»); there may be several. A noun, an adjective, a participle used as an "
                "adjective, an infinitive or a number does not count."}


def _clean(word: str) -> str:
    return word.strip(_EDGES + " ")


def _label(word: str) -> str:
    w = _clean(word)
    return w[:1].upper() + w[1:]


def sentence_notes(words: list[list], language: str = "es", key_word: bool = False,
                   decide: Decide | None = None) -> list[dict]:
    """Notes a sentence offers, in the order they are said: [{"label", "icon" | None, "t", "kind"}]
    (t = source seconds its word starts; kind = figure | icon | word (a noun) | emphasis (a long word)).
    words: [[written word, start, end]] (plan sentences' "words"). key_word: when it offers nothing,
    its longest word of SHORT_KEY_WORD letters or more. decide: asked which unsure candidates to keep."""
    return _notes(words, language, key_word, decide)[0]


def _candidates(words: list[list], language: str, key_word: bool) -> list[dict]:
    """Every note the rules offer, each with "conf" (CONFIDENCE) and "word" (as written)."""
    stop = STOPWORDS.get(language, STOPWORDS["en"])
    weak = _WEAK.get(language, _WEAK["en"])
    verb_like = _VERB_LIKE.get(language)
    named = DETERMINERS.get(language, DETERMINERS["en"])
    pronouns, openers = PRONOUN_AFTER.get(language, PRONOUN_AFTER["en"])
    lower = [_clean(w).lower() for w, _s, _e in words]                    # as written (accents kept: "tú" is not "tu")
    plain = ["".join(tokens(w)) if w.isalpha() else "" for w in lower]
    out, used, unit = [], set(), -1

    def note(i: int, label: str, icon: str | None, kind: str, conf: float) -> dict:
        return {"label": label, "icon": icon, "t": float(words[i][1]), "kind": kind, "conf": conf,
                "word": _clean(words[i][0])}

    for i, (word, start, _end) in enumerate(words):
        if i == unit:                                         # already shown as the unit of a figure
            continue
        figure = _FIGURE.match(word)
        if figure:
            label = figure.group(1).strip().rstrip(".,")
            if i + 1 < len(words) and lower[i + 1].isalpha() and len(lower[i + 1]) >= 2 and plain[i + 1] not in stop:
                label, unit = f"{label} {_clean(words[i + 1][0])}", i + 1     # "2 minutes", "20 apps"
            out.append(note(i, label, None, "figure", CONFIDENCE["figure"]))
            continue
        token = "".join(tokens(lower[i]))
        if len(token) < 3 or token in stop or token in ALONE:
            continue
        icon = find_icon(lower[i])
        if icon:
            if icon not in used:
                used.add(icon)
                out.append(note(i, _label(word), icon, "icon", CONFIDENCE["icon"]))
            continue
        before = lower[i - 1] if i else ""
        noun = (before in named and len(token) >= NOUN_LETTERS and plain[i] and not weak.search(token)
                and not (before in pronouns and i >= 2 and lower[i - 2] in openers))
        if noun and token not in used:
            used.add(token)
            sure = CONFIDENCE["verb_like" if verb_like and verb_like.search(token) else "word"]
            out.append(note(i, _label(word), None, "word", sure))
    taken = {n["t"] for n in out}
    good = [(len(t), i) for i, t in enumerate(plain) if t and t not in stop and not weak.search(t)
            and float(words[i][1]) not in taken and i != unit]
    out += [note(i, _label(words[i][0]), None, "emphasis", CONFIDENCE["emphasis"]) for n, i in good if n >= KEY_WORD_LETTERS]
    if not out and key_word:
        short = [x for x in good if x[0] >= SHORT_KEY_WORD]
        if short:
            i = max(short)[1]                                 # the longest; the later one among equals
            out.append(note(i, _label(words[i][0]), None, "emphasis", CONFIDENCE["key_word"]))
    return sorted(out, key=lambda n: n["t"])


def _extra_words(words: list[list], language: str, taken: set[float], shown: set[str]) -> list[dict]:
    """Other words of the sentence that may name something (4+ letters, not a stopword, not already a
    note): offered to the decider as well, so a verb it turns down can be replaced by a noun. The rules
    never keep them (they are not in the rules' answer)."""
    stop = STOPWORDS.get(language, STOPWORDS["en"])
    weak = _WEAK.get(language, _WEAK["en"])
    out = []
    for i, (word, start, _end) in enumerate(words):
        if _FIGURE.match(word) or (i and _FIGURE.match(words[i - 1][0])):    # a figure and its unit are a note already
            continue
        token = "".join(tokens(_clean(word).lower()))
        label = _label(word)
        if (len(token) < NOUN_LETTERS or token in stop or token in ALONE or weak.search(token)
                or float(start) in taken or label.lower() in shown):
            continue
        shown.add(label.lower())
        out.append({"label": label, "icon": find_icon(_clean(word).lower()), "t": float(start), "kind": "word",
                    "conf": EXTRA_CONFIDENCE, "word": _clean(word)})
    return out[:MAX_EXTRA]


def _notes(words: list[list], language: str, key_word: bool, decide: Decide | None) -> tuple[list[dict], str | None]:
    """(the notes of sentence_notes, the source of the answer when a model changed them, else None)."""
    out = _candidates(words, language, key_word)
    changed = None
    sure_labels = {n["label"].lower() for n in out if n["conf"] >= SURE}
    unsure = [n for n in out if n["conf"] < SURE and n["label"].lower() not in sure_labels]
    extra = _extra_words(words, language, {n["t"] for n in out}, {n["label"].lower() for n in out})
    if unsure or extra:
        options: dict[str, str] = {}
        for n in unsure + extra:
            options.setdefault(n["label"], n["word"])
        kept_by_rules = set(n["label"] for n in unsure)
        default = tuple(label for label in options if label not in kept_by_rules)   # the rules leave the extras out
        q = Question("label_words", "multi", PROMPT.get(language, PROMPT["en"]),
                     tuple(Option(label, word) for label, word in options.items()),
                     " ".join(str(w[0]) for w in words), default,
                     min([n["conf"] for n in unsure] + ([EXTRA_CONFIDENCE] if extra else [])), language)
        answer = ask(decide, q)
        verbs = set(answer.values)                               # the words that are verbs: never a note
        out = [n for n in out if n["conf"] >= SURE or (n["label"] not in verbs and n["label"].lower() not in sure_labels)]
        out += [n for n in extra if n["label"] not in verbs]     # the other words: notes too
        out.sort(key=lambda n: n["t"])
        if answer.source != "rules" and verbs != set(q.default):
            changed = answer.source
    return [{k: n[k] for k in ("label", "icon", "t", "kind")} for n in out], changed


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


def propose(plan: dict, language: str = "es", decide: Decide | None = None) -> list[dict]:
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
                        "at_sentence": group["first"], "until_sentence": group["last"], "_auto": "support",
                        **({"_decided": group["decided"]} if group.get("decided") else {})})
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
        notes, decided = _notes(s["words"], language, quiet and length >= 1.2, decide)
        offered = [note for note in notes
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
            if decided:
                group["decided"] = decided
        if group:
            group["last"] = n
            if decided:
                group["decided"] = decided         # its notes changed because of a model's answer
        clock += float(s["end"]) - float(s["start"])
        if cut is not None:                    # the picture changes inside this sentence: the notes end with it
            flush()
    flush()
    return out
