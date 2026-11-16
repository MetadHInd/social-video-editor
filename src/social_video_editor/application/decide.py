"""Use case: the decider — closed questions of the domain (domain/decide.py) answered by local models.

`run_decided(svc, project, fn)` runs a pure domain step `fn(decide)` twice or more:

  1. with the rules' answers, writing down every question it asks;
  2. the questions the rules are unsure of are answered in batches — the small zero-shot classifier
     first (then it is unloaded), then the local language model (the vision model is unloaded before
     it, and it is unloaded after) — and the answers are kept in the project cache;
  3. `fn` runs again with those answers; new questions (a different card asks other things) are
     answered the same way, at most MAX_PASSES times.

Without models (or with `decider.enabled: false`) it is `fn(rules_only)`: the engine exactly as before.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from ..domain import decide as dd
from ..domain.models import Project
from ..log import log
from .services import Services

T = TypeVar("T")
MAX_PASSES = 3
CACHE_KIND = "decisions"
_told: set[str] = set()                 # reasons already logged (once per run)


def _tell(message: str) -> None:
    if message not in _told:
        _told.add(message)
        log(message)


def _on(value) -> bool | None:
    """True / False / None (auto) from a setting."""
    if value is True or str(value).strip().lower() in ("true", "yes", "si", "sí", "1", "on"):
        return True
    if value is False or value is None or str(value).strip().lower() in ("false", "no", "0", "off"):
        return False
    return None


def decider_available(svc: Services, cfg: dict) -> tuple[bool, bool]:
    """(small classifier usable, language model usable) now; logs once why one is not."""
    s = dd.settings(cfg)
    enabled = _on(s["enabled"])
    if enabled is False:
        return False, False
    nli = False
    if svc.classifier is not None:
        reason = svc.classifier.ready(s, download=enabled is True)
        nli = reason is None
        if reason:
            _tell(f"Decider: small classifier not used ({reason})")
    llm = False
    if svc.reasoner is not None and _on(s["llm"]) is not False:
        reason = svc.reasoner.ready(s, download=False)
        llm = reason is None
        if reason:
            _tell(f"Decider: language model not used ({reason})")
    return nli, llm


class Decisions:
    """A `Decide` callable backed by the project's cache of answers, plus `settle()` to answer what
    the rules were unsure of with the models that are available."""

    def __init__(self, svc: Services, project: Project, cfg: dict | None = None):
        self.svc, self.project = svc, project
        self.cfg = cfg if cfg is not None else project.cfg
        self.s = dd.settings(self.cfg)
        self.nli, self.llm = decider_available(svc, self.cfg)
        raw = f"{self.s['nli_model']}|{self.s['llm_model']}|{dd.ROUTES_VERSION}"
        self.fingerprint = hashlib.sha1(raw.encode()).hexdigest()[:10]
        data = svc.cache.load(project.cache, CACHE_KIND, self.fingerprint) if self.active else None
        known = {k: a for k, v in (data or {}).items() if isinstance(v, dict) and (a := dd.Answer.from_dict(v))}
        self.recorder = dd.Recorder(known)
        self.changed = 0                    # answers given by a model (not the rules), this run

    @property
    def active(self) -> bool:
        return self.nli or self.llm

    def __call__(self, q: dd.Question) -> dd.Answer:
        return self.recorder(q)

    def due(self) -> list[dd.Question]:
        """Questions asked, not answered yet, that the rules are unsure of and a usable stage may answer."""
        usable = {"nli": self.nli, "llm": self.llm}
        return [q for k, q in self.recorder.asked.items() if k not in self.recorder.known and dd.needs(q, self.s)
                and any(usable[st] for st in dd.stages(q))]

    def settle(self) -> int:
        """Answers the questions due (small model, then language model) → how many were answered."""
        due = self.due()
        if not due:
            return 0
        answers = {q.key: dd.rules_only(q) for q in due}
        threshold = float(self.s["threshold"])
        nli_due = [q for q in due if self.nli and "nli" in dd.stages(q)]
        if nli_due:
            try:
                scores = self.svc.classifier.classify([dd.nli_input(q) for q in nli_due], self.s)
            except Exception as e:          # a model failure never stops the video: the rules answer
                _tell(f"Decider: small classifier failed ({type(e).__name__}: {e})")
                scores = []
            finally:
                self.svc.classifier.release()
            for q, sc in zip(nli_due, scores):
                a = dd.pick(q, "nli", sc, None, self.s, answers[q.key])
                if a is not None:
                    answers[q.key] = a
        llm_due = [q for q in due if self.llm and "llm" in dd.stages(q) and answers[q.key].confidence < threshold]
        if llm_due:
            release = getattr(self.svc.visual, "release", None)
            if callable(release):
                release()                   # one model in memory at a time
            log(f"Decider: {len(llm_due)} question(s) for the language model ({self.s['llm_model']})…")
            try:
                for q in llm_due:
                    prompt, keys, many = dd.llm_request(q)
                    try:
                        chosen, conf = self.svc.reasoner.choose(prompt, keys, self.s,
                                                                Path(q.image) if q.image else None, many)
                    except Exception as e:
                        _tell(f"Decider: language model failed ({type(e).__name__}: {e})")
                        continue
                    a = dd.pick(q, "llm", chosen, conf, self.s, answers[q.key])
                    if a is not None:
                        answers[q.key] = a
            finally:
                self.svc.reasoner.release(self.s)
        self.recorder.known.update(answers)
        self.changed += sum(a.source != "rules" for a in answers.values())
        self.svc.cache.save(self.project.cache, CACHE_KIND, self.fingerprint,
                            {k: a.as_dict() for k, a in self.recorder.known.items()})
        return len(due)


def run_decided(svc: Services, project: Project, fn: Callable[[dd.Decide], T], cfg: dict | None = None) -> T:
    """fn(decide), with the questions the rules are unsure of answered by the local models (see the
    module docstring). Without models it is fn(rules_only)."""
    d = Decisions(svc, project, cfg)
    if not d.active:
        return fn(dd.rules_only)
    result = fn(d)
    for _ in range(MAX_PASSES):
        if d.settle() == 0:
            break
        result = fn(d)
    if d.changed:
        log(f"Decider: {d.changed} answer(s) from the local models (cached in {CACHE_KIND}_{d.fingerprint}.json)")
    return result
