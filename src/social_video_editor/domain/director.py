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

import re
from collections import Counter

from .graphics.icons import ALONE, find_icon
from .graphics.scenes import ACTIONS, action_of
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
POSSESSIVES = {"tus", "sus", "mis", "your", "yours"}       # "tus apps" names the apps, not a person
DETERMINERS = set("el la los las un una unos unas tu tus su sus mi mis este esta estos estas the a an your my this these".split())
ICON_DEVICES = ("phone", "app", "laptop", "chip", "ram", "disk", "router", "server", "cloud", "globe", "tower", "lock", "battery")
OWNERS = {"swipe": ("app",), "open": ("app",), "sleep": ("app", "phone", "laptop"), "wait": ("app", "phone", "laptop", "user"),
          "store": ("app", "phone", "laptop"), "work": ("chip", "phone", "laptop", "server", "ram"),
          "faster": ("phone", "laptop", "app"), "slower": ("phone", "laptop", "app")}
CONNECTORS = set("pero si cuando porque entonces aunque como mientras but if when because then so although".split())
LOCAL_STOP = set("cada vez algo nada todo todos todas otra otro misma mismo cosa cosas forma manera thing things way".split())
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
    return None if len(t) < 3 or t in ALONE or t in POSSESSIVES else find_icon(word)


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
    if end - begin + 1 < FEWEST:                             # too short to read as a title: the next clause too
        while end < len(words) - 1 and end - begin + 1 < most:
            end += 1
            if closes[end] and end - begin + 1 >= FEWEST:
                break
    text = " ".join(w for w, _ in words[begin:end + 1]).strip(" ,;:.…")
    if end < len(words) - 1 and not closes[end]:
        text += "…"
    lead = len(text) - len(text.lstrip("¿¡\"'«("))
    return text[:lead] + text[lead:lead + 1].upper() + text[lead + 1:]


NUMBER_WORDS = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "cien": 100,
    "mil": 1000, "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "twenty": 20, "thirty": 30, "fifty": 50, "hundred": 100, "thousand": 1000,
}                           # "un" / "one" and "cero" ("de cero") are left out: they are rarely figures
UNIT_STOP = set("de del la el los las por y que al a en con the of a an and per".split())


def _clean(word: str) -> str:
    return str(word).strip("¿?¡!.,;:«»\"'()…")


def figure(words: list[tuple[str, float]]) -> dict | None:
    """A figure the sentence says ("20", "dos minutos") as a `number` graphic: the value, its unit (the
    word after it), what comes before it in its clause as the label and after it as the line under it.
    A figure with a unit is preferred ("dos minutos" over "veinte al día")."""
    plain = ["".join(tokens(w)) for w, _ in words]
    found = []
    for i, (w, _) in enumerate(words):
        raw = _clean(w)
        digits = raw.replace(".", "").replace(",", "").replace("%", "")
        value = raw if digits.isdigit() else NUMBER_WORDS.get(plain[i])
        if value is None:
            continue
        unit, after = "", i + 1
        if after < len(words) and plain[after] and plain[after] not in UNIT_STOP and not plain[after].isdigit() \
                and not words[i][0].rstrip().endswith((",", ";", ":", ".")):
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
QUOTE_LEAD = ("según", "segun", "according to")
QUOTE_VERBS = set("publicó publico dice dijo reveló revelo informó informo asegura aseguró afirma afirmó anunció anuncio "
                  "says said reported reports announced published claims".split())


def _unit_key(word: str) -> str:
    """'minutos' → 'minuto', 'GB' → 'gb', '%' → 'porciento': the same unit however it is said."""
    w = "".join(tokens(word)) or word.strip()
    if w in ("%", "porciento", "percent") or word.strip() == "%":
        return "porciento"
    for key in (w, w[:-2] if w.endswith("es") else w, w[:-1] if w.endswith("s") else w):
        if key in TIME_UNITS or key in SHORT_UNITS:
            return key
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def _figures(words: list[tuple[str, float]]) -> list[dict]:
    """Every figure said with its unit: [{"i", "end" (index after the unit), "value", "unit", "said"}]."""
    plain = ["".join(tokens(w)) for w, _ in words]
    out = []
    for i, (w, _) in enumerate(words):
        raw = _clean(w)
        pct = raw.endswith("%")
        digits = raw.rstrip("%").replace(".", "").replace(",", ".")
        try:
            value = float(digits) if digits.replace(".", "", 1).isdigit() else NUMBER_WORDS.get(plain[i])
        except ValueError:
            value = None
        if value is None:
            continue
        unit, said, end = ("porciento", "%", i + 1) if pct else ("", "", i + 1)
        if not pct and end < len(words) and _clean(words[end][0]) in ("%", "por ciento"):
            unit, said, end = "porciento", "%", end + 1
        elif not pct and end < len(words) and plain[end] and plain[end] not in UNIT_STOP and not plain[end].isdigit() \
                and not words[i][0].rstrip().endswith((",", ";", ":", ".")):
            unit, said, end = _unit_key(words[end][0]), _clean(words[end][0]), end + 1
            if unit == "por" or said.lower() in ("por", "per"):
                unit, said, end = "", "", i + 1
        if unit:
            out.append({"i": i, "end": end, "value": float(value), "unit": unit, "said": said})
    return out


