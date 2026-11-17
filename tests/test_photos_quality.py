"""Slideshow photos: near-duplicates (the sharper one is kept) and blurred shots left out, both off by
default. Synthetic pictures only: 1/f noise looks like a natural scene to these measures."""
from __future__ import annotations

import io
import json
import os

import numpy as np
import pytest
from PIL import Image, ImageEnhance, ImageFilter

from social_video_editor.cli import main
from social_video_editor.domain import photos as dp

W, H = 640, 480


def scene(seed: int, beta: float = 1.0, amp: float = 50.0) -> Image.Image:
    r = np.random.default_rng(seed)
    f = np.sqrt(np.fft.fftfreq(H)[:, None] ** 2 + np.fft.fftfreq(W)[None, :] ** 2)
    f[0, 0] = 1
    out = []
    for _ in range(3):
        x = np.real(np.fft.ifft2((r.normal(size=(H, W)) + 1j * r.normal(size=(H, W))) / f ** beta))
        out.append(np.clip(128 + amp * (x - x.mean()) / x.std(), 0, 255))
    return Image.fromarray(np.stack(out, -1).astype("uint8"))


def reframed(im: Image.Image, share: float) -> Image.Image:
    dx, dy = int(W * share), int(H * share)
    return im.crop((dx, dy, W, H)).resize((W, H), Image.BICUBIC)


def bgr(im: Image.Image) -> np.ndarray:
    b = io.BytesIO()
    im.save(b, "JPEG", quality=88)
    b.seek(0)
    return np.asarray(Image.open(b).convert("RGB"))[..., ::-1]


@pytest.fixture(scope="module")
def pictures() -> dict:
    a = scene(1)
    return {"a": a, "a_again": ImageEnhance.Brightness(reframed(a, 0.02)).enhance(1.1),
            "a_soft": reframed(a, 0.01).filter(ImageFilter.GaussianBlur(1.2)), "b": scene(2), "c": scene(3),
            "blurred": scene(4).filter(ImageFilter.GaussianBlur(3)), "fog": scene(5, beta=1.6, amp=15)}


def test_measures_tell_sharp_blurred_and_the_same_scene(pictures):
    m = {k: dp.measure_photo(bgr(v)) for k, v in pictures.items()}
    assert m["a"]["sharpness"] > 500 and m["a"]["detail"] > 0.8
    assert m["blurred"]["sharpness"] < 60 and m["blurred"]["detail"] < 0.3            # lost its finest detail
    assert m["fog"]["sharpness"] < 60 and m["fog"]["detail"] > dp.BLUR_DETAIL           # smooth, but not blurred
    diff = dp.signature_difference
    assert diff(m["a"]["signature"], m["a"]["signature"]) == 0
    assert diff(m["a"]["signature"], m["a_again"]["signature"]) < 0.12                  # reframed, brighter
    assert diff(m["a"]["signature"], m["a_soft"]["signature"]) < 0.12
    assert min(diff(m[x]["signature"], m[y]["signature"]) for x, y in (("a", "b"), ("a", "c"), ("b", "c"))) > 0.35
    assert diff("ab", "abcd") == 1.0
    assert len(bytes.fromhex(m["a"]["signature"])) * 8 == 2 * dp.HASH_SIZE * dp.HASH_SIZE


def _info(name: str, t: str | None) -> dict:
    return {"path": f"/album/{name}.jpg", "image": True, "taken": t}


