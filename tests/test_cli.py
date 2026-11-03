"""Entry point: python -m social_video_editor works from any folder; Spanish aliases still work."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _run(*args, cwd, workspace=None):
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    env.pop("SVE_WORKSPACE", None)
    env.pop("MOTOR_ESPACIO", None)
    if workspace:
        env["SVE_WORKSPACE"] = str(workspace)
    return subprocess.run([sys.executable, "-m", "social_video_editor", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True).stdout


def test_recipes_from_current_folder(tmp_path):
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "mine.yaml").write_text("# RECIPE: MINE\nduration: {target: 9}\n", encoding="utf-8")
    out = _run("recipes", cwd=tmp_path)                          # no SVE_WORKSPACE: the current folder
    names = [ln.split()[0] for ln in out.splitlines()]
    assert {"base", "unboxing", "review", "before_after", "mine"} <= set(names)
    assert "RECIPE: MINE" in out


def test_legacy_recipes_folder_and_alias(tmp_path):
    (tmp_path / "recetas").mkdir()
    (tmp_path / "recetas" / "mia.yaml").write_text("# RECETA: MÍA\nduracion: {objetivo: 9}\n", encoding="utf-8")
    out = _run("recetas", cwd=tmp_path)                          # Spanish command alias + recetas/ folder
    assert "mia" in [ln.split()[0] for ln in out.splitlines()]
    assert "RECETA: MÍA" in out


def test_workspace_env_has_priority(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "mine.yaml").write_text("# x\n", encoding="utf-8")
    out = _run("recipes", cwd=tmp_path, workspace=other)
    assert "mine" not in [ln.split()[0] for ln in out.splitlines()]


def test_new_creates_project_in_workspace(tmp_path):
    clip = tmp_path / "a.mov"
    clip.write_bytes(b"")
    _run("new", "demo", "--recipe", "vlog", "--sources", str(clip), cwd=tmp_path)
    text = (tmp_path / "projects" / "demo" / "project.yaml").read_text(encoding="utf-8")
    assert "recipe: vlog" in text and str(clip) in text


def test_new_spanish_alias_uses_existing_legacy_folder(tmp_path):
    clip = tmp_path / "a.mov"
    clip.write_bytes(b"")
    (tmp_path / "proyectos").mkdir()
    _run("nuevo", "demo", "--receta", "resena", "--fuentes", str(clip), cwd=tmp_path)
    text = (tmp_path / "proyectos" / "demo" / "project.yaml").read_text(encoding="utf-8")
    assert "recipe: review" in text


def test_package_version_matches_pyproject():
    import tomllib

    import social_video_editor

    root = Path(__file__).resolve().parents[1]
    with open(root / "pyproject.toml", "rb") as f:
        assert tomllib.load(f)["project"]["version"] == social_video_editor.__version__