def comparison(words: list[tuple[str, float]]) -> dict | None:
    """Two to six figures of the same kind compared in one sentence ("dos minutos por semana, al año dos
    horas"; "de 15 % antes a 40 % hoy") as `bars`: times are put in the smallest unit said; each bar is
    named by a word such as "antes / hoy" next to it, else by the words after it (or before it).
    → the spec plus "at" (the second the first figure is said), or None."""
    found = _figures(words)
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
    if not (best[0]["base"] or apart or other or any(w in MARKERS | COMPARING for w in plain)):
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
        if marked:
            pick = marked[:1]
        elif own:
            pick = before                                   # ", al año dos horas" → "Al año"
        elif after and len(after) <= LABEL_WORDS and closes[after[-1]]:
            pick = after                                    # "dos minutos por semana," → "Por semana"
        elif before:
            pick = before[-LABEL_WORDS - 1:]
        else:
            pick = after[:LABEL_WORDS]
        while pick and (plain[pick[0]] in LEADING or plain[pick[0]] in DETERMINERS):
            pick = pick[1:]
        while pick and plain[pick[-1]] in LABEL_STOP:
            pick = pick[:-1]
        pick = pick[-LABEL_WORDS:]
        used.update(pick)
        label = " ".join(_clean(words[k][0]) for k in pick).strip()
        labels.append(_capital(label) if label else f"{n + 1}")
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


def said_data(words: list[tuple[str, float]]) -> dict | None:
    """What a sentence says that is shown as data rather than searched for: figures compared → `bars`,
    a source quoted → `headline`. The spec plus "at" (when it starts being said), or None."""
    return comparison(words) or quote(words)


def quote(words: list[tuple[str, float]]) -> dict | None:
    """"Según X, …" / "X publicó que …" / "according to X, …" as a `headline`: what was said, and who said
    it as the outlet. → the spec plus "at" (the second the source is named), or None."""
    plain = ["".join(tokens(w)) for w, _ in words]
    text = [w for w, _ in words]
    for k, w in enumerate(plain):
        lead = 2 if w == "according" and k + 1 < len(plain) and plain[k + 1] == "to" else 1 if w in ("segun",) else 0
        if lead:
            stop = k + lead
            while stop < len(words) and stop - k - lead < 4 and not text[stop].rstrip().endswith(CLAUSE_END):
                stop += 1
            if stop >= len(words) or stop - k - lead >= 4:
                continue
            outlet = " ".join(_clean(x) for x in text[k + lead:stop + 1])
            said = " ".join(text[stop + 1:]).strip(" .")
            before = " ".join(text[:k]).strip(" ,")
            said = said or before
            if any(x[:1].isupper() for x in outlet.split()) and len(said.split()) >= 3 and "?" not in said:
                return {"template": "headline", "text": said[:1].upper() + said[1:], "outlet": outlet,
                        "at": words[k][1]}
        if w in QUOTE_VERBS and 0 < k <= 4 and k + 1 < len(plain) and plain[k + 1] in ("que", "that"):
            outlet = " ".join(_clean(x) for x in text[:k])
            said = " ".join(text[k + 2:]).strip(" .")
            if outlet[:1].isupper() and len(said.split()) >= 3:
                return {"template": "headline", "text": said[:1].upper() + said[1:], "outlet": outlet,
                        "at": words[0][1]}
    return None


def enumeration(text: str) -> list[str] | None:
    """3 to 6 short items said in a row ("A, B y C") or None."""
    body = str(text).strip(" .?!¿¡…")
    parts = [x.strip() for chunk in re.split(r",|;", body) for x in re.split(r"\s+(?:y|e|o|and|or)\s+", chunk) if x.strip()]
    if not 3 <= len(parts) <= 6 or any(len(x.split()) > 5 for x in parts):
        return None
    if "".join(tokens(parts[0].split()[0])) in CONNECTORS:    # clauses of one thought ("pero si…, se van…"), not a list
        return None
    return [x[:1].upper() + x[1:] for x in parts]


