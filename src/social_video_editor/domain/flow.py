"""A sentence that describes a flow between things, read as the ordered stops of a diagram.

"Tu celular manda la foto a la antena" → Tu celular → Antena (carrying "Foto");
"El agua pasa de la nube al río y luego al mar" → Nube → Río → Mar;
"The letter goes from the office to the post office and then to your house" → Office → Post office → Your house.

Hand rules, Spanish and English, no grammar model: a verb of movement (pasa, va, sale, llega, viaja…;
goes, travels, leaves, reaches…) or of sending (manda, envía, transmite…; sends, transmits…) opens the
flow; every stop after it is a short noun phrase after "de / desde / a / al / hasta / por" ("from / to /
into / through"); a sender's subject is the first stop and what it sends is what the flow carries. A flow
needs two different stops; "va a llover", "de lunes a viernes" (no movement) or a single stop are not one.
`flow(words)` is pure; the director may use it to propose a `diagram` (not wired here).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .script import tokens

MOVE = set("""pasa pasan va van viaja viajan sale salen llega llegan sube suben baja bajan entra entran cruza cruzan
vuelve vuelven regresa regresan salta saltan circula circulan fluye fluyen rebota rebotan
go goes went travel travels travelled traveled leave leaves left arrive arrives arrived reach reaches reached
come comes came move moves moved flow flows flowed pass passes passed jump jumps jumped cross crosses crossed
return returns returned bounce bounces bounced""".split())
SEND = set("""manda mandan mando envia envian envio transmite transmiten entrega entregan reenvia reenvian
send sends sent transmit transmits transmitted deliver delivers delivered forward forwards forwarded
upload uploads uploaded""".split())
FROM = set("de del desde from".split())
TO = set("a al hasta hacia to into toward towards".split())
THROUGH = set("por through via".split())
OPENERS = FROM | TO | THROUGH
ARTICLES = set("el la los las lo un una unos unas the a an".split())
STOP = set("""y e o u ni pero que donde cuando luego despues entonces mientras porque como si
and or but that which where when then after while because as if""".split()) | MOVE | SEND
NOWHERE = set("ahi alli aqui alla alguna algun algo todo nada eso esto ello there here it that this everything "
              "nothing something somewhere".split())
IDIOMS = set("vuelta nuevo cero repente verdad hecho golpe pronto todo lejos cerca veras".split())   # "de vuelta", "de cero"
_CLITIC_VERB = re.compile(r"(?:ar|er|ir)(?:l[aeo]s?|se|me|te|nos)$")      # "abrirla", "traerlas": a verb, not a place
SUBJECT_SKIP = set("y e pero entonces asi luego despues and but so then after".split())
PRONOUNS = set("le les me te se nos yo tú él ella ellos ellas i you he she we they it".split())   # as written
NODE_WORDS = 4
MAX_NODES = 6
_ENDS = (",", ";", ":", ".", "?", "!", "…")


@dataclass(frozen=True)
class Flow:
    nodes: tuple[str, ...]          # the stops, in the order they are said
    carries: str | None             # what moves, when the sentence says it ("la foto", "the data")
    at: int                         # index of the word that opens the flow


def _clean(word: str) -> str:
    return str(word).strip("¿?¡!.,;:«»\"'()…")


def _capital(text: str) -> str:
    first = text.split()[0] if text.split() else ""
    return text if any(c.isupper() for c in first[1:]) else text[:1].upper() + text[1:]


def _phrase(said: list[str], plain: list[str], i: int) -> tuple[str | None, int]:
    """The noun phrase from word i (a stop's name: up to NODE_WORDS words, without its article) and the
    index after it. "de" + a noun stays inside ("centro de datos"); another stop's opener ends it."""
    k, picked = i, []
    while k < len(said) and len(picked) < NODE_WORDS:
        p = plain[k]
        if not p or p in STOP:
            break
        if p in OPENERS and picked:
            nxt = plain[k + 1] if k + 1 < len(plain) else ""
            if not (p in ("de", "of") and nxt and nxt not in ARTICLES and nxt not in OPENERS
                    and not said[k - 1].rstrip().endswith(_ENDS)):
                break
        picked.append(k)
        k += 1
        if said[k - 1].rstrip().endswith(_ENDS):
            break
    while picked and plain[picked[-1]] in OPENERS | ARTICLES:
        picked.pop()
    words = [k for k in picked if plain[k] not in ARTICLES or k != picked[0]]
    if picked and plain[picked[0]] in ARTICLES:
        words = picked[1:]
    if not words or all(plain[k] in NOWHERE or _clean(said[k]).lower() in PRONOUNS for k in words):
        return None, k
    first = plain[words[0]]
    if first in IDIOMS or _CLITIC_VERB.search(first):
        return None, k
    if i > 0 and plain[i - 1] == "a" and words[0] == i and first.endswith(("ar", "er", "ir")) and len(words) == 1:
        return None, k                                      # "va a llover": a verb, not a place
    return _capital(" ".join(_clean(said[j]) for j in words)), k


def _subject(said: list[str], plain: list[str], verb: int) -> str | None:
    """The sender: the noun phrase right before its verb, in the same clause."""
    begin = verb
    while begin > 0 and verb - begin < NODE_WORDS and not said[begin - 1].rstrip().endswith(_ENDS) \
            and plain[begin - 1] not in SUBJECT_SKIP:
        begin -= 1
    ks = [k for k in range(begin, verb) if _clean(said[k]).lower() not in PRONOUNS]
    while ks and plain[ks[0]] in ARTICLES:
        ks = ks[1:]
    while ks and plain[ks[-1]] in ARTICLES:                 # "la" / "lo" stuck to the verb: "la manda"
        ks = ks[:-1]
    if not ks or any(plain[k] in OPENERS | STOP for k in ks) or all(plain[k] in NOWHERE for k in ks):
        return None
    return _capital(" ".join(_clean(said[k]) for k in ks))


def flow(words) -> Flow | None:
    """The flow a sentence describes, or None. words: the sentence's words (strings or [word, start, …])."""
    said = [str(w[0] if isinstance(w, (list, tuple)) else w) for w in words]
    plain = ["".join(tokens(w)) for w in said]
    nodes: list[str] = []
    carries, at = None, None
    k = 0
    while k < len(said):
        p = plain[k]
        if p not in MOVE and p not in SEND:
            k += 1
            continue
        verb = k
        if at is None:
            at = verb
        if p in SEND:
            who = _subject(said, plain, verb)
            if who and who not in nodes:
                nodes.append(who)
            got, after = _phrase(said, plain, verb + 1)
            if got and (after >= len(plain) or plain[after] in TO | THROUGH) and carries is None:
                carries = got                               # "manda la foto a…": the photo travels
            k = after if got else verb + 1
        elif carries is None:
            who = _subject(said, plain, verb)
            carries = who if who and verb > 0 else None     # "el agua pasa…": the water travels
            k = verb + 1
        else:
            k = verb + 1
        while k < len(said):                                # the stops after the verb, until the next verb
            p = plain[k]
            if p in MOVE or p in SEND:
                break
            if p in OPENERS:
                got, after = _phrase(said, plain, k + 1)
                if got and got not in nodes:
                    nodes.append(got)
                k = max(after, k + 1)
                continue
            k += 1
    if carries in nodes:
        carries = None
    if not 2 <= len(nodes) <= MAX_NODES:
        return None
    return Flow(tuple(nodes), carries, at or 0)
