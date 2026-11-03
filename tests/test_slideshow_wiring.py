"""Photo slideshow wiring: report (photo folders, playlists, summarized photo shots), contact sheets of
photos, unlimited durations in the selection, Spanish aliases of the new keys, photo segment settings.
Synthetic data only (tmp folders)."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from social_video_editor.adapters.files import legacy_es
from social_video_editor.application.thumbs import contact_sheets
from social_video_editor.cli import main
from social_video_editor.container import build_services
from social_video_editor.domain import photos as dp
from social_video_editor.domain.selection import duration_limit, propose


def _photos(folder: Path, n: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = folder / f"img_{i:03d}.jpg"
        Image.new("RGB", (64, 48), (40 + i * 7 % 200, 90, 140)).save(p, "JPEG")
        out.append(p)
    return out


def _slideshow_project(ws: Path, n_photos: int = 120) -> Path:
    photos = _photos(ws / "album", n_photos)
    d = ws / "projects" / "trip"
    (d / "cache").mkdir(parents=True)
    (ws / "music" / "mix").mkdir(parents=True)
    for name, dur in (("Track One.mp3", 200.0), ("Track Two.mp3", 160.0), ("Track Three.mp3", None)):
        (ws / "music" / "mix" / name).write_bytes(b"")
        if dur:
            (d / "cache" / f"music_{Path(name).stem.replace(' ', '_')}.json").write_text(
                json.dumps({"duration": dur, "tempo": 100.0, "beats": []}), encoding="utf-8")
    (d / "project.yaml").write_text(
        f"name: trip\nrecipe: slideshow\nsources:\n  - {ws / 'album'}\n"
        "settings:\n  music: {playlist: [mix]}\n", encoding="utf-8")
    shots = [{"type": "photo", "source": str(p), "duration": 4.0 + (i % 3) * 0.5,
              "transition": "fade" if i else "cut"} for i, p in enumerate(photos)]
    (d / "plan.json").write_text(json.dumps({"shots": shots, "texts": []}), encoding="utf-8")
    return d


def test_report_summarizes_photo_folder_playlist_and_photo_shots(workspace, capsys):
    _slideshow_project(workspace)
    main(["report", "trip"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) <= 50, out
    assert "SOURCES 1 · 120 photos" in out and "0 album/ 120 photos" in out
    assert "not analyzed yet" not in out
    assert "PLAN 120 shots" in out and "120 photo shots · 4.0–5.0 s each" in out
    assert "photo<" not in out and "img_0" not in out           # no line per photo
    assert "MUSIC playlist of 3 songs · 6.0 min (2 analyzed) · AutoMix on" in out

    main(["report", "trip", "--json"])
    r = json.loads(capsys.readouterr().out)
    assert r["sources"][0]["folder"] and r["sources"][0]["photos"] == 120
    assert r["plan"]["photos"]["count"] == 120 and r["plan"]["timeline"] == []
    assert r["music"]["playlist"] == {"songs": 3, "minutes": 6.0, "analyzed": 2, "automix": True}


def test_report_playlist_automix_off(workspace, capsys):
    d = _slideshow_project(workspace, 3)
    (d / "project.yaml").write_text(
        f"name: trip\nrecipe: slideshow\nsources:\n  - {workspace / 'album'}\n"
        "settings:\n  music: {playlist: [mix], automix: false}\n", encoding="utf-8")
    main(["report", "trip"])
    assert "AutoMix off" in capsys.readouterr().out


def test_thumbs_of_photo_project_make_photo_sheets(workspace):
    _slideshow_project(workspace, 35)
    svc = build_services()
    project = svc.projects.load(svc.projects.resolve("trip"))
    paths = contact_sheets(svc, project)
    assert [p.name for p in paths] == ["photos_sheet_01.jpg", "photos_sheet_02.jpg"]   # 30 per sheet
    assert all(p.exists() for p in paths)
    one = contact_sheets(svc, project, source=3)
    assert len(one) == 1 and one[0].name.endswith("_photo_sheet_01.jpg")


def test_unlimited_duration_does_not_crash_selection(cfg):
    assert duration_limit(None) == duration_limit(0) == duration_limit("x") == float("inf")
    assert duration_limit(30) == 30
    an = {"duration": 20.0, "samples": [{"t": t / 4, "brightness": 0.5, "sharpness": 50.0, "motion": 1.0 + t % 5,
                                         "color": 1.0} for t in range(80)],
          "scenes": [[0, 20]], "audio": None, "words": [], "voice": {"segments": [], "ratio": 0.0}}
    for target, mx in ((0, 0), (None, None)):
        c = {**cfg, "duration": {"target": target, "max": mx}}
        assert propose([an], c)["shots"]                            # no TypeError, picks moments


def test_spanish_aliases_of_new_keys():
    cfg = legacy_es.config({"presentacion": True, "foto": {"tono_auto": 0.5, "ajustar_a_musica": True,
                                                            "transicion": "fundido"},
                            "musica": {"lista": ["canciones"]}, "color": {"calidez": 0.2, "vineta": 0.1}})
    assert cfg == {"slideshow": True, "photo": {"auto_tone": 0.5, "fit_to_music": True, "transition": "fade"},
                   "music": {"playlist": ["canciones"]}, "color": {"warmth": 0.2, "vignette": 0.1}}
    shot = legacy_es.plan({"tomas": [{"tipo": "photo", "fuente": 0, "duracion": 4, "movimiento": "zoom_in"}]})
    assert shot["shots"][0] == {"type": "photo", "source": 0, "duration": 4, "motion": "zoom_in"}


def test_slideshow_recipe_turns_auto_tone_on_base_keeps_it_off(recipes):
    assert dp.auto_tone_setting(recipes.load("slideshow")) == 0.6
    base = recipes.load("base")
    assert dp.auto_tone_setting(base) == 0 and dp.photo_crf(base) == 16
