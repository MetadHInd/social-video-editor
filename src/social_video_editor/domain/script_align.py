"""Finding each sentence of a script in the recording (word-level transcription of every source).

The presenter says the script in front of the camera, repeating whatever goes wrong. For every
sentence all the places where it was said are found (its "takes"), one is chosen (by default the
last good one: people repeat a sentence until it comes out right) and its source times are
returned. Nothing is invented: a sentence that was not said is reported as missing.

Matching is done on words (script.tokens), tolerant to the transcription's mistakes: two words are
the same when they are identical or look alike (ratio ≥ 0.8, words of 4+ letters).
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from math import ceil

from .script import tokens

DEFAULTS = {
    "min_match": 0.6,     # share of the sentence's words that must be found for a take to count
    "take": "last",       # last | best: which take is used when a sentence was said several times
    "pad_start": 0.08,    # seconds kept before the first word
    "pad_end": 0.12,      # seconds kept after the last word
    "join_gap": 0.6,      # consecutive sentences this close in the recording stay in one shot
    "subtitles": "script",  # script | speech: subtitles show the script's words, or what the transcription heard
}
NEAR_BEST = 0.1           # take: last → the last take whose score is this close to the best one
SIMILAR = 0.8             # two words of FUZZY_MIN_LETTERS+ letters this alike are the same word
FUZZY_MIN_LETTERS = 4
WINDOW = 1.3              # a take may be this much longer than the sentence (filler words, restarts)
EDGE_GAP = 0.3            # a word swapped at the edge of a take is kept when it is this close (seconds)
SHORT_SENTENCE = 2        # sentences of up to this many words are placed next to their neighbors
SURE_WORDS = 3            # sentences of up to this many words must be found whole
LOOSE_GAP = 2             # a single matched word this many words away from the rest is not part of the take
SENTENCE_END = (".", "?", "!", "…")
MAX_ALTERNATIVES = 4
MIN_REFRAMED = 2.0        # a shot made by reframing lasts at least this long (else it stays in the previous one)
UNSAID = 0.06             # a written word left with less time than this (seconds) was not said


@dataclass(frozen=True)
class Word:
    token: str
    start: float
    end: float
    source: int
    raw: str = ""


@dataclass(frozen=True)
class Take:
    first: int            # index of its first word in the transcript
    last: int             # index of its last word
    matched: int          # words of the sentence found
    score: float          # matched / words of the sentence
    pairs: tuple = ()     # (index in the transcript, index of the sentence's word) of every word found


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **{k: v for k, v in (cfg.get("script") or {}).items() if v is not None}}


def transcript(analyses: list[dict]) -> list[Word]:
    """Every transcribed word of every source, in order, as comparable tokens."""
    out = []
    for source, an in enumerate(analyses):
        for w in (an or {}).get("words") or []:
            raw = str(w.get("word", "")).strip()
            for tok in tokens(raw):
                out.append(Word(tok, float(w["start"]), float(w["end"]), source, raw))
    return out


def same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < FUZZY_MIN_LETTERS:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= SIMILAR


def find_takes(sentence: list[str], words: list[Word], min_match: float = 0.6) -> list[Take]:
    """Every place where the sentence was said (takes that do not overlap), best first."""
    n = len(sentence)
    if not n or not words:
        return []
    wanted = set(sentence)
    known: dict[str, str | None] = {}

    def canonical(tok: str) -> str | None:
        """The sentence's word this transcribed word stands for (None: not a word of the sentence)."""
        if tok not in known:
            known[tok] = tok if tok in wanted else next((s for s in wanted if same_word(tok, s)), None)
        return known[tok]

    seen = [canonical(w.token) for w in words]
    hits = [0]
    for s in seen:
        hits.append(hits[-1] + (s is not None))
    need = max(ceil(min_match * n - 1e-9), min(n, SURE_WORDS))     # a short sentence needs all its words
    length = max(n + 2, ceil(n * WINDOW))
    found: dict[tuple[int, int], Take] = {}
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(sentence)
    for i, s in enumerate(seen):
        if s is None:
            continue
        j = min(i + length, len(words))
        while j > i and words[j - 1].source != words[i].source:      # a take never spans two sources
            j -= 1
        if hits[j] - hits[i] < need:
            continue
        matcher.set_seq1(seen[i:j])
        blocks = _tight([b for b in matcher.get_matching_blocks() if b.size], need)
        matched = sum(b.size for b in blocks)
        if matched < need:
            continue
        first, last = i + blocks[0].a, i + blocks[-1].a + blocks[-1].size - 1
        first, last = _edges(words, first, last, blocks[0].b, n - (blocks[-1].b + blocks[-1].size))
        pairs = tuple((i + b.a + k, b.b + k) for b in blocks for k in range(b.size))
        take = Take(first, last, matched, round(matched / n, 3), pairs)
        old = found.get((first, last))
        if old is None or take.matched > old.matched:
            found[(first, last)] = take
    out: list[Take] = []
    for take in sorted(found.values(), key=lambda t: (-t.matched, t.last - t.first, -t.first)):
        if not any(take.first <= o.last and o.first <= take.last for o in out):
            out.append(take)
    return out


