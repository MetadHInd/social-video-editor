"""Core data types shared by every layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class UserError(SystemExit):
    """A problem with the user's input (bad plan, missing file, unknown recipe…).

    It is a SystemExit so the CLI prints only the message and exits with status 1.
    """


class PlanError(UserError):
    """The edit plan is inconsistent (bad shot reference, circular dependency…)."""


@dataclass
class Project:
    """One video to produce: its sources, its recipe, its settings and its working folder."""

    dir: Path
    name: str
    recipe: str
    sources: list[dict]                     # [{"path": "/abs/clip.mov", …}] in story order
    settings: dict = field(default_factory=dict)   # project overrides on top of the recipe
    cfg: dict = field(default_factory=dict)        # fully merged + resolved configuration
    file: Path | None = None                # project.yaml (or legacy proyecto.yaml)

    @property
    def cache(self) -> Path:
        return self.dir / "cache"

    @property
    def plan_path(self) -> Path:
        return self.dir / "plan.json"

    @property
    def language(self) -> str:
        return self.cfg.get("language", "es")