def test_skip_photos_rules():
    s = {**dp.QUALITY, "skip_duplicates": True, "skip_blurred": True}
    sig = {"x": "00" * 32, "x2": "01" + "00" * 31, "y": "ff" * 32, "z": "0f" * 32}
    photos = [_info("1", "2020-01-01T10:00:00"), _info("2", "2020-01-01T10:00:05"),     # same scene, 5 s apart
              _info("3", "2020-01-01T10:00:09"),                                       # …and again
              _info("4", "2020-01-01T10:05:00"),                                       # same picture, 5 min later
              _info("5", "2020-01-01T11:00:00"), _info("6", "2020-01-01T11:00:10"),     # blurred / unread
              _info("7", None)]
    measures = {"/album/1.jpg": {"sharpness": 400, "detail": 1.0, "signature": sig["x"]},
                "/album/2.jpg": {"sharpness": 900, "detail": 1.0, "signature": sig["x2"]},
                "/album/3.jpg": {"sharpness": 300, "detail": 1.0, "signature": sig["x"]},
                "/album/4.jpg": {"sharpness": 500, "detail": 1.0, "signature": sig["x"]},
                "/album/5.jpg": {"sharpness": 20, "detail": 0.2, "signature": sig["y"]},
                "/album/7.jpg": {"sharpness": 30, "detail": 0.95, "signature": sig["z"]}}   # smooth, sharp
    kept, skipped = dp.skip_photos(photos, measures, s)
    assert [p["path"][-5] for p in kept] == ["2", "4", "6", "7"]
    assert [(x["source"][-5], x["reason"]) for x in skipped] == [("1", "duplicate"), ("3", "duplicate"), ("5", "blurred")]
    assert skipped[0]["kept"] == "/album/2.jpg" and "sharper (400 < 900)" in skipped[0]["why"]
    assert "blurred: sharpness 20 under 60" in skipped[2]["why"]
    # off (the default): nothing changes
    assert dp.skip_photos(photos, measures, dp.QUALITY) == (dp.chronological(photos), [])
    # never every photo
    alone = {"/album/5.jpg": measures["/album/5.jpg"]}
    assert dp.skip_photos([photos[4]], alone, s) == ([photos[4]], [])
    assert dp.quality_settings({"photo": {"skip_blurred": True, "min_sharpness": 30}})["min_sharpness"] == 30.0


def _album(ws, pictures) -> tuple:
    album = ws / "album"
    album.mkdir()
    t0 = 1_600_000_000
    order = [("01_a", "a", 0), ("02_a_again", "a_again", 4), ("03_a_soft", "a_soft", 8), ("04_b", "b", 600),
             ("05_blurred", "blurred", 1200), ("06_c", "c", 1800), ("07_fog", "fog", 2400)]
    for name, key, dt in order:
        p = album / f"{name}.jpg"
        pictures[key].save(p, "JPEG", quality=90)
        os.utime(p, (t0 + dt, t0 + dt))                 # no EXIF: the capture time is the file's date
    d = ws / "projects" / "trip"
    d.mkdir(parents=True)
    return album, d


def _write(d, album, photo: dict | None):
    settings = {"photo": photo} if photo is not None else {}
    (d / "project.yaml").write_text(json.dumps({"name": "trip", "recipe": "slideshow", "sources": [str(album)],
                                                "settings": settings}), encoding="utf-8")


def test_plan_leaves_out_duplicates_and_blurred_photos_and_reports_them(workspace, capsys, pictures):
    album, d = _album(workspace, pictures)
    _write(d, album, None)
    main(["plan", "trip"])
    default = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    assert len(default["shots"]) == 7 and "skipped_photos" not in default
    _write(d, album, {"skip_duplicates": False, "skip_blurred": False})
    main(["plan", "trip", "--force"])
    assert json.loads((d / "plan.json").read_text(encoding="utf-8")) == default        # off: the same plan
    assert not (d / "cache" / "photo_quality.json").exists()                           # and nothing measured

    _write(d, album, {"omitir_duplicadas": True, "omitir_borrosas": True})             # Spanish keys
    main(["planear", "trip", "--forzar"])
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    names = [os.path.basename(s["source"]) for s in plan["shots"]]
    assert names == ["01_a.jpg", "04_b.jpg", "06_c.jpg", "07_fog.jpg"]                  # the sharpest of the three a's
    skipped = {os.path.basename(x["source"]): x for x in plan["skipped_photos"]}
    assert {k: x["reason"] for k, x in skipped.items()} == {
        "02_a_again.jpg": "duplicate", "03_a_soft.jpg": "duplicate", "05_blurred.jpg": "blurred"}
    assert skipped["03_a_soft.jpg"]["kept"].endswith("01_a.jpg")
    assert plan["shots"][0]["transition"] == "cut" and plan["shots"][1]["transition"] == "fade"
    cache = json.loads((d / "cache" / "photo_quality.json").read_text(encoding="utf-8"))
    assert len(cache) == 7

    capsys.readouterr()
    main(["report", "trip"])
    out = capsys.readouterr().out
    assert "3 photos left out: 2 near-duplicates (the sharper one is shown), 1 blurred" in out
    assert "02_a_again.jpg: same scene as 01_a.jpg, which is sharper" in out and "05_blurred.jpg: blurred" in out
