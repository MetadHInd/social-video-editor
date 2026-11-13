"""The engine's decider: closed questions about language and pictures, answered by a cascade.

The domain asks; it never calls a model. A `Question` has closed options, the answer the hand rules
give (`default`) and how sure the rules are (`confidence`). A `Decide` callable answers it:

  - `rules_only` returns the rules' answer (the default everywhere: the engine without models);
  - application/decide.py answers from a cache filled by the local models, in this order, only for
    questions the rules are unsure of (confidence < `decider.threshold`): a zero-shot text classifier
    (NLI, small), then a local language model (larger; it may also look at a picture).

A later stage replaces an earlier answer only when its answer is valid (one of the options) and at
least as sure; a language-model answer must also reach `decider.llm_min`. So a model can only make a
decision better, never break one: when it fails, is missing or is unsure, the rules' answer stands.

Which stages may answer each kind of question is `ROUTES` (measured with a labeled set of questions;
`ROUTES_VERSION` changes when it does, so cached answers are asked again).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["choice", "multi", "yesno"]
Source = Literal["rules", "nli", "clip", "llm"]
KINDS = ("choice", "multi", "yesno")
STAGES = ("nli", "llm")
KEYS = "abcdefghijklmnopqrstuvwxyz"      # the language model answers with one letter per option

DEFAULTS = {
    "enabled": False,       # auto = use what is installed · true = may download the small model · false = rules only
    "threshold": 0.8,       # questions the rules answer with less confidence than this go to the models
    "nli_model": "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
    "llm": "auto",          # auto = when a local server has the model · false = never
    "llm_model": "qwen3.5:9b",
    "llm_min": 0.5,         # a language-model answer less sure than this keeps the previous answer
    "timeout": 60,          # seconds per language-model request
    "device": "cpu",        # the small classifier: cpu | mps | cuda
}

# topic → the stages (after the rules) that may answer it, in order
ROUTES: dict[str, tuple[str, ...]] = {
    "label_words": ("llm",),            # which words of a sentence make good on-screen notes
    "title_span": ("llm",),             # which part of a sentence reads well as a card title
    "is_comparison": ("nli", "llm"),    # are these figures set against each other?
    "bar_labels": ("llm",),             # the name of each bar
    "action": ("nli", "llm"),           # what an illustrated scene shows the thing doing
    "icon": ("nli", "llm"),             # which illustration a sentence names
    "card_type": ("llm",),              # which drawing fits a sentence (figure, list, scene, title…)
    "shorten": ("llm",),                # which shorter version of a card's text still says it
    "clip_shows": ("llm",),             # does this picture show what is said? (with the picture)
    "clip_lively": ("llm",),            # does this picture show something happening? (with the picture)
}
ROUTES_VERSION = 1


@dataclass(frozen=True)
class Option:
    value: str              # what the domain uses
    text: str               # what a model reads (in the video's language)


@dataclass(frozen=True)
class Question:
    topic: str                              # a key of ROUTES
    kind: Kind
    prompt: str                             # the question, in the video's language
    options: tuple[Option, ...]
    context: str                            # what it is about (the sentence, the card's text…)
    default: tuple[str, ...]                # the rules' answer (option values; () = none of them)
    confidence: float                       # how sure the rules are (0–1)
    language: str = "es"
    hypothesis: str | None = None           # zero-shot template with "{}" (an option's text); None = NLI cannot answer
    image: str | None = None                # a picture to look at (a file path), for the language model

    @property
    def key(self) -> str:
        """The same question asked again has the same key (cached answers)."""
        raw = json.dumps([self.topic, self.kind, self.prompt, [[o.value, o.text] for o in self.options],
                          self.context, self.language, self.hypothesis, self.image, ROUTES_VERSION],
                         ensure_ascii=False)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def values(self) -> tuple[str, ...]:
        return tuple(o.value for o in self.options)


@dataclass(frozen=True)
class Answer:
    values: tuple[str, ...]
    confidence: float
    source: Source = "rules"

    @property
    def value(self) -> str | None:
        """The single answer of a choice / yes-no question (None when there is none)."""
        return self.values[0] if self.values else None

    def as_dict(self) -> dict:
        return {"values": list(self.values), "confidence": round(float(self.confidence), 4), "source": self.source}

    @staticmethod
    def from_dict(d: dict) -> Answer | None:
        try:
            return Answer(tuple(str(v) for v in d["values"]), float(d["confidence"]), str(d.get("source", "rules")))
        except (KeyError, TypeError, ValueError):
            return None


Decide = Callable[[Question], Answer]


def rules_only(q: Question) -> Answer:
    """The rules' own answer: the engine without models."""
    return Answer(tuple(q.default), float(q.confidence), "rules")


