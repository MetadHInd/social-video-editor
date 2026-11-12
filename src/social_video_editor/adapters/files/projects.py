"""Projects on disk: project.yaml (or legacy proyecto.yaml), plan.json, version folders, outputs."""
from __future__ import annotations

import hashlib
import itertools
import os
import shutil
import time
from pathlib import Path

from ...domain.models import Project, UserError
from ...log import log
from . import legacy_es
from .jsonio import read_json, read_yaml, write_json, write_yaml
from .recipes import YamlRecipeRepository
from .workspace import subdir, subdirs, workspace

RENAME_ATTEMPTS = 3          # mark_failed: tries of the rename, RENAME_RETRY_SECONDS apart
RENAME_RETRY_SECONDS = 0.3
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

    def versions(self, project: Project) -> list[Path]:
        """Every version folder (v1, v2, v2SinMusica, v3_failed…) sorted by number, then name."""
        def key(p: Path):
            digits = "".join(itertools.takewhile(str.isdigit, p.name[1:]))
            return (int(digits), p.name) if digits else (-1, p.name)
        return sorted((p for p in project.dir.glob("v*") if p.is_dir() and p.name[1:2].isdigit()), key=key)

    def load_version(self, folder: str | Path, version: str | None = None) -> dict:
        """A rendered version, read only: {"dir", "name", "video", "summary", "plan", "config", "stems"}.

        version=None → the latest main version. Legacy Spanish files (resumen.json, plan_usado.json,
        config_usada.json) are translated; the config is completed with the base recipe defaults.
        Missing files give empty dicts; "stems" maps original/music/sfx to the wavs that exist.
        """
        d = Path(folder)
        if version:
            vdir = d / version
        else:
            vs = sorted((p for p in d.glob("v*") if p.is_dir() and p.name[1:].isdigit()), key=lambda p: int(p.name[1:]))
            vdir = vs[-1] if vs else d / "v1"
        if not vdir.is_dir():
            raise UserError(f"Version folder not found: {vdir}")

        def read(name: str) -> dict:
            for n in (name, legacy_es.FILES.get(name)):
                if n and (vdir / n).exists():
                    return read_json(vdir / n) or {}
            return {}

        summary = legacy_es.summary(read("summary.json"))
        config = self.recipes.load("base", read("config_used.json"))
        video = vdir / summary["video"] if summary.get("video") else None
        if video is None or not video.exists():
            mp4 = sorted(p for p in vdir.glob("*.mp4") if not p.name.startswith("_"))
            video = mp4[0] if mp4 else None
        stems = {k: vdir / "stems" / f"{k}.wav" for k in ("original", "music", "sfx")
                 if (vdir / "stems" / f"{k}.wav").exists()}
        return {"dir": vdir, "name": vdir.name, "video": video, "summary": summary,
                "plan": legacy_es.plan(read("plan_used.json")), "config": config, "stems": stems}

    def mark_failed(self, folder: Path, suffix: str) -> Path:
        """Renames a version folder whose render failed (v3 → v3_failed, or v3_failed_2… when taken).

        Runs inside an exception handler, so it never raises: on Windows a file still open (antivirus,
        a player, an FFmpeg that is exiting) blocks the rename for a moment, so it is retried; if it
        still fails the folder keeps its name and that folder is returned."""
        folder = Path(folder)
        failed = folder.with_name(folder.name + suffix)
        k = 2
        while failed.exists():
            failed = folder.with_name(f"{folder.name}{suffix}_{k}")
            k += 1
        for attempt in range(RENAME_ATTEMPTS):
            try:
                os.replace(folder, failed)
                return failed
            except PermissionError as e:
                if attempt + 1 < RENAME_ATTEMPTS:
                    time.sleep(RENAME_RETRY_SECONDS)
                    continue
                log(f"Warning: could not rename {folder.name} to {failed.name} ({e}); it keeps its name.")
            except OSError as e:
                log(f"Warning: could not rename {folder.name} to {failed.name} ({e}); it keeps its name.")
                break
        return folder

    # ── outputs ──────────────────────────────────────────────────────────────
    def write_json(self, path: Path, data) -> None:
        write_json(path, data)

    def read_json(self, path: Path):
        """A JSON output (products.json, check.json…) or None if it is missing or unreadable."""
        try:
            return read_json(path) if Path(path).is_file() else None
        except (OSError, ValueError):
            return None

    def load_summary(self, folder: Path) -> dict | None:
        """summary.json of a version folder (legacy resumen.json is translated)."""
        for name in ("summary.json", legacy_es.FILES["summary.json"]):
            data = self.read_json(Path(folder) / name)
            if isinstance(data, dict):
                return legacy_es.summary(data)
        return None

    def write_text(self, path: Path, text: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return Path(path).exists()

    def file_hash(self, path: Path) -> str | None:
        """SHA-256 of a file's content, None if it is not a file."""
        path = Path(path)
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    # ── script ───────────────────────────────────────────────────────────────
    def read_script(self, project: Project) -> tuple[str, str]:
        """(file name, text) of the narration script: settings script.file (default script.md, or the
        legacy guion.md), relative to the project folder."""
        name = str((project.cfg.get("script") or {}).get("file") or "script.md")
        tried = [name] + ([legacy_es.FILES["script.md"]] if name == "script.md" else [])
        for n in tried:
            p = Path(n).expanduser()
            p = p if p.is_absolute() else project.dir / p
            if p.is_file():
                return p.name, p.read_text(encoding="utf-8-sig")
        raise UserError(f"Missing the script: {project.dir / name}. Write what is said, one or more sentences per "
                        "line, and cues such as [bars data=\"A:1, B:2\"] between them (docs/PLAN_FORMAT.md).")

    def translate_cue(self, spec: dict) -> dict:
        return legacy_es.cue(spec)

    def remove(self, path: Path) -> None:
        """Deletes a temporary file or folder. A file another program keeps open (Windows) is left in
        place with a warning: a leftover temporary file must not fail a finished render."""
        path = Path(path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return
        try:
            path.unlink(missing_ok=True)
        except PermissionError as e:
            log(f"Warning: could not delete {path.name} ({e}); delete it later by hand.")

    def clear_files(self, folder: Path, pattern: str) -> None:
        folder = Path(folder)
        if folder.is_dir():
            for p in folder.glob(pattern):
                if p.is_file():
                    try:
                        p.unlink()
                    except PermissionError as e:       # open in another program (Windows)
                        log(f"Warning: could not delete {p.name} ({e}); it is left in place.")

    def list_files(self, folder: Path) -> list[Path]:
        folder = Path(folder)
        return sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []

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

    def find_playlist(self, music_cfg: dict, project_dir: Path) -> list[Path]:
        """music.playlist: a list of tracks and/or folders (a folder = its audio files sorted by
        name), or one folder. Relative entries are looked up in the project folder, then in the
        workspace music folder, then in the workspace. [] when there is no playlist."""
        entries = music_cfg.get("playlist")
        if not entries or music_cfg.get("file") in (False, "none"):
            return []
        if isinstance(entries, (str, Path)):
            entries = [entries]
        out: list[Path] = []
        for e in entries:
            p = Path(str(e)).expanduser()
            if not p.is_absolute():
                for base in (project_dir, self.music_dir(), workspace()):
                    if (base / p).exists():
                        p = base / p
                        break
                else:
                    raise UserError(f"Playlist entry not found: {e}")
            if p.is_dir():
                out += sorted((f for f in p.iterdir() if f.is_file() and f.suffix.lower() in MUSIC_EXT),
                              key=lambda f: f.name.lower())
            elif p.exists():
                out.append(p)
            else:
                raise UserError(f"Playlist entry not found: {p}")
        if not out:
            raise UserError("music.playlist has no audio files")
        return out
