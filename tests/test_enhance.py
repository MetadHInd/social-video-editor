"""`sve enhance`: upscaler fallback, target sizes, cache skip, Real-ESRGAN tiling (synthetic images only)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from social_video_editor.adapters.upscale.lanczos import LanczosUpscaler
from social_video_editor.adapters.upscale.realesrgan import RealEsrganUpscaler, find_model, tiles, upscale_tiled
from social_video_editor.application.enhance import enhance_photos, enhanced_or_none, enhanced_path
from social_video_editor.cli import parser
from social_video_editor.container import build_services
from test_photos import make_photo

MISSING_MODEL = {"model": "/nonexistent/folder/model.pth"}


def _project(svc, tmp_path, enhance=None):
    from social_video_editor.adapters.files.jsonio import read_yaml, write_yaml

    root = tmp_path / "photos"
    make_photo(root / "small.jpg", size=(160, 120))           # below the 180-px output short side → upscale
    make_photo(root / "big.jpg", size=(800, 600))             # above 1.25 × 180 = 225 → reduce to 225
    make_photo(root / "fine.jpg", size=(240, 200))            # in between → kept as is
    make_photo(root / "turned.jpg", size=(120, 100), orientation=6)
    yaml_path = svc.projects.create("enhance_me", "base", [str(root)])
    data = read_yaml(yaml_path)
    data["settings"] = {"output": {"width": 320, "height": 180}, "enhance": enhance or MISSING_MODEL}
    write_yaml(yaml_path, data)
    return svc.projects.load(yaml_path.parent), root


def test_tiles_cover_every_pixel_once():
    for length, tile, overlap in ((10, 4, 2), (384, 384, 16), (1000, 256, 16), (1, 64, 8)):
        kept = []
        for r0, r1, k0, k1 in tiles(length, tile, overlap):
            assert 0 <= r0 <= k0 < k1 <= r1 <= length and k1 - k0 <= tile
            kept += list(range(k0, k1))
        assert kept == list(range(length))


def test_tiled_matches_whole_image_for_a_local_model():
    rng = np.random.default_rng(1)
    rgb = (rng.random((37, 53, 3)) * 255).astype(np.uint8)

    def nearest_x2(t):                        # a "model" that is purely local: ×2 nearest neighbour
        return t.repeat_interleave(2, dim=2).repeat_interleave(2, dim=3)
    pytest.importorskip("torch")
    out = upscale_tiled(nearest_x2, rgb, 2, "cpu", tile=16, overlap=4)
    assert out.shape == (74, 106, 3)
    assert np.array_equal(out, rgb.repeat(2, axis=0).repeat(2, axis=1))


def test_fallback_upscaler_and_cache_skip(tmp_path, capsys):
    svc = build_services()
    svc.upscaler = RealEsrganUpscaler(fallback=LanczosUpscaler())
    project, root = _project(svc, tmp_path)
    assert "Lanczos" in svc.upscaler.describe(MISSING_MODEL)
    counts = enhance_photos(svc, project)
    assert (counts["upscaled"], counts["downscaled"], counts["kept"], counts["skipped"]) == (2, 1, 1, 0)
    assert counts["methods"] == {"lanczos": 3}
    sizes = {name: Image.open(enhanced_path(svc, project.cache, root / name)).size
             for name in ("small.jpg", "big.jpg", "turned.jpg")}
    assert sizes == {"small.jpg": (240, 180), "big.jpg": (300, 225), "turned.jpg": (180, 216)}
    assert enhanced_or_none(svc, project.cache, root / "fine.jpg") is None
    assert enhanced_or_none(svc, project.cache, root / "small.jpg") is not None
    with Image.open(root / "small.jpg") as im:                # originals untouched
        assert im.size == (160, 120)

    again = enhance_photos(svc, project)
    assert (again["upscaled"], again["downscaled"], again["skipped"]) == (0, 0, 3)

    os.utime(root / "small.jpg", (1_000_000_000, 1_000_000_000))    # a changed file → new cache name
    third = enhance_photos(svc, project)
    assert (third["upscaled"], third["skipped"]) == (1, 2)


def test_enhance_command_and_alias():
    for name in ("enhance", "mejorar"):
        a = parser().parse_args([name, "my_project", "--force"])
        assert a.project == "my_project" and a.force and a.f.__name__ == "cmd_enhance"


def _real_esrgan_reason() -> str | None:
    return RealEsrganUpscaler().ready({})


@pytest.mark.enhance
@pytest.mark.skipif(_real_esrgan_reason() is not None, reason=f"Real-ESRGAN unavailable: {_real_esrgan_reason()}")
def test_real_esrgan_tiny_upscale(tmp_path):
    up = RealEsrganUpscaler()
    assert find_model({}) is not None and "Real-ESRGAN" in up.describe({})
    rng = np.random.default_rng(0)
    rgb = (rng.random((48, 64, 3)) * 255).astype(np.uint8)
    out = up.upscale_array(rgb, {"tile": 32})
    assert out.shape == (192, 256, 3) and out.dtype == np.uint8
    src = tmp_path / "tiny.png"
    Image.fromarray(rgb).save(src)
    assert up.enhance(src, tmp_path / "out.jpg", 100, True, {}) == "realesrgan"
    assert Image.open(tmp_path / "out.jpg").size == (133, 100)