def _tight(blocks: list, need: int) -> list:
    """Drops a lone word matched away from the rest at either end (a common word such as "the" a few
    words before or after the sentence), while enough words stay matched."""
    blocks = list(blocks)
    while len(blocks) > 1 and sum(b.size for b in blocks) - 1 >= need:
        head, tail = blocks[0], blocks[-1]
        if tail.size == 1 and tail.a - (blocks[-2].a + blocks[-2].size) >= LOOSE_GAP:
            blocks.pop()
        elif head.size == 1 and blocks[1].a - (head.a + 1) >= LOOSE_GAP:
            blocks.pop(0)
        else:
            break
    return blocks


def _edges(words: list[Word], first: int, last: int, before: int, after: int) -> tuple[int, int]:
    """Widens a take over the words said in place of the sentence's first / last words (swapped by
    the speaker or misheard by the transcription): at most as many as are missing, only while they
    follow closely and without crossing the end of another sentence."""
    for _ in range(before):
        p = first - 1
        if (p < 0 or words[p].source != words[first].source or words[first].start - words[p].end > EDGE_GAP
                or words[p].raw.endswith(SENTENCE_END)):
            break
        first = p
    for _ in range(after):
        p = last + 1
        if (p >= len(words) or words[p].source != words[last].source or words[p].start - words[last].end > EDGE_GAP
                or words[last].raw.endswith(SENTENCE_END)):
            break
        last = p
    return first, last


def _preference(takes: list[Take], mode: str) -> list[Take]:
    """Takes in the order they are tried. last: the latest of the good ones (score close to the
    best), then the rest by score; best: by score (the latest among equals)."""
    if not takes:
        return []
    by_score = sorted(takes, key=lambda t: (-t.score, -t.first))
    if mode == "best":
        return by_score
    top = by_score[0].score
    good = sorted((t for t in takes if t.score >= top - NEAR_BEST), key=lambda t: -t.first)
    return good + [t for t in by_score if t.score < top - NEAR_BEST]


def _overlap(a: Take, b: Take) -> bool:
    return a.first <= b.last and b.first <= a.last


def choose(takes: list[list[Take]], lengths: list[int], mode: str = "last") -> list[Take | None]:
    """One take per sentence. Two sentences never share words of the recording: the one that
    matches more words keeps them and the other one goes to its next take (so "And they travel apart"
    is not taken out of the later "And they travel apart again"). Very short sentences ("Exactly.")
    go next to their neighbors."""
    order = [_preference(t, mode) for t in takes]
    at = [0] * len(takes)

    def current(k: int) -> Take | None:
        return order[k][at[k]] if at[k] < len(order[k]) else None

    while True:
        clash = None
        for a in range(len(takes)):
            ta = current(a)
            if ta is None:
                continue
            for b in range(a + 1, len(takes)):
                tb = current(b)
                if tb is not None and _overlap(ta, tb):
                    clash = (a, b)
                    break
            if clash:
                break
        if clash is None:
            break
        a, b = clash
        ta, tb = current(a), current(b)
        # the weaker one moves on: fewer words matched, then lower score, then the earlier sentence
        # (the same words said twice belong, in order, to the two sentences that say them)
        loser = a if (ta.matched, ta.score, a) < (tb.matched, tb.score, b) else b
        at[loser] += 1
    chosen = [current(k) for k in range(len(takes))]
    for k, n in enumerate(lengths):
        if n > SHORT_SENTENCE or len(takes[k]) < 2:
            continue
        near = [chosen[j] for j in (k - 1, k + 1) if 0 <= j < len(chosen) and chosen[j] is not None]
        free = [t for t in takes[k] if not any(_overlap(t, o) for j, o in enumerate(chosen) if o is not None and j != k)]
        if near and free:
            chosen[k] = min(free, key=lambda t: min(abs(t.first - o.last) + abs(t.last - o.first) for o in near))
    return chosen


def _window(words: list[Word], take: Take, pad_start: float, pad_end: float) -> tuple[float, float]:
    """Source seconds of a take: a little air before and after, never into the neighboring words."""
    first, last = words[take.first], words[take.last]
    start, end = first.start - pad_start, last.end + pad_end
    if take.first > 0 and words[take.first - 1].source == first.source:
        start = max(start, min(words[take.first - 1].end, first.start))
    if take.last + 1 < len(words) and words[take.last + 1].source == last.source:
        end = min(end, max(words[take.last + 1].start, last.end))
    return round(max(start, 0.0), 3), round(end, 3)


