"""photo.auto_tone: gentle automatic exposure fix of still photos (synthetic images only)."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from social_video_editor.adapters.ffmpeg.photo_encoder import TONE_CHROMA_MAX, auto_tone, lightness_quartiles
from social_video_editor.domain import photos as dp


def _scene(scale: float, h: int = 240, w: int = 320, seed: int = 0) -> Image.Image:
    """A warm horizontal gradient with a blue square and light noise, times `scale` (exposure)."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, w)[None, :].repeat(h, 0)
    base = 0.25 + 0.5 * x
    img = np.stack([base, base * 0.8, base * 0.6], -1)
    img[60:160, 90:200] = [0.3, 0.6, 0.9]
    img = img * scale + rng.normal(0, 0.01, img.shape)
    return Image.fromarray(np.clip(img * 255, 0, 255).astype(np.uint8))


def _backlit(h: int = 240, w: int = 320, seed: int = 1) -> Image.Image:
    """Bright sky over a dark subject (the classic compact-camera backlit shot)."""
    rng = np.random.default_rng(seed)
    img = np.empty((h, w, 3))
    img[:] = [0.95, 0.95, 0.97]
    img[h // 3:, :] = [0.16, 0.12, 0.10]
    img[h // 4:, w // 3: 2 * w // 3] = [0.22, 0.15, 0.12]
    img = img + rng.normal(0, 0.01, img.shape)
    return Image.fromarray(np.clip(img * 255, 0, 255).astype(np.uint8))


def _arr(im) -> np.ndarray:
    return np.asarray(im).astype(np.float64)


# ── domain ───────────────────────────────────────────────────────────────────

def test_tone_curve_keeps_black_and_white_and_is_monotonic():
    x = np.linspace(0, 1, 1001)
    for lift in (0.0, 0.5, 1.0, dp.TONE_MAX_LIFT):
        y = dp.tone_curve(x, lift)
        assert y[0] == 0 and y[-1] == pytest.approx(1.0)
        assert np.all(np.diff(y) >= 0)
        assert np.all(y >= x - 1e-12) and np.all(y <= 1 + 1e-12)


def test_auto_tone_lift_rules():
    assert dp.auto_tone_lift(0.35, 0.50, 0.6) == 0            # well exposed: untouched
    assert dp.auto_tone_lift(0.45, 0.65, 1.0) == 0            # bright: never darkened here
    assert dp.auto_tone_lift(0.05, 0.10, 0.0) == 0            # off
    dark, darker = dp.auto_tone_lift(0.15, 0.25, 0.6), dp.auto_tone_lift(0.08, 0.15, 0.6)
    assert 0 < dark < darker <= dp.TONE_MAX_LIFT
    assert dp.auto_tone_lift(0.08, 0.15, 0.3) < darker        # strength scales the lift
    assert dp.auto_tone_lift(0.12, 0.60, 0.6) > 0             # backlit: bright median, dark quartile
    assert dp.local_contrast_share(0, 0.6) == 0


def test_auto_tone_setting_defaults_off():
    assert dp.auto_tone_setting({}) == 0
    assert dp.auto_tone_setting({"photo": {"auto_tone": 0.6}}) == 0.6
    assert dp.auto_tone_setting({"photo": {"auto_tone": 0.6}}, {"auto_tone": 0}) == 0
    assert dp.auto_tone_setting({"photo": {"auto_tone": 7}}) == 1.0
    assert dp.photo_crf({}) == 16 and dp.photo_crf({"render": {"photo_crf": 20}}) == 20


# ── pixels ───────────────────────────────────────────────────────────────────

def test_dark_photo_is_lifted_but_not_washed_out():
    im = _scene(0.35)
    p25, p50 = lightness_quartiles(np.asarray(im))
    out = auto_tone(im, 0.6)
    q25, q50 = lightness_quartiles(np.asarray(out))
    assert q50 > p50 + 0.1 and q25 > p25                      # clearly brighter
    assert q50 < 0.5                                          # still natural, not pushed to grey
    a, b = _arr(im), _arr(out)
    assert np.percentile(b.mean(-1), 1) < 45                  # blacks stay dark (no milky veil)
    assert (b >= 250).mean() <= (a >= 250).mean() + 0.001     # no clipping explosion


def test_well_exposed_and_bright_photos_barely_change():
    for scale in (1.0, 1.3):
        im = _scene(scale)
        assert np.abs(_arr(auto_tone(im, 0.6)) - _arr(im)).mean() < 1.0


def test_backlit_subject_lifted_sky_kept():
    im = _backlit()
    out = auto_tone(im, 0.6)
    a, b = _arr(im), _arr(out)
    h = a.shape[0]
    sky, subject = slice(0, h // 4 - 5), slice(h // 3 + 5, h)
    assert b[subject].mean() > a[subject].mean() + 15
    assert abs(b[sky].mean() - a[sky].mean()) < 4
    assert (b >= 250).mean() <= (a >= 250).mean() + 0.005


def _lab_mean(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(rgb.astype(np.float32) / 255, cv2.COLOR_RGB2LAB).reshape(-1, 3).mean(0)


def test_no_color_shift():
    im = _scene(0.35)
    a, b = _arr(im)[60:160, 90:200], _arr(auto_tone(im, 0.6))[60:160, 90:200]
    la, lb = _lab_mean(a), _lab_mean(b)
    hue = np.degrees(np.arctan2(lb[2], lb[1]) - np.arctan2(la[2], la[1]))
    assert abs((hue + 180) % 360 - 180) < 3                    # same hue (the blue square stays this blue)
    ca, cb = np.hypot(la[1], la[2]), np.hypot(lb[1], lb[2])
    assert ca - 0.5 <= cb <= ca * TONE_CHROMA_MAX + 0.5         # colorfulness kept, never boosted much
    assert lb[0] > la[0] + 10                                  # only lighter


def test_strength_zero_and_determinism():
    im = _scene(0.3)
    assert auto_tone(im, 0) is im
    assert np.array_equal(np.asarray(auto_tone(im, 0.6)), np.asarray(auto_tone(im, 0.6)))
    weak, strong = auto_tone(im, 0.3), auto_tone(im, 1.0)
    assert _arr(weak).mean() < _arr(strong).mean()


class _Recorder:
    """Stands in for FfmpegEncoder.encode_frames: keeps the mean of the frames and the crf."""

    def encode_frames(self, frames, n, W, H, fps, out, audio=None, crf=None):
        self.mean = float(np.mean([np.frombuffer(f, np.uint8).mean() for f in frames]))
        self.crf = crf
        return out


def test_photo_encoder_applies_auto_tone_and_photo_crf(tmp_path):
    from social_video_editor.adapters.ffmpeg.photo_encoder import FfmpegPhotoEncoder

    src = tmp_path / "dark.jpg"
    _scene(0.35).save(src, "JPEG", quality=95)
    results = {}
    for tone in (0, 0.6):
        rec = _Recorder()
        cfg = {"photo": {"auto_tone": tone}, "framing": {"mode": "blur"}, "render": {"photo_crf": 18}}
        FfmpegPhotoEncoder(rec).encode_photo(src, {"dur": 0.2, "motion": "zoom_in"}, cfg, 192, 108, 10,
                                             tmp_path / f"seg_{tone}.mkv")
        results[tone] = rec
    assert results[0.6].mean > results[0].mean + 15
    assert results[0].crf == 18


# ── backlit scenes: local shadow recovery ───────────────────────────────────

def _lightness(rgb) -> np.ndarray:
    import cv2
    return cv2.cvtColor(np.asarray(rgb).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)[..., 0]   # L* 0–100


def _window_backlit(h: int = 240, w: int = 320, seed: int = 2) -> Image.Image:
    """Mostly bright background (window / sky: the median is high) with a dark subject block."""
    rng = np.random.default_rng(seed)
    img = np.empty((h, w, 3))
    img[:] = [0.96, 0.96, 0.98]
    img[h * 2 // 5:, w * 5 // 16: w * 11 // 16] = [0.15, 0.11, 0.09]
    img = np.clip(img + rng.normal(0, 0.01, img.shape), 0, 1)
    return Image.fromarray((img * 255).astype(np.uint8))


def test_shadow_recovery_rules():
    assert dp.shadow_recovery(0.25, 0.0, 0.6) == 0               # dark but nothing bright: global curve only
    assert dp.shadow_recovery(0.0, 0.6, 0.6) == 0                # bright, no deep shadows
    assert dp.shadow_recovery(0.05, 0.08, 0.6) == 0              # ordinary contrast: untouched
    full = dp.shadow_recovery(0.3, 0.5, 0.6)
    assert 0.18 <= full <= 0.22 and dp.shadow_recovery(0.3, 0.5, 0) == 0
    assert dp.shadow_weight(0.0) == 0 and dp.shadow_weight(0.25) == pytest.approx(1, abs=0.01)
    assert dp.shadow_weight(0.9) < 0.01


def test_backlit_with_bright_median_gets_subject_lifted_locally():
    im = _window_backlit()
    assert lightness_quartiles(np.asarray(im))[1] > 0.9         # the global curve alone would do nothing
    out = auto_tone(im, 0.6)
    a, b = _lightness(im), _lightness(out)
    h, w = a.shape
    subject = (slice(h * 2 // 5 + 15, h), slice(w * 5 // 16 + 15, w * 11 // 16 - 15))
    edge = (slice(h * 2 // 5 + 2, h), slice(w * 5 // 16 + 1, w * 5 // 16 + 4))   # next to the window
    bright = np.ones_like(a, bool)
    bright[h * 2 // 5:, w * 5 // 16: w * 11 // 16] = False
    assert b[subject].mean() > a[subject].mean() + 10           # the subject comes out of the shadow
    assert b[edge].mean() > a[edge].mean() + 8                  # all the way to the edge (no dark rim)
    assert np.abs(b - a)[bright].max() < 3                      # the window / sky stays as it was (no halo)
    assert b[subject].std() < a[subject].std() * 1.3 + 0.2      # noise not boosted


def test_well_exposed_scene_with_sky_and_some_shadow_unchanged():
    im = _scene(1.0)
    arr = np.asarray(im).copy()
    arr[:20] = 245                                              # a bit of bright sky
    arr[-12:, :40] = 30                                         # and a small shadow
    im = Image.fromarray(arr)
    assert np.abs(_arr(auto_tone(im, 0.6)) - _arr(im)).mean() < 1.0
