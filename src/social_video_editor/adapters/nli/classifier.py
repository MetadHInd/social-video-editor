"""Zero-shot text classifier (NLI) for the decider — skeleton; the full adapter is written next."""
from __future__ import annotations


class NliClassifier:
    def ready(self, settings: dict, download: bool = False) -> str | None:
        return "not available yet"

    def classify(self, items: list, settings: dict) -> list[list[float]]:
        return [[] for _ in items]

    def release(self) -> None:
        return None
