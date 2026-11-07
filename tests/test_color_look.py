"""Color look: copying a reference video's look, fade and tint (synthetic frames only)."""
from __future__ import annotations

import numpy as np
import pytest

from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.application import render as app_render
from social_video_editor.domain import color_math
from social_video_editor.domain.models import UserError


def frames(level: float, spread: float, bgr=(1.0, 1.0, 1.0), seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(4):
        y = np.clip(rng.normal(level, spread, (40, 30, 1)), 0, 1)
        out.append((np.clip(y * np.array(bgr), 0, 1) * 255).astype(np.uint8))
    return out


def test_measure_has_percentiles_and_highlight_color():
    s = color_math.measure(frames(0.5, 0.2, bgr=(0.8, 0.9, 1.0)))          # warm (BGR)
    assert s["p02"] < s["p05"] < s["luma"] < s["p95"] < s["p98"]
    assert s["hr"] > s["hb"] and s["hr"] > s["r"]


def test_match_reference_moves_shot_towards_reference():
    shot = color_math.measure(frames(0.45, 0.28, bgr=(0.8, 0.95, 1.0)))   # dark-ish, contrasty, yellow
    ref = color_math.measure(frames(0.58, 0.15, bgr=(1.0, 0.94, 1.0)))   # bright, flat, pink
    p = color_math.match_reference(shot, ref)
    il, ih, ol, oh = p["linear"]
    assert ol > 0.05 and oh <= 1.0 and 0 <= il < ih <= 1                   # lifted blacks
    r, g, b = p["gains"]
    assert b > 1 > r and all(0.9 <= x <= 1.1 for x in p["gains"])        # less yellow, limited
    assert p["gamma"] > 1                                                  # brighter
    none = color_math.match_reference(shot, ref, strength=0)
    assert none["gains"] == (1, 1, 1) and none["gamma"] == 1 and none["linear"][2] == 0


def test_correction_uses_reference_and_chain_has_levels():
    shot = color_math.measure(frames(0.45, 0.28))
    ref = color_math.measure(frames(0.58, 0.15))
    cfg = {"auto": True, "_reference": ref}
    assert "linear" in color_math.correction(shot, cfg)
    chain = filters.correction_chain(shot, cfg)
    assert "romin=" in chain and "rimin=" in chain and "eq=gamma=" in chain
    assert "romin=" not in filters.correction_chain(shot, {"auto": True})  # default unchanged


def test_fade_and_tint_filters():
    c = color_math.creative({"fade": 0.1, "tint": 0.5})
    assert c == {"fade": 0.1, "tint": 0.5}
    chain = filters.creative_chain(c)
    assert "colorbalance=gs=-0.050" in chain and "romin=0.100" in chain and "romax=0.950" in chain
    assert color_math.creative({"fade": 0, "tint": 0}) == {}


class _Probe:
    def duration(self, path):
        return 10.0

    def fingerprint(self, path):
        return {"path": str(path), "size": path.stat().st_size if path.exists() else None, "mtime_ns": None}


class _Video:
    def read_frames(self, path, times):
        assert len(list(times)) == app_render.REFERENCE_FRAMES
        return frames(0.6, 0.1)


class _Svc:
    probe, video = _Probe(), _Video()


class _Project:
    def __init__(self, d):
        self.dir = d


def test_apply_color_reference(tmp_path):
    (tmp_path / "ref.mp4").write_bytes(b"x")
    cfg = {"color": {"reference": "ref.mp4"}}
    app_render.apply_color_reference(_Svc(), _Project(tmp_path), cfg)
    assert 0.5 < cfg["color"]["_reference"]["luma"] < 0.7
    plain = {"color": {"reference": None}}
    app_render.apply_color_reference(_Svc(), _Project(tmp_path), plain)
    assert "_reference" not in plain["color"]
    with pytest.raises(UserError):
        app_render.apply_color_reference(_Svc(), _Project(tmp_path), {"color": {"reference": "missing.mp4"}})
