"""Landscape (horizontal) output: profile, text scaling, safe zones, polaroid and collage geometry.

Vertical output must not change, so every landscape rule is checked next to its portrait twin.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from social_video_editor import cli
from social_video_editor.adapters.pillow import composer as cm
from social_video_editor.adapters.pillow.text_layer import PillowTextRenderer, blocks
from social_video_editor.domain import check as ck
from social_video_editor.domain.config import (EXPORT_PROFILES, canonical_profile, is_landscape, orientation,
                                               output_name, variant_suffix)
from social_video_editor.domain.text_layout import resolve_style, text_scale, text_width

PORTRAIT, LANDSCAPE, SQUARE = (1080, 1920), (1920, 1080), (1080, 1080)


# ── profiles ─────────────────────────────────────────────────────────────────

def test_landscape_profile_matches_youtube_quality():
    land, yt = EXPORT_PROFILES["landscape"], EXPORT_PROFILES["youtube"]
    assert (land["width"], land["height"]) == (1920, 1080)
    assert (land["crf"], land["maxrate"]) == (yt["crf"], yt["maxrate"])
    assert yt == {"width": 1920, "height": 1080, "crf": 18, "maxrate": "16M"}       # unchanged
    assert canonical_profile("horizontal") == "landscape"
    assert canonical_profile("youtube") == "youtube" and canonical_profile(None) is None


def test_orientation_from_output_size_only():
    assert orientation(*PORTRAIT) == "portrait" and not is_landscape(*PORTRAIT)
    assert orientation(*LANDSCAPE) == "landscape" and is_landscape(*LANDSCAPE)
    assert orientation(*SQUARE) == "square" and not is_landscape(*SQUARE)
    assert orientation(1280, 720) == "landscape"                 # settings output.width/height, no profile


def test_landscape_labels():
    assert variant_suffix("es", profile="landscape") == "Horizontal"
    assert output_name("clip", "en", profile="landscape") == "clip_landscape"


@pytest.mark.parametrize("given, expected", [("horizontal", "landscape"), ("landscape", "landscape"),
                                             ("youtube", "youtube"), ("cuadrado", "square")])
def test_cli_profile_alias(monkeypatch, given, expected):
    seen = {}
    monkeypatch.setattr(cli, "_project", lambda svc, name: name)
    monkeypatch.setattr(cli, "load_plan", lambda svc, p: {})
    monkeypatch.setattr(cli, "render", lambda svc, p, plan, **kw: seen.update(kw))
    a = cli.parser().parse_args(["render", "demo", "--perfil", given])
    a.f(None, a)
    assert seen["profile"] == expected


# ── text scaling ─────────────────────────────────────────────────────────────

def test_text_scale_short_side():
    assert text_scale(*PORTRAIT) == 1080 / 1080 == 1.0          # vertical: same factor as W/1080
    assert text_scale(540, 960) == 540 / 1080                     # vertical draft
    assert text_scale(*SQUARE) == 1.0
    assert text_scale(*LANDSCAPE) == 1.0                          # was 1920/1080 = 1.78
    assert text_scale(960, 540) == 0.5                            # landscape draft
    for W, H in (PORTRAIT, (540, 960), SQUARE, (720, 1280)):     # identical to the old width rule
        assert text_scale(W, H) == W / 1080


def test_text_width_capped_only_in_landscape():
    for W, H in (PORTRAIT, (540, 960), SQUARE):
        assert text_width(W, H) == W
    W, H = LANDSCAPE
    assert text_width(W, H) == pytest.approx(H * 1.4) and text_width(W, H) < W


def test_resolved_style_sizes_portrait_vs_landscape(cfg):
    styles = cfg["texts"]["styles"]
    tx = {"text": "hi", "style": "card"}
    portrait = resolve_style(tx, styles, text_scale(*PORTRAIT))
    landscape = resolve_style(tx, styles, text_scale(*LANDSCAPE))
    assert portrait["size"] == landscape["size"] == styles["card"]["size"]


def test_card_wraps_within_capped_width():
    style = {"size": 60, "max_width": 0.84}
    tx = {"text": "a fairly long title that would run across the whole wide screen easily"}
    W, H = LANDSCAPE
    title, _ = blocks(tx, style, W, "Inter-Black", H)
    assert title.width <= text_width(W, H) * 0.84
    t_old, _ = blocks(tx, style, 1080, "Inter-Black")             # H omitted: old behavior (width only)
    t_new, _ = blocks(tx, style, 1080, "Inter-Black", 1920)
    assert t_old.lines == t_new.lines


def test_text_layer_fonts_not_oversized_in_landscape(cfg, tmp_path):
    texts = [{"text": "Hello there", "start": 0.0, "end": 1.0, "style": "card"}]
    groups = [{"words": [{"word": "one", "start": 0.1, "end": 0.4}, {"word": "two", "start": 0.4, "end": 0.8}],
               "start": 0.1, "end": 0.9}]
    sizes = {}
    for W, H in (PORTRAIT, LANDSCAPE):
        boxes: list = []
        PillowTextRenderer().render_layer(texts, groups, cfg, W, H, 1.0, tmp_path / f"{W}", boxes)
        sizes[(W, H)] = {b["kind"]: b["h"] for b in boxes}
        for b in boxes:
            assert 0 <= b["x"] and b["x"] + b["w"] <= W and 0 <= b["y"] and b["y"] + b["h"] <= H
    # same pixel height in both orientations (the old width rule made landscape 1.78× taller)
    for kind in ("text", "subtitle"):
        assert sizes[LANDSCAPE][kind] == pytest.approx(sizes[PORTRAIT][kind], rel=0.05)


def test_estimated_boxes_scale_by_short_side(cfg):
    styles = cfg["texts"]["styles"]
    tx = [{"text": "hi", "style": "card", "start": 0, "end": 1}]
    p = ck.estimated_boxes(tx, styles, *PORTRAIT)[0]
    land = ck.estimated_boxes(tx, styles, *LANDSCAPE)[0]
    assert land["h"] == pytest.approx(p["h"])


# ── safe zones ───────────────────────────────────────────────────────────────

def _box(text, x, y, w, h):
    return {"text": text, "x": x, "y": y, "w": w, "h": h, "start": 0, "end": 1}


def test_safe_zone_per_orientation():
    th = ck.thresholds()
    z = ck.safe_zone(*PORTRAIT, th)
    assert z["orientation"] == "portrait" and z["rail"] == [0.35, 0.85] and z["bottom"] == 0.78
    assert ck.safe_zone(*SQUARE, th)["rail"] == [0.35, 0.85]                # square keeps the app rules
    z = ck.safe_zone(*LANDSCAPE, th)
    assert z == {"orientation": "landscape", "top": 0.05, "bottom": 0.90, "left": 0.05, "right": 0.05,
                 "rail": None}


def test_landscape_box_issues_no_tiktok_rules():
    th = ck.thresholds()
    W, H = LANDSCAPE
    boxes = [_box("low_ok", 600, 800, 700, 100),        # bottom 0.83·H: fine in landscape (> 0.78)
             _box("rail_ok", 1650, 500, 150, 80),       # right 0.94·W, middle height: no button column
             _box("too_low", 600, 920, 700, 100),       # bottom 0.94·H > 0.90
             _box("too_high", 600, 20, 700, 100),       # top 0.02·H < 0.05
             _box("left", 40, 500, 400, 80),            # left 0.02·W < 0.05
             _box("right", 1500, 500, 400, 80)]         # right 0.99·W > 0.95
    issues = {i["text"]: i["issues"] for i in ck.box_issues(boxes, W, H, th)}
    assert set(issues) == {"too_low", "too_high", "left", "right"}
    assert "bottom" in issues["too_low"][0] and "top" in issues["too_high"][0]
    assert "left" in issues["left"][0] and "right" in issues["right"][0]
    assert not any("buttons" in w for ws in issues.values() for w in ws)


def test_portrait_box_issues_unchanged_rules():
    th = ck.thresholds()
    W, H = PORTRAIT
    boxes = [_box("low", 100, 1450, 800, 120), _box("buttons", 500, 1000, 560, 100),
             _box("left_edge", 0, 600, 300, 100)]                  # no left rule in portrait
    issues = {i["text"]: i["issues"] for i in ck.box_issues(boxes, W, H, th)}
    assert set(issues) == {"low", "buttons"} and "under the buttons" in issues["buttons"][0]


def test_landscape_thresholds_default_and_override(cfg):
    z = ck.safe_zone(*LANDSCAPE, ck.thresholds(cfg.get("check")))
    assert (z["top"], z["bottom"], z["left"], z["right"]) == (0.05, 0.90, 0.05, 0.05)
    th = ck.thresholds(cfg.get("check"), {"landscape_safe_bottom": 0.95})
    assert ck.safe_zone(*LANDSCAPE, th)["bottom"] == 0.95
    assert ck.safe_zone(*PORTRAIT, th)["bottom"] == 0.78


# ── polaroid and collage ─────────────────────────────────────────────────────

def _frame(path, W=640, H=360, seed=0):
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 255, (H, W, 3), dtype=np.uint8)).save(path)
    return path


def test_polaroid_width_portrait_unchanged_landscape_fits(cfg):
    assert cm.polaroid_photo_width(*PORTRAIT, cfg) == int(1080 * 0.70)
    assert cm.polaroid_photo_width(540, 960, cfg) == int(540 * 0.70)
    w = cm.polaroid_photo_width(*LANDSCAPE, cfg)
    assert w * cm.POLAROID_HEIGHT_RATIO <= 1080 * cm.POLAROID_MAX_HEIGHT + 1


def test_polaroid_frame_inside_landscape_output(cfg, tmp_path):
    W, H = LANDSCAPE
    photo = cm.PillowComposer()._photo(Image.open(_frame(tmp_path / "f.png")).convert("RGB"), W, "caption",
                                       cfg, H)
    bbox = photo.getbbox()                                              # visible pixels (shadow included)
    assert bbox[3] - bbox[1] < H and bbox[2] - bbox[0] < W
    frames = list(cm.PillowComposer().polaroid_frames(tmp_path / "f.png", {"dur": 0.5}, W, H, 30, cfg))
    assert len(frames) == 15 and all(len(f) == W * H * 3 for f in frames)
    # resting position (after the drop): the paper frame sits inside the picture
    rest_h = photo.height * 1.035
    assert H * 0.47 - rest_h / 2 > -60 and H * 0.47 + rest_h / 2 < H + 60     # shadow margin only


def _inside(g, n, W, H):
    for k in range(n):
        cx = g["x0"] + (k % g["columns"]) * (g["cw"] + g["gap"])
        cy = g["y0"] + (k // g["columns"]) * (g["ch"] + g["gap"])
        if not (0 <= cx and cx + g["cw"] <= W and 0 <= cy and cy + g["ch"] <= H):
            return False
    return True


@pytest.mark.parametrize("n", [2, 3, 4, 6, 8, 9, 12])
def test_collage_landscape_grid_fits_with_fewer_rows(cfg, n):
    W, H = LANDSCAPE
    g = cm.collage_layout(n, W, H, {"columns": 3}, cfg)
    assert _inside(g, n, W, H)
    assert g["y0"] >= 0.10 * H - 1 and g["y0"] + g["rows"] * g["ch"] + (g["rows"] - 1) * g["gap"] <= 0.90 * H + 1
    assert g["rows"] <= g["columns"] or n < 3                   # wide grid: never taller than wide
    assert g["rows"] * g["columns"] - n < g["columns"]           # no empty row
    portrait_cols = cm.collage_layout(n, W, H, {}, cfg)["columns"]
    assert g["columns"] == portrait_cols                         # landscape ignores the portrait column count
    assert g["ch"] >= 0.25 * H or n > 9                          # cells stay reasonably big


def test_collage_landscape_prefers_full_rows(cfg):
    g = cm.collage_layout(6, *LANDSCAPE, {}, cfg)
    assert (g["columns"], g["rows"]) == (3, 2)
    g = cm.collage_layout(8, *LANDSCAPE, {}, cfg)
    assert (g["columns"], g["rows"]) == (4, 2)


def test_collage_portrait_layout_unchanged(cfg):
    g = cm.collage_layout(7, *PORTRAIT, {}, cfg)
    gap = int(1080 * 0.022)
    cw = int((1080 * 0.94 - gap * 3) / 4)
    assert (g["columns"], g["rows"], g["gap"], g["cw"], g["ch"]) == (4, 2, gap, cw, int(cw * 4 / 3))
    assert cm.collage_layout(3, *PORTRAIT, {"columns": 3}, cfg)["columns"] == 3
    assert _inside(g, 7, *PORTRAIT)


def test_collage_frames_render_in_landscape(cfg, tmp_path):
    W, H = LANDSCAPE
    pngs = [_frame(tmp_path / f"f{i}.png", seed=i) for i in range(6)]
    frames = list(cm.PillowComposer().collage_frames(pngs, pngs[0], {"dur": 0.2}, [0.0] * 6, W, H, 30, cfg))
    assert len(frames) == 6 and all(len(f) == W * H * 3 for f in frames)
