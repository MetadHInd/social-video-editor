"""The real CLIP adapter on synthetic images (skipped when the model is not installed/downloaded).

Never downloads: it only uses models already in the Hugging Face cache.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from social_video_editor.adapters.clip.detector import DEFAULT_MODEL, ClipVisualDetector, hub_cache
from social_video_editor.domain import products as dp

HUB = hub_cache()       # read at import: the autouse fixture moves XDG_CACHE_HOME into a temporary folder
pytestmark = pytest.mark.vision


@pytest.fixture(scope="module")
def detector():
    det = ClipVisualDetector(cache_folder=str(HUB))
    reason = det.ready({})
    if reason:
        pytest.skip(f"vision model not available offline: {reason}")
    return det


def red_circle(w=180, h=320) -> Image.Image:
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).ellipse((w * 0.15, h / 2 - w * 0.35, w * 0.85, h / 2 + w * 0.35), fill=(220, 20, 30))
    return im


def gray_plastic(w=180, h=320, seed=0) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = 150 + 25 * np.sin(np.linspace(0, 9, w))[None, :] + 12 * rng.standard_normal((h, w))
    return Image.fromarray(np.clip(np.repeat(base[..., None], 3, axis=2), 0, 255).astype(np.uint8))


def test_texts_and_images_share_one_space(detector):
    settings = {}
    images = detector._encode(DEFAULT_MODEL, [red_circle(), gray_plastic()], settings)
    texts = detector.text_embeddings(["un círculo rojo", "una bolsa de plástico gris"], settings)
    assert images.shape[1] == texts.shape[1] and np.allclose(np.linalg.norm(texts, axis=1), 1, atol=1e-4)
    sim = images @ texts.T
    assert sim[0, 0] > sim[0, 1] and sim[1, 1] > sim[1, 0]           # Spanish text ↔ the right image
    again = detector.text_embeddings(["un círculo rojo"], settings)  # cached in memory
    assert np.allclose(again[0], texts[0])


def test_frame_embeddings_are_cached_per_source(detector, tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    video = tmp_path / "clip_proxy.mp4"
    out = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (180, 320))
    for im in [red_circle()] * 20 + [gray_plastic(seed=k) for k in range(20)]:
        out.write(cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR))
    out.release()
    t, emb = detector.frame_embeddings(str(video), tmp_path, "clip", {"fps": 2})
    assert len(t) == len(emb) >= 7 and np.all(np.diff(t) > 0)
    assert (tmp_path / "clip_clip.npy").exists() and (tmp_path / "clip_clip.json").exists()
    pos = detector.text_embeddings(["un objeto rojo y redondo"], {})
    neg = detector.text_embeddings(["una bolsa de plástico gris"], {})
    contrast = dp.prompt_contrast(emb, pos, neg)
    assert contrast[t < 1.8].mean() > contrast[t > 2.2].mean()
    monkeypatch.setattr(detector, "_embed_video", lambda *a: pytest.fail("should come from the cache"))
    t2, emb2 = detector.frame_embeddings(str(video), tmp_path, "clip", {"fps": 2})
    assert np.allclose(t2, t) and np.allclose(emb2, emb, atol=2e-3)       # stored as float16
