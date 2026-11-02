"""Use cases: create a project, list recipes."""
from __future__ import annotations

from pathlib import Path

from .services import Services


def new_project(svc: Services, name: str, recipe: str, sources: list[str]) -> Path:
    svc.recipes.read(recipe)                       # fails early on an unknown recipe
    return svc.projects.create(name, recipe, sources)


def recipe_lines(svc: Services) -> list[str]:
    return [f"{name:<15} {svc.recipes.description(name)}" for name in svc.recipes.available()]
