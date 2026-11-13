"""Local language model (Ollama) for the decider — skeleton; the full adapter is written next."""
from __future__ import annotations

from pathlib import Path


class OllamaReasoner:
    def ready(self, settings: dict, download: bool = False) -> str | None:
        return "not available yet"

    def choose(self, prompt: str, keys: list[str], settings: dict, image: Path | None = None,
               many: bool = False) -> tuple[list[str], float | None]:
        return [], None

    def release(self, settings: dict) -> None:
        return None
