"""Heavy libraries load only when they are used: importing the engine (and collecting the tests) stays fast."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

import social_video_editor
from social_video_editor.adapters.upscale import realesrgan

SRC = str(Path(social_video_editor.__file__).resolve().parents[1])     # works without an installed package
HEAVY = ("torch", "spandrel", "sentence_transformers", "transformers", "librosa", "numba", "scipy", "cv2",
         "scenedetect", "faster_whisper", "ctranslate2", "av", "soundfile", "yt_dlp", "skia")


def test_engine_modules_import_without_heavy_libraries():
    code = f"""
import importlib, pkgutil, sys
import social_video_editor as pkg
for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
    if not m.name.endswith("__main__"):
        importlib.import_module(m.name)
print(sorted({{n.split(".")[0] for n in sys.modules}} & set({HEAVY!r})))
"""
    assert _fresh_python(code).strip() == "[]"


def _fresh_python(code: str) -> str:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")]))}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env).stdout


class _Forbid:
    """Meta-path finder that fails any import of the named packages."""

    def __init__(self, names):
        self.names, self.tried = set(names), []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.names:
            self.tried.append(name)
            raise ImportError(f"{name} must not be imported here")
        return None


def test_realesrgan_ready_checks_packages_without_importing_torch(tmp_path, monkeypatch):
    forbid = _Forbid(["torch", "spandrel"])
    for name in ("torch", "spandrel"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [forbid, *sys.meta_path])
    monkeypatch.setattr(realesrgan.importlib.util, "find_spec", lambda name: object())   # as if installed
    why = realesrgan.RealEsrganUpscaler().ready({"model": str(tmp_path / "missing.pth")})
    assert why.startswith("model not found") and forbid.tried == []

    monkeypatch.setattr(realesrgan.importlib.util, "find_spec", lambda name: None)      # not installed
    assert "not installed" in realesrgan.RealEsrganUpscaler().ready({})


def test_enhance_falls_back_to_lanczos_when_torch_fails_to_load(tmp_path, monkeypatch):
    src, out = tmp_path / "photo.jpg", tmp_path / "out.jpg"
    Image.new("RGB", (64, 48), (200, 120, 40)).save(src)
    up = realesrgan.RealEsrganUpscaler()
    monkeypatch.setattr(up, "ready", lambda settings: None)

    def broken(rgb, settings):
        raise ImportError("libtorch_cpu.so: cannot open shared object file")

    monkeypatch.setattr(up, "upscale_array", broken)
    assert up.enhance(src, out, 96, True, {}) == "lanczos"
    with Image.open(out) as im:
        assert min(im.size) == 96


@pytest.mark.parametrize("module", ["social_video_editor.cli", "social_video_editor.container"])
def test_cli_startup_stays_light(module):
    code = f"import sys, {module}; print(sorted({{n.split('.')[0] for n in sys.modules}} & set({HEAVY!r})))"
    assert _fresh_python(code).strip() == "[]"