def yes_no(language: str = "es") -> tuple[Option, Option]:
    return (Option("yes", "sí" if language == "es" else "yes"), Option("no", "no"))


def ask(decide: Decide | None, q: Question) -> Answer:
    """decide(q), or the rules' answer without a decider; an invalid answer falls back to the rules."""
    if decide is None:
        return rules_only(q)
    a = decide(q)
    return a if validate(q, a.values) is not None else rules_only(q)


@dataclass
class Recorder:
    """A `Decide` that answers from a table of known answers (by question key) and the rules
    otherwise, and keeps every question it was asked: the first pass of application/decide.py."""
    known: dict[str, Answer] = field(default_factory=dict)
    asked: dict[str, Question] = field(default_factory=dict)

    def __call__(self, q: Question) -> Answer:
        self.asked.setdefault(q.key, q)
        return self.known.get(q.key) or rules_only(q)


def validate(q: Question, values) -> tuple[str, ...] | None:
    """The answer as option values, or None when it is not a valid answer to the question."""
    try:
        got = tuple(str(v) for v in values)
    except TypeError:
        return None
    allowed = set(q.values())
    if any(v not in allowed for v in got) or len(set(got)) != len(got):
        return None
    if q.kind in ("choice", "yesno") and len(got) != 1:
        return None
    return tuple(v for v in q.values() if v in got)          # in the options' order


def settings(cfg: dict | None) -> dict:
    s = ((cfg or {}).get("decider") or {})
    return {k: s.get(k, v) if s.get(k) is not None else v for k, v in DEFAULTS.items()}


def stages(q: Question) -> tuple[str, ...]:
    """The stages that may answer this question (NLI only when it has a hypothesis template)."""
    return tuple(st for st in ROUTES.get(q.topic, ()) if st != "nli" or q.hypothesis)


def needs(q: Question, s: dict) -> bool:
    """Whether the models should be asked: the rules are unsure and a stage may answer."""
    return float(q.confidence) < float(s["threshold"]) and bool(stages(q))


def nli_input(q: Question) -> tuple[str, list[str], str, bool]:
    """(text, option texts, hypothesis template, several may be true) for a zero-shot classifier."""
    return q.context, [o.text for o in q.options], q.hypothesis or "{}", q.kind == "multi"


def llm_request(q: Question) -> tuple[str, list[str], bool]:
    """(prompt, option keys, several may be chosen) for the language model: the options are listed
    with one letter each and the answer is one (or several) of those letters."""
    keys = list(KEYS[:len(q.options)])
    head = {"es": ("Texto", "Opciones", "Responde solo con la letra", "Responde con las letras que correspondan"),
            "en": ("Text", "Options", "Answer with the letter only", "Answer with every letter that applies")}
    t = head.get(q.language, head["en"])
    lines = [q.prompt, "", f"{t[0]}: {q.context}" if q.context else "", f"{t[1]}:"]
    lines += [f"{k}) {o.text}" for k, o in zip(keys, q.options)]
    lines += ["", t[3] if q.kind == "multi" else t[2]]
    return "\n".join(x for x in lines if x is not None), keys, q.kind == "multi"


def from_keys(q: Question, keys: list[str]) -> tuple[str, ...] | None:
    """Option values for the letters the language model chose (None when one is not an option)."""
    index = {k: o.value for k, o in zip(KEYS, q.options)}
    try:
        return validate(q, [index[str(k).strip().lower()] for k in keys])
    except KeyError:
        return None


def pick(q: Question, stage: str, raw, confidence: float | None, s: dict, previous: Answer) -> Answer | None:
    """A stage's answer if it should replace `previous`, else None.

    stage "nli": raw = a score in [0, 1] per option (in order); a choice / yes-no takes the best one
    (its score is the confidence), a multi question takes the options scoring 0.5 or more (the
    confidence is the least clear-cut of them). stage "llm": raw = the option letters chosen;
    confidence None (the model gave none) counts as `llm_min`."""
    if stage == "nli":
        try:
            scores = [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
        if len(scores) != len(q.options):
            return None
        if q.kind == "multi":
            values = tuple(o.value for o, sc in zip(q.options, scores) if sc >= 0.5)
            conf = min(max(sc, 1 - sc) for sc in scores)
        else:
            best = max(range(len(scores)), key=lambda i: scores[i])
            values, conf = (q.options[best].value,), scores[best]
        values = validate(q, values)
    elif stage == "llm":
        values = from_keys(q, list(raw or []))
        conf = float(s["llm_min"]) if confidence is None else float(confidence)
        if conf < float(s["llm_min"]):
            return None
    else:
        return None
    if values is None or conf < float(previous.confidence):
        return None
    return Answer(values, round(conf, 4), stage)   # type: ignore[arg-type]
