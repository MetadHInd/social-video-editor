"""Still photos as sources: EXIF capture time and orientation, folder sources, Ken Burns, PhotoShot,
slideshow plan and photo segments (synthetic images generated with Pillow in tmp folders)."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg.photo_encoder import FfmpegPhotoEncoder, load_photo
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe, image_info, scan_images
from social_video_editor.adapters.system.tools import FFPROBE
from social_video_editor.application.analyze import expand_sources, prepare_sources
from social_video_editor.application.plan import make_plan
from social_video_editor.container import build_services
from social_video_editor.domain import photos as dp
from social_video_editor.domain.models import PlanError
from social_video_editor.domain.pieces import SHOT_TYPES, PhotoShot, Sequence, make_shot
from social_video_editor.domain.timeline import compute_timeline

TAG_ORIENTATION, TAG_DATETIME, TAG_ORIGINAL, IFD_EXIF = 274, 306, 36867, 0x8769


def make_photo(path: Path, size=(320, 240), color=(200, 60, 40), original: str | None = None,
               date_time: str | None = None, orientation: int | None = None, mtime: float | None = None) -> Path:
    """A synthetic photo with optional EXIF tags (and modification time)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    im.paste((20, 220, 30), (0, 0, size[0] // 4, size[1] // 4))        # green mark at the top-left corner
    exif = Image.Exif()
    if orientation:
        exif[TAG_ORIENTATION] = orientation
    if date_time:
        exif[TAG_DATETIME] = date_time
    if original:
        exif.get_ifd(IFD_EXIF)[TAG_ORIGINAL] = original
    if path.suffix.lower() in (".jpg", ".jpeg"):
        im.save(path, "JPEG", quality=92, exif=exif)
    else:
        im.save(path, exif=exif)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


# ── domain ───────────────────────────────────────────────────────────────────

def test_parse_exif_datetime():
    assert dp.parse_exif_datetime("2019:05:03 10:20:30") == "2019-05-03T10:20:30"
    assert dp.parse_exif_datetime(b"2019:05:03 10:20:30\x00") == "2019-05-03T10:20:30"
    assert dp.parse_exif_datetime("2019-05-03 10:20:30") == "2019-05-03T10:20:30"
    assert dp.parse_exif_datetime("0000:00:00 00:00:00") is None
    assert dp.parse_exif_datetime("") is None and dp.parse_exif_datetime(None) is None


def test_capture_time_fallbacks():
    assert dp.capture_time("2019:05:03 10:20:30", "2020:01:01 00:00:00", 0) == ("2019-05-03T10:20:30", "exif_original")
    assert dp.capture_time(None, "2020:01:01 08:00:00", 0) == ("2020-01-01T08:00:00", "exif")
    assert dp.capture_time("garbage", None, 0)[1] == "mtime"
    ts = datetime(2018, 7, 1, 12, 0, 0).timestamp()
    assert dp.capture_time(None, None, ts) == ("2018-07-01T12:00:00", "mtime")
    assert dp.capture_time() == (None, None)


def test_chronological_tie_break_and_missing_dates():
    photos = [{"path": "/b/IMG_2.jpg", "taken": "2020-01-01T10:00:00"},
              {"path": "/a/IMG_9.jpg", "taken": None},
              {"path": "/a/IMG_3.jpg", "taken": "2020-01-01T10:00:00"},
              {"path": "/z/IMG_1.jpg", "taken": "2019-12-31T23:59:59"}]
    assert [p["path"] for p in dp.chronological(photos)] == ["/z/IMG_1.jpg", "/b/IMG_2.jpg", "/a/IMG_3.jpg",
                                                             "/a/IMG_9.jpg"]


def test_ken_burns_stays_inside_and_is_gentle():
    for motion in dp.ALL_MOTIONS:
        prev = None
        for k in range(101):
            z, cx, cy = dp.ken_burns(motion, k / 100, 0.06)
            assert 1.0 <= z <= 1.06 + 1e-9
            half = 0.5 / z
            assert half - 1e-9 <= cx <= 1 - half + 1e-9 and half - 1e-9 <= cy <= 1 - half + 1e-9
            if prev is not None:                       # smooth: tiny steps, never backwards
                assert abs(z - prev[0]) < 0.002 and abs(cx - prev[1]) < 0.002
            prev = (z, cx, cy)
    assert dp.ken_burns("zoom_in", 0)[0] == 1.0 and dp.ken_burns("zoom_in", 1)[0] == pytest.approx(1.06)
    assert dp.ken_burns("zoom_out", 0)[0] == pytest.approx(1.06) and dp.ken_burns("zoom_out", 1)[0] == 1.0
    assert dp.ken_burns("pan_right", 0)[1] < dp.ken_burns("pan_right", 1)[1]
    assert dp.ken_burns("pan_left", 0)[1] > dp.ken_burns("pan_left", 1)[1]
    assert dp.ken_burns("zoom_in", 1, amount=5)[0] == pytest.approx(1 + dp.MAX_MOTION_AMOUNT)


def test_assign_motions_alternates_deterministically():
    land = [{"width": 4000, "height": 3000}] * 8
    m = dp.assign_motions(land, 1920, 1080)
    assert m == dp.assign_motions(land, 1920, 1080)
    assert all(a != b for a, b in zip(m, m[1:]))
    assert set(m) == set(dp.MOTIONS)
    portrait = [{"width": 3000, "height": 4000}] * 4      # shown over a blurred background → zooms only
    assert dp.assign_motions(portrait, 1920, 1080) == ["zoom_in", "zoom_out", "zoom_in", "zoom_out"]


def test_photo_shot_registered_and_fits_durations(cfg):
    assert SHOT_TYPES["photo"] is PhotoShot
    plan = {"shots": [{"type": "photo", "source": "/x/a.jpg", "duration": 4.5, "motion": "zoom_in"},
                      {"type": "photo", "source": 1, "transition": "fade"}]}
    seq = Sequence.from_plan(plan)
    a, b = seq.shots()
    assert a.base_duration() == 4.5 and b.base_duration() == dp.PHOTO_SECONDS
    assert a.source is None and a.ref == "/x/a.jpg" and b.motion == dp.default_motion(1)
    assert seq.dependencies() == set() and a.source_window() is None
    tl = compute_timeline(seq, cfg)
    assert tl[1].overlap > 0 and tl.total == pytest.approx(9.0 - tl[1].overlap, abs=1e-3)
    a.fit_to_duration(3.2, None, [])
    assert plan["shots"][0]["duration"] == 3.2           # written into the plan (editable)
    assert a.shift_end(0.5, cfg, []) and plan["shots"][0]["duration"] == 3.7
    assert not a.shift_end(-10, cfg, [])
    assert a.summary()["type"] == "photo" and "a.jpg" in a.describe()
    with pytest.raises(PlanError):
        make_shot({"type": "photo", "source": "/x/a.jpg", "motion": "spin"}, 0).motion
    with pytest.raises(PlanError):
        make_shot({"type": "photo"}, 0).ref


def test_resolve_photo_ref():
    infos = [{"id": "a_1", "path": "/p/a.jpg"}, {"id": "b_2", "path": "/p/b.jpg"}]
    assert dp.resolve_photo_ref(1, infos) == "/p/b.jpg"
    assert dp.resolve_photo_ref("0", infos) == "/p/a.jpg"
    assert dp.resolve_photo_ref("b_2", infos) == "/p/b.jpg"
    assert dp.resolve_photo_ref("/p/a.jpg", infos) == "/p/a.jpg"
    assert dp.resolve_photo_ref("/elsewhere/c.png", infos) == "/elsewhere/c.png"
    with pytest.raises(ValueError):
        dp.resolve_photo_ref(5, infos)


def test_photo_plan(cfg):
    cfg = {**cfg, "output": {**cfg["output"], "width": 1920, "height": 1080}, "photo": {"duration": 3}}
    infos = [{"path": "/p/late.jpg", "image": True, "width": 400, "height": 300, "taken": "2021-01-01T00:00:00"},
             {"path": "/p/early.jpg", "image": True, "width": 400, "height": 300, "taken": "2001-01-01T00:00:00"},
             {"path": "/p/mid.jpg", "image": True, "width": 300, "height": 400, "taken": "2011-01-01T00:00:00"}]
    plan = dp.photo_plan(infos, cfg)
    shots = plan["shots"]
    assert [s["source"] for s in shots] == ["/p/early.jpg", "/p/mid.jpg", "/p/late.jpg"]
    assert all(s["type"] == "photo" and s["duration"] == 3 for s in shots)
    assert [s["transition"] for s in shots] == ["cut", "fade", "fade"]
    assert shots[1]["motion"] in dp.ZOOM_MOTIONS and shots[0]["note"] == "2001-01-01 00:00:00"
    assert dp.is_slideshow({}, infos) and not dp.is_slideshow({}, infos + [{"path": "v.mov"}])
    assert dp.is_slideshow({"slideshow": True}, [{"path": "v.mov"}])
    compute_timeline(plan, cfg)                         # a valid plan


def test_enhance_target_and_name():
    assert dp.enhance_target(640, 480, 1080) == ("upscale", 1080)
    assert dp.enhance_target(4000, 3000, 1080) == ("downscale", 1350)
    assert dp.enhance_target(1440, 1200, 1080) == ("keep", 1200)
    assert dp.enhanced_name("/p/a.jpg", 1) == dp.enhanced_name("/p/a.jpg", 1) != dp.enhanced_name("/p/a.jpg", 2)


# ── probe, orientation, folders ──────────────────────────────────────────────

def test_probe_image_with_exif_dates(tmp_path):
    a = make_photo(tmp_path / "a.jpg", original="2015:06:01 09:00:00", date_time="2016:01:01 00:00:00")
    b = make_photo(tmp_path / "b.jpg", date_time="2014:02:02 10:00:00")
    ts = datetime(2010, 3, 3, 8, 0, 0).timestamp()
    c = make_photo(tmp_path / "c.png", mtime=ts)
    probe = FfprobeMediaProbe()
    ia, ib, ic = probe.probe(a), probe.probe(b), probe.probe(c)
    assert ia["image"] and ia["duration"] == 0.0 and not ia["has_audio"] and ia["audio_index"] is None
    assert (ia["width"], ia["height"]) == (320, 240)
    assert (ia["taken"], ia["taken_from"]) == ("2015-06-01T09:00:00", "exif_original")
    assert (ib["taken"], ib["taken_from"]) == ("2014-02-02T10:00:00", "exif")
    assert (ic["taken"], ic["taken_from"]) == ("2010-03-03T08:00:00", "mtime")
    assert probe.duration(a) == 0.0
    assert [Path(p["path"]).name for p in dp.chronological([ia, ib, ic])] == ["c.png", "b.jpg", "a.jpg"]


@pytest.mark.parametrize("orientation", [6, 8])
def test_orientation_rotates_size_and_pixels(tmp_path, orientation):
    p = make_photo(tmp_path / f"o{orientation}.jpg", size=(400, 200), orientation=orientation)
    info = image_info(p)
    assert info["orientation"] == orientation and (info["width"], info["height"]) == (200, 400)
    img = load_photo(p, 1000)
    assert img.size == (200, 400)
    px = np.asarray(img)
    # the green mark (top-left of the stored image) ends top-right (6 = 90° clockwise) or bottom-left (8)
    corner = px[5, -5] if orientation == 6 else px[-5, 5]
    assert corner[1] > 150 and corner[0] < 100


def test_folder_sources_scanned_recursively(tmp_path, cfg):
    root = tmp_path / "album"
    make_photo(root / "2001" / "x.jpg")
    make_photo(root / "a.png")
    make_photo(root / "2001" / "deeper" / "y.JPEG")
    (root / "notes.txt").write_text("not a photo")
    make_photo(root / ".hidden" / "z.jpg")
    found = scan_images(root)
    assert [Path(f).relative_to(root).as_posix() for f in found] == ["2001/deeper/y.JPEG", "2001/x.jpg", "a.png"]

    svc = build_services()
    single = make_photo(tmp_path / "single.jpg")
    expanded = expand_sources(svc, [{"path": str(root)}, {"path": str(single)}])
    assert [Path(s["path"]).name for s in expanded] == ["y.JPEG", "x.jpg", "a.png", "single.jpg"]
    assert expanded[0]["folder"] == str(root)


def _photo_project(svc, tmp_path, name="album_project", settings=None, n=3):
    root = tmp_path / "photos"
    for k in range(n):                                   # file names in reverse chronological order
        make_photo(root / f"p{k}.jpg", size=(320, 240) if k != 1 else (240, 320), color=(40 * k, 80, 160),
                   original=f"20{20 - k:02d}:01:01 12:00:00")
    yaml_path = svc.projects.create(name, "base", [str(root)])
    if settings:
        from social_video_editor.adapters.files.jsonio import read_yaml, write_yaml
        data = read_yaml(yaml_path)
        data["settings"] = settings
        write_yaml(yaml_path, data)
    return svc.projects.load(yaml_path.parent)


def test_prepare_sources_skips_video_analysis_for_photos(tmp_path):
    svc = build_services()
    project = _photo_project(svc, tmp_path)
    infos, analyses = prepare_sources(svc, project)
    assert len(infos) == 3 and all(i["image"] and i["proxy"] is None and i["wav"] is None for i in infos)
    assert all(a["samples"] == [] and a["words"] == [] and a["image"] for a in analyses)
    assert not list(project.cache.glob("*_proxy.mp4")) and not list(project.cache.glob("*_analysis.json"))
    assert prepare_sources(svc, project)[0] == infos      # cached media info


def test_plan_proposes_one_photo_shot_per_photo(tmp_path):
    svc = build_services()
    project = _photo_project(svc, tmp_path, settings={"output": {"width": 1920, "height": 1080}})
    make_plan(svc, project)
    plan = json.loads(project.plan_path.read_text())
    names = [Path(s["source"]).name for s in plan["shots"]]
    assert names == ["p2.jpg", "p1.jpg", "p0.jpg"]           # chronological, not by name
    assert all(s["type"] == "photo" and s["duration"] == 4.5 for s in plan["shots"])
    assert plan["shots"][1]["motion"] in dp.ZOOM_MOTIONS     # the portrait photo only zooms


# ── rendering (FFmpeg) ───────────────────────────────────────────────────────

def _ffprobe(path: Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        "-count_frames", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@pytest.mark.integration
@requires_ffmpeg
@pytest.mark.parametrize("motion,size", [("pan_left", (320, 240)), ("zoom_in", (180, 320))])
def test_photo_segment_duration_and_size(tmp_path, cfg, motion, size):
    p = make_photo(tmp_path / "s.jpg", size=size, orientation=6)
    cfg = {**cfg, "color": {**cfg["color"], "warmth": 0.4, "vignette": 0.5}}
    out = FfmpegPhotoEncoder().encode_photo(p, {"dur": 1.5, "motion": motion}, cfg, 320, 180, 30,
                                            tmp_path / "seg.mkv")
    d = _ffprobe(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next(s for s in d["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["pix_fmt"]) == (320, 180, "yuv420p")
    assert int(v["nb_read_frames"]) == 45 and float(d["format"]["duration"]) == pytest.approx(1.5, abs=0.04)
    assert a["sample_rate"] == "48000" and a["channels"] == 2 and a["codec_name"] == "pcm_s16le"


@pytest.mark.integration
@requires_ffmpeg
def test_render_photo_slideshow(tmp_path):
    svc = build_services()
    project = _photo_project(svc, tmp_path, settings={
        "output": {"width": 320, "height": 180, "fps": 24}, "music": {"file": "none"},
        "subtitles": {"auto": False}, "export": {"cover": False}, "photo": {"duration": 1.2}})
    make_plan(svc, project)
    from social_video_editor.application.render import render
    from social_video_editor.application.view import load_plan
    final = render(svc, project, load_plan(svc, project))
    d = _ffprobe(final)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (320, 180)
    assert float(d["format"]["duration"]) == pytest.approx(3 * 1.2 - 2 * 0.35, abs=0.1)
    assert len(list((project.cache / "segments").glob("photo_*.mkv"))) == 3
    summary = json.loads((final.parent / "summary.json").read_text())
    assert [s["type"] for s in summary["shot_details"]] == ["photo"] * 3
