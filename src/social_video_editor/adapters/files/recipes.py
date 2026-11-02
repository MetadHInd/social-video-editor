"""Recipes: <workspace>/recipes/<name>.yaml (or legacy recetas/) first, then the packaged ones."""
from __future__ import annotations

from pathlib import Path

from ...domain.config import deep_merge, resolve
from ...domain.models import UserError
from . import legacy_es
from .jsonio import read_yaml
from .workspace import PACKAGE_RECIPES, subdirs


class YamlRecipeRepository:
    def dirs(self) -> list[Path]:
        """Where recipes are looked up, by priority."""
        return [*subdirs("recipes"), PACKAGE_RECIPES]

    def path(self, name: str) -> Path | None:
        name = legacy_es.recipe_name(name)
        files = [f for f in (name, legacy_es.legacy_recipe_name(name)) if f]
        for d in self.dirs():
            for f in files:
                if (d / f"{f}.yaml").exists():
                    return d / f"{f}.yaml"
        return None

    def available(self) -> dict[str, Path]:
        """name → file (a workspace recipe hides the packaged one with the same name)."""
        out = {}
        for d in reversed(self.dirs()):
            for p in sorted(d.glob("*.yaml")):
                out[legacy_es.recipe_name(p.stem)] = p
        return dict(sorted(out.items()))

    def read(self, name: str) -> dict:
        """One recipe file alone (translated to English keys)."""
        path = self.path(name)
        if path is None:
            raise UserError(f"Recipe '{name}' not found. Available: {', '.join(self.available())}")
        return legacy_es.config(read_yaml(path))

    def load(self, name: str, settings: dict | None = None) -> dict:
        """base.yaml → <name>.yaml → project settings, merged and resolved."""
        cfg = self.read("base")
        name = legacy_es.recipe_name(name or "base")
        if name != "base":
            cfg = deep_merge(cfg, self.read(name))
        return resolve(deep_merge(cfg, legacy_es.config(settings or {})))