def align(sentences: list[str], words: list[Word], cfg: dict | None = None) -> list[dict | None]:
    """Where each sentence is in the recording: {"source", "start", "end", "score", "takes",
    "alternatives": [[source, start, end, score], …]} (source seconds), or None when it was not said."""
    p = settings(cfg or {})
    split = [tokens(s) for s in sentences]
    takes = [find_takes(t, words, float(p["min_match"])) for t in split]
    chosen = choose(takes, [len(t) for t in split], str(p["take"]))
    pads = float(p["pad_start"]), float(p["pad_end"])
    out: list[dict | None] = []
    for sentence, found, take in zip(sentences, takes, chosen):
        if take is None:
            out.append(None)
            continue
        start, end = _window(words, take, *pads)
        others = sorted((t for t in found if t is not take), key=lambda t: t.first)[:MAX_ALTERNATIVES]
        out.append({"source": words[take.first].source, "start": start, "end": end, "score": take.score,
                    "takes": len(found),
                    "alternatives": [[words[t.first].source, *_window(words, t, *pads), t.score] for t in others],
                    "words": script_words(sentence, take, words)})
    return out


def script_words(sentence: str, take: Take, words: list[Word]) -> list[list]:
    """The sentence as written, word by word, with the times it was said in a take: [[word, start, end]].
    Subtitles made from these say what the script says ("zero") and not what the transcription heard
    ("hero"). A written word that was heard takes its time; the ones in between share the time between
    their neighbors; a word with no time left for it (it was not said) is dropped."""
    shown = sentence.split()
    owner: list[int] = []                                   # sentence token → the written word it belongs to
    for k, w in enumerate(shown):
        owner += [k] * len(tokens(w))
    heard: dict[int, list[float]] = {}
    for at, token in take.pairs:
        k, w = owner[token], words[at]
        heard[k] = [min(heard[k][0], w.start), max(heard[k][1], w.end)] if k in heard else [w.start, w.end]
    first, last = words[take.first].start, words[take.last].end
    out: list[list] = []
    k = 0
    while k < len(shown):
        if k in heard:
            out.append([shown[k], *heard[k]])
            k += 1
            continue
        j = k
        while j < len(shown) and j not in heard:
            j += 1
        a = out[-1][2] if out else first
        b = max(heard[j][0] if j < len(shown) else last, a)
        step = (b - a) / (j - k)
        if step >= UNSAID:
            out += [[shown[m], a + step * (m - k), a + step * (m - k + 1)] for m in range(k, j)]
        k = j
    return [[w, round(a, 3), round(b, 3)] for w, a, b in out]


def join_shots(matches: list[dict | None], join_gap: float = 0.6, reframe_every: float = 0.0) -> list[dict]:
    """The sentences as shots, in script order: [{"source", "start", "end", "sentences": [indices]}].
    Consecutive sentences that also follow each other in the recording (less than join_gap apart)
    stay in one shot, so nothing is cut where the presenter simply kept talking.

    reframe_every > 0: such a run is still split, between two sentences, once a shot has lasted that
    many seconds. The new shot starts exactly where the previous one ends ("continues": true: not a
    frame or a sample is lost), so the only thing that changes there is the framing — the planner
    alternates a punch-in at every cut, which gives a single camera the rhythm of several."""
    shots: list[dict] = []
    previous = None
    for k, m in enumerate(matches):
        if m is None:
            continue
        last = shots[-1] if shots else None
        if (last is not None and previous == k - 1 and m["source"] == last["source"]
                and m["start"] >= last["start"] and m["start"] - last["end"] < join_gap):
            if reframe_every > 0 and last["end"] - last["start"] >= reframe_every:
                border = round((last["end"] + m["start"]) / 2, 3)        # the quiet point between the two sentences
                last["end"] = border
                shots.append({"source": m["source"], "start": border, "end": m["end"], "sentences": [k],
                              "continues": True})
            else:
                last["end"] = max(last["end"], m["end"])
                last["sentences"].append(k)
        else:
            shots.append({"source": m["source"], "start": m["start"], "end": m["end"], "sentences": [k]})
        previous = k
    out: list[dict] = []
    for shot in shots:                     # a reframed tail too short to register goes back to its shot
        if shot.get("continues") and out and shot["end"] - shot["start"] < MIN_REFRAMED:
            out[-1]["end"] = shot["end"]
            out[-1]["sentences"] += shot["sentences"]
        else:
            out.append(shot)
    return out
