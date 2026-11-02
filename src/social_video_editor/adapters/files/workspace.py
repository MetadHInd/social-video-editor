"""The workspace: the folder with projects/, music/, sfx/ and your own recipes/.

It is $SVE_WORKSPACE (legacy: $MOTOR_ESPACIO) or the current folder. Legacy Spanish folder
names (proyectos/, recetas/, musica/) are used when they exist and the English one does not.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import legacy_es

PACKAGE_DIR = Path(__file__).resolve().parents[2]        # engine code (read only)
PACKAGE_RECIPES = PACKAGE_DIR / "recipes"                 # recipes shipped with the package


def workspace() -> Path:
    for var in legacy_es.ENV_WORKSPACE:
        if os.environ.get(var):
            return Path(os.environ[var]).expanduser().resolve()
    return Path.cwd().resolve()


def subdirs(kind: str) -> list[Path]:
    """Candidate folders for projects / recipes / music / sfx (English name first)."""
    ws = workspace()
    names = dict.fromkeys([kind, legacy_es.FOLDERS.get(kind, kind)])
    return [ws / n for n in names]


def subdir(kind: str) -> Path:
    """The folder to use: the English one if it exists, else the legacy one if it exists, else English."""
    cands = subdirs(kind)
    return next((d for d in cands if d.exists()), cands[0])
