"""Punto de entrada: python -m motor_edicion funciona desde cualquier carpeta."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _correr(*args, cwd, espacio=None):
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    env.pop("MOTOR_ESPACIO", None)
    if espacio:
        env["MOTOR_ESPACIO"] = str(espacio)
    return subprocess.run([sys.executable, "-m", "motor_edicion", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True).stdout


def test_recetas_desde_carpeta_actual(tmp_path):
    (tmp_path / "recetas").mkdir()
    (tmp_path / "recetas" / "mia.yaml").write_text("# RECETA: MÍA\nduracion: {objetivo: 9}\n", encoding="utf-8")
    out = _correr("recetas", cwd=tmp_path)                          # sin MOTOR_ESPACIO: la carpeta actual
    nombres = [ln.split()[0] for ln in out.splitlines()]
    assert {"base", "unboxing", "mia"} <= set(nombres)
    assert "RECETA: MÍA" in out


def test_motor_espacio_tiene_prioridad(tmp_path):
    otro = tmp_path / "otro"
    otro.mkdir()
    (tmp_path / "recetas").mkdir()
    (tmp_path / "recetas" / "mia.yaml").write_text("# x\n", encoding="utf-8")
    out = _correr("recetas", cwd=tmp_path, espacio=otro)
    assert "mia" not in [ln.split()[0] for ln in out.splitlines()]


def test_nuevo_crea_proyecto_en_el_espacio(tmp_path):
    clip = tmp_path / "a.mov"
    clip.write_bytes(b"")
    _correr("nuevo", "demo", "--receta", "vlog", "--fuentes", str(clip), cwd=tmp_path)
    texto = (tmp_path / "proyectos" / "demo" / "proyecto.yaml").read_text(encoding="utf-8")
    assert "receta: vlog" in texto and str(clip) in texto
