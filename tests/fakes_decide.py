"""Test doubles for the decider (domain/decide.py, application/decide.py): no model is loaded."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from social_video_editor.adapters.files.cache import JsonCacheStore
from social_video_editor.domain import decide as dd


@dataclass
class FakeDecider:
    """A `Decide` that answers some topics from a table and leaves the rest to the rules.

    table: {topic: values (option values) | callable(question) -> values | None}. Answers that are
    not valid for the question fall back to the rules (like the real decider)."""
    table: dict = field(default_factory=dict)
    confidence: float = 0.95
    source: str = "llm"
    asked: list = field(default_factory=list)

    def __call__(self, q: dd.Question) -> dd.Answer:
        self.asked.append(q)
        if q.topic not in self.table:
            return dd.rules_only(q)
        got = self.table[q.topic]
        values = got(q) if callable(got) else got
        if values is None:
            return dd.rules_only(q)
        values = (values,) if isinstance(values, str) else tuple(values)
        valid = dd.validate(q, values)
        return dd.Answer(valid, self.confidence, self.source) if valid is not None else dd.rules_only(q)

    def topics(self) -> list[str]:
        return [q.topic for q in self.asked]


@dataclass
class FakeClassifier:
    """TextClassifier: score(text, label) -> float (default 0.5); records batches and releases."""
    score: Callable[[str, str], float] = lambda text, label: 0.5
    reason: str | None = None
    batches: list = field(default_factory=list)
    released: int = 0
    log: list | None = None                  # a shared list to check the order of the calls

    def ready(self, settings: dict, download: bool = False) -> str | None:
        return self.reason

    def classify(self, items, settings):
        self.batches.append(list(items))
        if self.log is not None:
            self.log.append("nli")
        return [[self.score(text, label) for label in labels] for text, labels, _, _ in items]

    def release(self) -> None:
        self.released += 1
        if self.log is not None:
            self.log.append("nli-release")


@dataclass
class FakeReasoner:
    """Reasoner: answer(prompt, keys) -> (letters, confidence); records prompts and releases."""
    answer: Callable[[str, list[str]], tuple[list[str], float | None]] = lambda prompt, keys: ([keys[0]], 0.9)
    reason: str | None = None
    prompts: list = field(default_factory=list)
    images: list = field(default_factory=list)
    released: int = 0
    log: list | None = None

    def ready(self, settings: dict, download: bool = False) -> str | None:
        return self.reason

    def choose(self, prompt, keys, settings, image: Path | None = None, many: bool = False):
        self.prompts.append(prompt)
        self.images.append(image)
        if self.log is not None:
            self.log.append("llm")
        return self.answer(prompt, keys)

    def release(self, settings: dict) -> None:
        self.released += 1
        if self.log is not None:
            self.log.append("llm-release")


@dataclass
class FakeVisual:
    released: int = 0
    log: list | None = None

    def release(self) -> None:
        self.released += 1
        if self.log is not None:
            self.log.append("clip-release")


def decider_services(tmp_path: Path, classifier=None, reasoner=None, visual=None):
    """(svc, project) with only what application/decide.py uses: a cache, the two models, vision."""
    cache = tmp_path / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    svc = SimpleNamespace(cache=JsonCacheStore(), classifier=classifier, reasoner=reasoner, visual=visual)
    project = SimpleNamespace(cache=cache, cfg={"language": "es", "decider": {"enabled": "auto"}})
    return svc, project
