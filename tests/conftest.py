"""Fixtures comunes: cada prueba trabaja en un espacio temporal (nada se escribe fuera de tmp_path)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from motor_edicion.config import cargar_receta
from motor_edicion.util import FFMPEG


@pytest.fixture(autouse=True)
def espacio(tmp_path, monkeypatch) -> Path:
    """Espacio de trabajo y caché de usuario aislados en tmp_path."""
    esp = tmp_path / "espacio"
    esp.mkdir()
    monkeypatch.setenv("MOTOR_ESPACIO", str(esp))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache_usuario"))
    return esp


@pytest.fixture
def cfg() -> dict:
    """Receta base del paquete (el espacio temporal no tiene recetas propias)."""
    return cargar_receta("base")


def hay_ffmpeg() -> bool:
    return Path(FFMPEG).exists() or shutil.which("ffmpeg") is not None


requiere_ffmpeg = pytest.mark.skipif(not hay_ffmpeg(), reason="FFmpeg no está instalado")
