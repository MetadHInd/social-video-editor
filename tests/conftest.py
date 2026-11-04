"""Common fixtures: every test works in a temporary workspace (nothing is written outside tmp_path)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from social_video_editor.adapters.files.recipes import YamlRecipeRepository
from social_video_editor.adapters.system.tools import FFMPEG


@pytest.fixture(autouse=True, scope="session")
def numba_cache(tmp_path_factory) -> Path:
    """cli.main() points NUMBA_CACHE_DIR to the user cache unless it is set; tests call main() in-process,
    so it is set once here: numba never writes into a test's user cache (or the real one)."""
    mp = pytest.MonkeyPatch()
    folder = tmp_path_factory.mktemp("numba_cache")
    mp.setenv("NUMBA_CACHE_DIR", str(folder))
    yield folder
    mp.undo()


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