def scene_for(words: list[tuple[str, float]], at: float, main: str) -> dict | None:
    """An illustrated scene (graphics/scenes.py) when the sentence says what something does: the verb
    said at or after the cut first, else the one before it."""
    cut = next((k for k, (_, t) in enumerate(words) if t >= at - 1e-6), 0)
    found = action_of([w for w, _ in words[cut:]])
    if found:
        found = (found[0], found[1] + cut)
    else:
        found = action_of([w for w, _ in words])
    if not found:
        return None
    action, where = found
    subject, target, _ = ACTIONS[action]
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
    spec = {"template": "scene", "action": action, "text": phrase(words, at)}
    if subject:
        spec["subject"] = subject
    if target and action in ("send", "store", "swipe", "open"):
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


def card(sentence: dict, at: float | None = None, main: str = TOPIC_DEFAULT, last: str | None = None) -> dict:
    """What the engine draws for a sentence when no footage is good enough, by what the sentence says:
    a figure → `number`; three or more items → `list`; something doing something → `scene` (an animated
    illustration); else a `title` with the illustration of what it names. `last`: the template used
    just before — another one is chosen when there is one, so the same kind does not come twice."""
    words = [(str(w[0]), float(w[1])) for w in sentence.get("words") or []]
    at = float(sentence.get("start") or 0) if at is None else at
    named = [i for w, t in words if (i := _icon_of(w))]
    after = [i for w, t in words if t >= at - 1e-6 and (i := _icon_of(w))]
    title = {"template": "title", "text": phrase(words, at) or str(sentence.get("text") or ""),
             "icon": (after or named or [main])[0]}
    items = enumeration(sentence.get("text") or "")
    data = said_data(words)
    data = {k: v for k, v in data.items() if k != "at"} if data else None
    options = [x for x in (data, figure(words), {"template": "list", "items": "; ".join(items), "marks": "n"} if items else None,
                           scene_for(words, at, main), title) if x]
    fresh = [x for x in options if x["template"] != last]
    return (fresh or options)[0]


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
    last: str | None = None
    out: list[dict] = []
    clock, opening = 0.0, True
    for k, s in enumerate(sentences):
        n, start, end = int(s["n"]), float(s["start"]), float(s["end"])
        if n in under:
            clock, opening = 0.0, False
            continue
        allowed = limit_open if opening else limit
        words = [(str(w[0]), float(w[1])) for w in s["words"]]
        data = said_data(words) if k < len(sentences) - 1 else None
        if data and data["template"] != last:              # data said aloud is shown as it is said
            at = max(float(data.pop("at")), start + max(MIN_FACE - clock, LEAD_IN))
            due = start + max(allowed - clock, MIN_FACE - clock, LEAD_IN)
            if at > due + 1e-6:                             # waiting for the figure would keep the face too long
                at = next((t for w, t in words if due - 1e-6 <= t <= at), due)
            if end - at < shortest:
                at = start + LEAD_IN
            if end - at >= shortest:
                said = next((w for w, t in words if t >= at - 1e-6), words[0][0])
                out.append({"type": "graphic", **data, "at_sentence": n, "until_sentence": n,
                            "from_word": [s["source"], round(at, 3)], "note": f"«{said.strip('¿?¡!.,;:')}»",
                            "_auto": "director"})
                last = data["template"]
                clock, opening = 0.0, False
                continue
        if k == len(sentences) - 1 or clock + (end - start) <= allowed:
            clock += end - start
            continue
        earliest = start + max(allowed - clock, 0.0)
        if clock >= allowed and end - start >= shortest:            # already over: cover it from its start
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
        drawn = card(s, at, main, last)
        where = {"at_sentence": n, "until_sentence": n, "from_word": [s["source"], round(at, 3)],
                 "note": f"«{word.strip('¿?¡!.,;:')}»", "_auto": "director"}
        if drawn["template"] == "number":                   # a figure is shown, not searched for
            out.append({"type": "graphic", **drawn, **where})
        else:
            out.append({"type": "broll", "query": query, "query_lang": "en", "query_local": local_query(words, at, icon, str(cfg.get("language", "es"))),
                        "said": str(s.get("text") or ""), **where, "fallback": drawn})
        last = drawn["template"]
        clock, opening = 0.0, False
    return out
