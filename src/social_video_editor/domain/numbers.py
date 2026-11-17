"""Numbers said in words or written in digits: "ciento sesenta y uno" = "161", "fifty-four" = "54",
"17 mil" = "17.000" = "17,000", "2,5" = "2.5" (Spanish and English, no grammar model).

`spelled(words, i)` reads the number said from word i on (the director's figures use it); `fold(words)`
turns a list of written words into comparable tokens where every number, however it is written, is ONE
token of its value ("#161"), so a script that spells its figures out matches a transcription that writes
digits, and the other way round (script_align.py). A lone "un / una / uno / one" or "cero / zero" stays a
word (it is mostly an article); `same_number` lets it stand for "#1" / "#0" when the other side has the digit.
"""
from __future__ import annotations

import re

from .script import tokens

# every number word, for figures said in words ("ciento sesenta y uno", "fifty-four", "17 mil", "two million")
_UNITS_ES = "uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete " \
            "dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco veintiseis veintisiete " \
            "veintiocho veintinueve".split()
_UNITS_EN = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
            "seventeen eighteen nineteen".split()
SPELLED = {**{w: n for n, w in enumerate(_UNITS_ES, 1)}, **{w: n for n, w in enumerate(_UNITS_EN, 1)},
           "un": 1, "una": 1, "veintiun": 21, "veintiuna": 21, "cero": 0, "zero": 0,
           **{w: 10 * n for n, w in enumerate("treinta cuarenta cincuenta sesenta setenta ochenta noventa".split(), 3)},
           **{w: 10 * n for n, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)},
           "cien": 100, "ciento": 100, "quinientos": 500, "quinientas": 500, "setecientos": 700, "setecientas": 700,
           "novecientos": 900, "novecientas": 900,
           **{f"{w}cientos": 100 * n for n, w in ((2, "dos"), (3, "tres"), (4, "cuatro"), (6, "seis"), (8, "ocho"))},
           **{f"{w}cientas": 100 * n for n, w in ((2, "dos"), (3, "tres"), (4, "cuatro"), (6, "seis"), (8, "ocho"))}}
SCALES = {"mil": 1000, "thousand": 1000, "millon": 10 ** 6, "millones": 10 ** 6, "million": 10 ** 6,
          "millions": 10 ** 6, "billion": 10 ** 9}
ALONE_NUMBERS = {"un", "una", "uno", "one", "cero", "zero"}     # a figure only with a unit ("un segundo"), not alone
_EDGE = (",", ";", ":", ".", "?", "!")


def _digits(raw: str) -> float | None:
    """A number written with digits ("17", "1.000", "2,5", "40 %"), else None."""
    body = raw.rstrip("%").strip()
    if not body or not body.replace(".", "").replace(",", "").isdigit():
        return None
    if "," in body and "." not in body and len(body.split(",")[-1]) != 3:
        body = body.replace(",", ".")                       # "2,5" is two and a half; "17,000" is seventeen thousand
    elif "." in body and "," not in body and len(body.split(".")[-1]) != 3:
        pass                                                # "1.5"
    else:
        body = body.replace(".", "").replace(",", "")
    try:
        return float(body)
    except ValueError:
        return None


def spelled(words: list[str], i: int) -> tuple[float, int] | None:
    """The number said from word i on, in digits or in words, and the index after it:
    "ciento sesenta y uno" → 161, "fifty-four" → 54, "17 mil" → 17000, "two million" → 2000000.
    A word that ends a clause ("10,") ends the number."""
    total, group, last, end, said = 0.0, 0.0, None, i, False
    k = i
    while k < len(words):
        raw = _clean(words[k])
        parts = tokens(raw)
        joint = len(parts) == 1 and parts[0] in ("y", "and")
        if joint:                                           # "sesenta y uno", "one hundred and five"
            nxt = tokens(_clean(words[k + 1])) if k + 1 < len(words) else []
            if not said or words[k - 1].rstrip().endswith(_EDGE) or not nxt or nxt[0] not in SPELLED \
                    or last is None or SPELLED[nxt[0]] >= last or (last < 20 and last != 100):
                break
            k += 1
            continue
        if k == i and _digits(raw) is not None and "%" not in raw[:-1]:
            group, last, said = _digits(raw) or 0.0, None, True
            end = k + 1
        elif parts and all(p in SPELLED or p in SCALES or p == "hundred" for p in parts):
            ok = True
            for p in parts:
                if p in SCALES:
                    if not said and not group and SCALES[p] > 1000:      # "millones" alone is not a figure
                        ok = False
                        break
                    total += (group or 1) * SCALES[p]
                    group, last = 0.0, None
                elif p == "hundred":
                    if group >= 100 or (said and not group):
                        ok = False
                        break
                    group, last = (group or 1) * 100, 100
                else:
                    n = SPELLED[p]
                    if last is not None and n >= last or (said and last is None and group):
                        ok = False
                        break
                    group, last = group + n, n
                said = True
            if not ok:
                break
            end = k + 1
        else:
            break
        if words[k].rstrip().endswith(_EDGE):
            break
        k += 1
    if not said or end == i:
        return None
    return total + group, end


def _clean(word: str) -> str:
    return str(word).strip("¿?¡!.,;:«»\"'()…")


NUMBER_MARK = "#"
_DIGIT_WORD = re.compile(r"^([+-]?\d[\d.,]*)(%|[^\W\d_]+)?$")
LONE = {"un": 1, "una": 1, "uno": 1, "one": 1, "cero": 0, "zero": 0}     # words that stay words when alone


def number_token(value: float) -> str:
    return NUMBER_MARK + (str(int(value)) if float(value).is_integer() else f"{value:g}")


def fold(words: list[str]) -> list[tuple[str, int, int]]:
    """Comparable tokens of written words (script.tokens), with every number as one token of its value:
    [(token, first word, last word)] — "va a ciento sesenta y un megas." → va, a, #161 (words 2–5), megas;
    "a 161Mbps" → a, #161, mbps; "54%" → #54. A range ("de 7 a 9") stays two numbers; a lone
    "un / uno / una / one / cero / zero" stays a word (see same_number)."""
    out: list[tuple[str, int, int]] = []
    i = 0
    while i < len(words):
        raw = _clean(words[i])
        glued = _DIGIT_WORD.match(raw)
        if glued and glued.group(2):                        # "161Mbps", "54%", "5G": the number, then its unit
            value = _digits(glued.group(1))
            if value is not None:
                out.append((number_token(value), i, i))
                out += [(t, i, i) for t in tokens(glued.group(2))]
                i += 1
                continue
        got = spelled(words, i)
        if got is not None:
            value, end = got
            plain = tokens(raw)
            if not (end == i + 1 and len(plain) == 1 and plain[0] in LONE):
                out.append((number_token(value), i, end - 1))
                i = end
                continue
        out += [(t, i, i) for t in tokens(words[i])]
        i += 1
    return out


def same_number(a: str, b: str) -> bool:
    """Two tokens of fold() that say the same number: equal, or a lone "un / one" against "#1" ("cero /
    zero" against "#0")."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if x.startswith(NUMBER_MARK) and y in LONE and x == number_token(LONE[y]):
            return True
    return False
