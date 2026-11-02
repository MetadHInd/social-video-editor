"""Common fixtures: every test works in a temporary workspace (nothing is written outside tmp_path)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from social_video_editor.adapters.files.recipes import YamlRecipeRepository
from social_video_editor.adapters.system.tools import FFMPEG


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch) -> Path:
    """Isolated workspace and user cache inside tmp_path."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.delenv("MOTOR_ESPACIO", raising=False)
    monkeypatch.setenv("SVE_WORKSPACE", str(ws))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "user_cache"))
    return ws


@pytest.fixture
def recipes() -> YamlRecipeRepository:
    return YamlRecipeRepository()


@pytest.fixture
def cfg(recipes) -> dict:
    """Packaged base recipe (the temporary workspace has no recipes of its own)."""
    return recipes.load("base")


def has_ffmpeg() -> bool:
    return Path(FFMPEG).exists() or shutil.which("ffmpeg") is not None


requires_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="FFmpeg is not installed")
