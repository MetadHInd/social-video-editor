"""Projects on disk: project.yaml (or legacy proyecto.yaml), plan.json, version folders, outputs."""
from __future__ import annotations

import shutil
from pathlib import Path

from ...domain.models import Project, UserError
from . import legacy_es
from .jsonio import read_json, read_yaml, write_json, write_yaml
from .recipes import YamlRecipeRepository
from .workspace import subdir, subdirs, workspace

MUSIC_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")


class YamlProjectRepository:
    def __init__(self, recipes: YamlRecipeRepository):
        self.recipes = recipes

    # ── projects ─────────────────────────────────────────────────────────────
    def resolve(self, name_or_path: str) -> Path:
        """A path, or the name of a project inside <workspace>/projects (or legacy proyectos/)."""
        p = Path(name_or_path)
        if not p.exists():
            for d in subdirs("projects"):
                if (d / name_or_path).exists():
                    return d / name_or_path
        return p

    def project_file(self, folder: Path) -> Path | None:
        for name in ("project.yaml", legacy_es.FILES["project.yaml"]):
            if (folder / name).exists():
                return folder / name
        return None

    def load(self, folder: str | Path) -> Project:
        d = Path(folder).expanduser().resolve()
        f = self.project_file(d)
        if f is None:
            raise UserError(f"Missing {d / 'project.yaml'}. Create one with: sve new {d.name}")
        data = legacy_es.project(read_yaml(f))
        recipe = legacy_es.recipe_name(data.get("recipe", "base"))
        settings = data.get("settings") or {}
        sources = []
        for s in data.get("sources", []):
            s = {"path": s} if isinstance(s, str) else dict(s)
            s["path"] = str(Path(s["path"]).expanduser())
            if not Path(s["path"]).exists():
                raise UserError(f"Source not found: {s['path']}")
            sources.append(s)
        project = Project(dir=d, name=data.get("name", d.name), recipe=recipe, sources=sources,
                          settings=settings, cfg=self.recipes.load(recipe, settings), file=f)
        project.cache.mkdir(exist_ok=True)
        return project

    def create(self, name: str, recipe: str, sources: list[str]) -> Path:
        d = subdir("projects") / name
        d.mkdir(parents=True, exist_ok=True)
        data = {"name": name, "recipe": legacy_es.recipe_name(recipe),
                "sources": [{"path": str(Path(f).expanduser().resolve())} for f in sources], "settings": {}}
        write_yaml(d / "project.yaml", data)
        return d / "project.yaml"

    # ── plan ─────────────────────────────────────────────────────────────────
    def load_plan(self, project: Project) -> dict | None:
        if not project.plan_path.exists():
            return None
        return legacy_es.plan(read_json(project.plan_path))

    def save_plan(self, project: Project, plan: dict) -> Path:
        """Writes plan.json; an existing one is kept as plan_previous.json."""
        if project.plan_path.exists():
            shutil.copy(project.plan_path, project.dir / "plan_previous.json")
        write_json(project.plan_path, plan)
        return project.plan_path

    # ── version folders ──────────────────────────────────────────────────────
    def new_version(self, project: Project) -> Path:
        n = 1
        while (project.dir / f"v{n}").exists():
            n += 1
        d = project.dir / f"v{n}"
        d.mkdir()
        return d

    def variant_folder(self, project: Project, suffix: str) -> Path:
        """Variant of the latest version with the same number: v3 → v3SinMusica / v3NoMusic, v3Youtube…

        If it already exists a counter is added (v3SinMusica_2) so nothing is overwritten.
        """
        base = self.last_version(project)
        n = int(base.name[1:]) if base else 1
        d = project.dir / f"v{n}{suffix}"
        k = 2
        while d.exists():
            d = project.dir / f"v{n}{suffix}_{k}"
            k += 1
        d.mkdir()
        return d

    def last_version(self, project: Project) -> Path | None:
        """Latest main version (v1, v2…); variants do not count."""
        vs = sorted((p for p in project.dir.glob("v*") if p.is_dir() and p.name[1:].isdigit()),
                    key=lambda p: int(p.name[1:]))
        return vs[-1] if vs else None

    def mark_failed(self, folder: Path, suffix: str) -> Path:
        failed = folder.with_name(folder.name + suffix)
        folder.rename(failed)
        return failed

    # ── outputs ──────────────────────────────────────────────────────────────
    def write_json(self, path: Path, data) -> None:
        write_json(path, data)

    def write_text(self, path: Path, text: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")

    def remove(self, path: Path) -> None:
        path = Path(path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    # ── music ────────────────────────────────────────────────────────────────
    def music_dir(self) -> Path:
        return subdir("music")

    def find_music(self, music_cfg: dict, project_dir: Path) -> Path | None:
        """music.file (relative to the project or the workspace); none/false = no music;
        otherwise the first track of music.folder (default <workspace>/music)."""
        f = music_cfg.get("file")
        if f in (False, "none"):
            return None
        if f:
            p = Path(f).expanduser()
            if not p.is_absolute():
                p = (project_dir / p) if (project_dir / p).exists() else workspace() / p
            if not p.exists():
                raise UserError(f"Music not found: {p}")
            return p
        folder = workspace() / music_cfg["folder"] if music_cfg.get("folder") else self.music_dir()
        tracks = sorted(p for p in folder.glob("*") if p.suffix.lower() in MUSIC_EXT) if folder.exists() else []
        return tracks[0] if tracks else None
