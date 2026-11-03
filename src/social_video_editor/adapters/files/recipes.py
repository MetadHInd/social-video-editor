"""Recipes: <workspace>/recipes/<name>.yaml (or legacy recetas/) first, then the packaged ones."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from ...domain.config import deep_merge, resolve
from ...domain.models import UserError
from . import legacy_es
from .jsonio import read_yaml
from .workspace import PACKAGE_RECIPES, subdirs

VOCABULARY_PACKAGE = "social_video_editor.data.vocabulary"


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

    def description(self, name: str) -> str:
        """First meaningful line of the recipe's comment header ("#" stripped); "" if there is none.

        Decorative lines (rules such as # ─────, # ====) and empty comments are skipped."""
        path = self.path(name)
        if path is None:
            raise UserError(f"Recipe '{name}' not found. Available: {', '.join(self.available())}")
        return header_description(path.read_text(encoding="utf-8"))

    def vocabulary(self, language: str) -> list[str]:
        """Packaged product names (data/vocabulary/products.<language>.txt), comments and blanks skipped."""
        text = resources.files(VOCABULARY_PACKAGE).joinpath(f"products.{language}.txt").read_text(encoding="utf-8")
        return [x.strip() for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")]

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


def header_description(text: str) -> str:
    """First comment line with letters or digits, read from the leading comment block of a YAML file."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break                                   # the header ended: no description
        comment = stripped.lstrip("#").strip()
        if any(ch.isalnum() for ch in comment):
            return comment
    return ""
