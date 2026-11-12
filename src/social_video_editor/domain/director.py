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
"""
from __future__ import annotations

from collections import Counter

from .graphics.icons import ALONE, find_icon
from .script import tokens

DEFAULTS = {"auto_broll": False, "max_camera": 4.0, "opening": 2.5, "auto_min": 1.8}
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
PHRASE_WORDS = 6
FEWEST = 3
CLAUSE_END = (",", ";", ":")
LEADING = set("y e pero que o u entonces asi and but so or that then".split())
COVERING = ("graphic", "photo", "broll")


def settings(cfg: dict) -> dict:
    s = (cfg or {}).get("script") or {}
    return {k: s.get(k, v) if s.get(k) is not None else v for k, v in DEFAULTS.items()}


def _icon_of(word: str) -> str | None:
    t = "".join(tokens(word))
    return None if len(t) < 3 or t in ALONE else find_icon(word)


def topic(sentences: list[dict]) -> str:
    """The thing the script names most (an illustration name), for searches with nothing better."""
    seen = Counter(i for s in sentences for w in s.get("words") or [] if (i := _icon_of(str(w[0]))))
    return seen.most_common(1)[0][0] if seen else TOPIC_DEFAULT


def phrase(words: list[tuple[str, float]], at: float, most: int = PHRASE_WORDS) -> str:
    """What is said around the cut, as a short title (the illustrated card used when no footage is found):
    the clause the cut falls in (its last `most` words when it is longer), at least FEWEST words, without
    a leading "and / but / then"; capitalized (after an opening ¿ or ¡), no final stop."""
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
    while end < len(words) - 1 and end - begin + 1 < FEWEST:  # too short to read as a title: the next clause too
        end += 1
    text = " ".join(w for w, _ in words[begin:end + 1]).strip(" ,;:.…")
    if end < len(words) - 1 and not closes[end]:
        text += "…"
    lead = len(text) - len(text.lstrip("¿¡\"'«("))
    return text[:lead] + text[lead:lead + 1].upper() + text[lead + 1:]


def card(sentence: dict, at: float | None = None, main: str = TOPIC_DEFAULT) -> dict:
    """The illustrated title card for a sentence (from second `at` of it): what is said, with the
    illustration of the thing it names (or the script's topic)."""
    words = [(str(w[0]), float(w[1])) for w in sentence.get("words") or []]
    at = float(sentence.get("start") or 0) if at is None else at
    named = [i for w, t in words if (i := _icon_of(w))]
    after = [i for w, t in words if t >= at - 1e-6 and (i := _icon_of(w))]
    return {"template": "title", "text": phrase(words, at) or str(sentence.get("text") or ""),
            "icon": (after or named or [main])[0]}


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


def propose(plan: dict, cfg: dict, overlay: set[str] = frozenset()) -> list[dict]:
    """b-roll cutaways for the stretches the presenter is alone on camera for too long. The plan is
    not changed. overlay: the templates drawn over the picture (they do not cover the presenter)."""
    p = settings(cfg)
    limit_open, limit, shortest = float(p["opening"]), float(p["max_camera"]), float(p["auto_min"])
    sentences = [s for s in plan.get("sentences") or []
                 if isinstance(s, dict) and not s.get("missing") and s.get("start") is not None and s.get("words")]
    if not sentences:
        return []
    under = covered(plan, overlay)
    main = topic(sentences)
    turns: Counter = Counter()
    last_query = None
    out: list[dict] = []
    clock, opening = 0.0, True
    for k, s in enumerate(sentences):
        n, start, end = int(s["n"]), float(s["start"]), float(s["end"])
        if n in under:
            clock, opening = 0.0, False
            continue
        allowed = limit_open if opening else limit
        if k == len(sentences) - 1 or clock + (end - start) <= allowed:
            clock += end - start
            continue
        earliest = start + max(allowed - clock, 0.0)
        words = [(str(w[0]), float(w[1])) for w in s["words"]]
        if clock >= allowed and end - start >= shortest:            # already over: cover it from its start
            room = [(words[0][0], start)]
        else:                                                       # a word after the face had its moment
            first = start + max(MIN_FACE - clock, LEAD_IN)
            room = [(w, t) for w, t in words if t >= first - 1e-6 and end - t >= shortest]
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
        out.append({"type": "broll", "query": query, "at_sentence": n, "until_sentence": n,
                    "from_word": [s["source"], round(at, 3)], "note": f"«{word.strip('¿?¡!.,;:')}»",
                    "fallback": {"template": "title", "text": phrase(words, at), "icon": icon}, "_auto": "director"})
        clock, opening = 0.0, False
    return out
