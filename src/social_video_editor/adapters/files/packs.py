"""Packs: <workspace>/packs/<name>/ — the look and the reusable drawings of one channel or brand.

    packs/<name>/pack.yaml     theme: {…}            overrides of the graphics theme (domain/graphics/theme.py)
                               modules: [elements.py]  Python files that register templates / elements
    packs/<name>/elements.py   optional (loaded by default when it exists)
    packs/<name>/fonts/…       font files the theme points to (paths relative to the pack folder)
    packs/<name>/brands.yaml   optional: brands whose official channel may be used for b-roll
                               brands: [{name: …, channel_id: UC…, aliases: […]}]

A pack module is ordinary Python run once per process:

    from social_video_editor.domain.graphics import element, template, Scene, kit, paths, motion

    @element("marker")
    def marker(cv, st, x, y, t=0.0, **options): ...
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ...domain.models import UserError
from .jsonio import read_yaml
from .workspace import subdirs

_loaded: dict[Path, object] = {}


class FilePackRepository:
    def dirs(self) -> list[Path]:
        return subdirs("packs")

    def available(self) -> list[str]:
        return sorted({p.parent.name for d in self.dirs() for p in d.glob("*/pack.yaml")})

    def folder(self, name: str) -> Path:
        for d in self.dirs():
            if (d / name / "pack.yaml").is_file():
                return d / name
        raise UserError(f"Pack '{name}' not found (looked for packs/{name}/pack.yaml). "
                        f"Available: {', '.join(self.available()) or 'none'}")

    def load(self, name: str) -> dict:
        """{"name", "dir", "theme"}; registers the pack's templates and elements (once per process)."""
        folder = self.folder(name)
        data = read_yaml(folder / "pack.yaml") or {}
        theme = data.get("theme") or {}
        for role, spec in (theme.get("fonts") or {}).items():
            theme["fonts"][role] = _resolve_font(spec, folder)
        modules = data.get("modules")
        if modules is None:
            modules = ["elements.py"] if (folder / "elements.py").is_file() else []
        for module in modules:
            _run_module(folder / module, name)
        return {"name": name, "dir": folder, "theme": theme}


    def brands(self, name: str) -> list[dict]:
        """The brands listed in the pack's brands.yaml (`brands: [...]`, or the list alone); [] without it."""
        path = self.folder(name) / "brands.yaml"
        if not path.is_file():
            return []
        data = read_yaml(path) or []
        listed = data.get("brands") if isinstance(data, dict) else data
        if not isinstance(listed, list):
            raise UserError(f"Pack '{name}': brands.yaml must hold a list of brands (brands: [{{name, channel_id}}])")
        return listed


def _resolve_font(spec, folder: Path):
    """A font file given relative to the pack folder becomes an absolute path."""
    if isinstance(spec, dict):
        return {**spec, "file": _resolve_font(spec.get("file"), folder)}
    if isinstance(spec, str) and (folder / spec).is_file():
        return str((folder / spec).resolve())
    return spec


def _run_module(path: Path, pack: str) -> None:
    path = path.resolve()
    if path in _loaded:
        return
    if not path.is_file():
        raise UserError(f"Pack '{pack}': module not found: {path}")
    name = f"sve_pack_{pack}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:                  # noqa: BLE001  (a pack is the user's code: say which file failed)
        sys.modules.pop(name, None)
        raise UserError(f"Pack '{pack}': {path.name} failed to load: {type(e).__name__}: {e}") from e
    _loaded[path] = module
