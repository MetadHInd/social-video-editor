"""Playlists (music.playlist), photo durations fitted to a mix, per-transition durations."""
from __future__ import annotations

import numpy as np
import pytest

from social_video_editor.adapters.files.projects import YamlProjectRepository
from social_video_editor.domain import automix
from social_video_editor.domain.models import UserError
from social_video_editor.domain.slideshow_timing import change_times, fit_photos_to_music, fit_plan_to_music
from social_video_editor.domain.timeline import compute_timeline
from social_video_editor.domain.transitions import transition_duration

from test_automix import fake_song


# ── music.playlist ───────────────────────────────────────────────────────────

def test_find_playlist_files_and_folders(workspace, tmp_path, recipes):
    music = workspace / "music"
    (music / "set").mkdir(parents=True)
    for n in ("b.mp3", "a.wav", "notes.txt"):
        (music / "set" / n).write_bytes(b"x")
    (music / "one.flac").write_bytes(b"x")
    project = workspace / "projects" / "p"
    project.mkdir(parents=True)
    (project / "local.m4a").write_bytes(b"x")
    repo = YamlProjectRepository(recipes)
    got = repo.find_playlist({"playlist": ["one.flac", "set", "local.m4a"]}, project)
    assert [p.name for p in got] == ["one.flac", "a.wav", "b.mp3", "local.m4a"]
    assert [p.name for p in repo.find_playlist({"playlist": str(music / "set")}, project)] == ["a.wav", "b.mp3"]
    assert repo.find_playlist({"playlist": None}, project) == []
    assert repo.find_playlist({"playlist": ["one.flac"], "file": "none"}, project) == []
    # music.file keeps working as before
    assert repo.find_music({"file": "music/one.flac"}, project).name == "one.flac"
    with pytest.raises(UserError):
        repo.find_playlist({"playlist": ["missing.mp3"]}, project)


# ── photo durations fitted to the music ──────────────────────────────────────

def _mix(*specs) -> dict:
    songs = [fake_song(f"s{i}", bpm, dur=dur, onset_rate=rate) for i, (bpm, dur, rate) in enumerate(specs)]
    plan = automix.plan_mix(songs, automix.settings({}))
    return automix.mix_timeline(plan, songs)


def _on_grid(times, grid, tol=1e-3) -> bool:
    g = np.asarray(grid)
    return all(np.min(np.abs(g - t)) <= tol for t in times)


@pytest.mark.parametrize("n_photos", [54, 60, 66])
def test_fit_photos_invariants(n_photos):
    tl = _mix((128, 100.0, 6.0), (80, 110.0, 1.0), (124, 90.0, 5.0))
    durs = fit_photos_to_music(n_photos, tl, 3.5, 5.5, 1.0)
    assert len(durs) == n_photos
    assert sum(durs) == pytest.approx(tl["duration"], abs=1e-3)               # the video ends with the music
    assert min(durs) >= 3.5 - 1e-6 and max(durs) <= 5.5 + 1e-6
    changes = change_times(durs)
    assert _on_grid(changes[:-1], tl["beats"])                                # every change on a beat
    for b in tl["boundaries"]:                                                # song changes = photo changes
        assert np.min(np.abs(np.asarray(changes) - b)) < 1e-3
    assert durs == fit_photos_to_music(n_photos, tl, 3.5, 5.5, 1.0)          # deterministic


def test_fit_photos_energetic_songs_get_shorter_photos():
    tl = _mix((128, 120.0, 8.0), (96, 120.0, 0.5))
    durs = fit_photos_to_music(50, tl, 3.5, 5.5, 1.0)
    changes = np.asarray(change_times(durs))
    b = tl["boundaries"][0]
    fast = [d for d, c in zip(durs, changes) if c <= b + 1e-6]
    calm = [d for d, c in zip(durs, changes) if c > b + 1e-6]
    assert np.mean(fast) < np.mean(calm)
    downs = set(np.round(tl["downbeats"], 3))
    on_bar = sum(1 for c in changes[:-1] if round(c, 3) in downs)
    assert on_bar >= 0.8 * (len(changes) - 1)                                # bars preferred


def test_fit_photos_few_photos_cover_the_start_on_a_bar():
    tl = _mix((120, 200.0, 3.0), (120, 200.0, 3.0))
    durs = fit_photos_to_music(10, tl, 3.5, 5.5, 1.0)
    assert len(durs) == 10 and all(3.5 - 1e-6 <= d <= 5.5 + 1e-6 for d in durs)
    assert sum(durs) < tl["duration"]
    assert _on_grid(change_times(durs), tl["beats"])
    assert _on_grid(change_times(durs)[-1:], tl["downbeats"])                # the video ends on a bar


def test_fit_plan_to_music_places_photo_changes_on_the_mix(cfg):
    tl = _mix((120, 100.0, 4.0), (124, 100.0, 4.0))
    cfg = {**cfg, "photo": {"fit_to_music": True, "min_duration": 3.5, "max_duration": 5.5},
           "transitions": {**cfg["transitions"], "fade_duration": 1.0}}
    plan = {"shots": [{"type": "photo", "source": f"p{i}.jpg", "transition": "fade"} for i in range(36)]}
    info = fit_plan_to_music(plan, tl, cfg, [])
    assert info["photos"] == 36
    timeline = compute_timeline(plan, cfg)
    fps = cfg["output"]["fps"]
    assert timeline.total == pytest.approx(tl["duration"], abs=1 / fps)
    assert all(c.overlap == pytest.approx(1.0) for c in timeline.clips[1:])
    cuts = [timeline.cut(i) for i in range(len(timeline) - 1)]
    assert _on_grid(cuts, tl["beats"], tol=1 / fps)
    # not applicable: setting off, or a plan that is not only photos
    assert fit_plan_to_music({"shots": [{"type": "photo", "source": "a.jpg"}]}, tl,
                             {**cfg, "photo": {}}, []) is None
    mixed = {"shots": [{"type": "photo", "source": "a.jpg"}, {"source": 0, "start": 0, "end": 2}]}
    assert "skipped" in fit_plan_to_music(mixed, tl, cfg, [10.0])


# ── transitions ──────────────────────────────────────────────────────────────

def test_fade_respects_its_own_duration_setting(cfg):
    assert transition_duration("fade", cfg) == 0.35                          # old recipes unchanged
    assert transition_duration("fade", {"transitions": {"duration": 1.0}}) == 0.35
    assert transition_duration("fade", {"transitions": {"fade_duration": 1.0}}) == 1.0
    assert transition_duration("zoom", {"transitions": {"duration": 0.22, "zoom_duration": 0.6}}) == 0.6
    assert transition_duration("soft_slide", cfg) == 0.8
    assert transition_duration("soft_zoom", {"transitions": {"soft_zoom_duration": 1.2}}) == 1.2


# ── end to end: photos fitted to an AutoMix of a playlist ────────────────────

@pytest.mark.integration
def test_render_photos_fitted_to_a_playlist_and_check_splices(workspace, tmp_path):
    import json

    from PIL import Image

    from conftest import has_ffmpeg
    from social_video_editor.adapters.files.jsonio import read_yaml, write_yaml
    from social_video_editor.application.check import check_version
    from social_video_editor.application.render import render
    from social_video_editor.container import build_services
    from test_automix import click_song

    if not has_ffmpeg():
        pytest.skip("FFmpeg is not installed")
    music = workspace / "music" / "set"
    music.mkdir(parents=True)
    click_song(music / "01.wav", 120, body=20.0, tail=4.0)
    click_song(music / "02.wav", 124, body=20.0, tail=4.0, amp=0.3, pad_hz=247)
    photos = tmp_path / "photos"
    photos.mkdir()
    for k in range(7):
        Image.new("RGB", (192, 108), (25 * k, 90, 160)).save(photos / f"p{k}.jpg")
    svc = build_services()
    yaml_path = svc.projects.create("slides", "base", [str(photos)])
    data = read_yaml(yaml_path)
    data["settings"] = {"output": {"width": 160, "height": 90, "fps": 24}, "subtitles": {"auto": False},
                        "export": {"cover": False}, "duration": {"target": 0, "max": None},
                        "music": {"playlist": ["set"]}, "transitions": {"fade_duration": 1.0},
                        "photo": {"fit_to_music": True, "min_duration": 3.5, "max_duration": 5.5}}
    write_yaml(yaml_path, data)
    project = svc.projects.load(yaml_path.parent)
    plan = {"shots": [{"type": "photo", "source": str(photos / f"p{k}.jpg"), "duration": 4.5,
                       "transition": "fade" if k else "cut"} for k in range(7)]}
    final = render(svc, project, plan)
    vdir = final.parent
    mix = json.loads((vdir / "automix.json").read_text())
    summary = json.loads((vdir / "summary.json").read_text())
    assert summary["music"]["playlist"] == ["01.wav", "02.wav"]
    assert summary["duration_sec"] == pytest.approx(mix["duration"], abs=0.05)     # video = music length
    assert len(mix["boundaries"]) == 1
    used = json.loads((vdir / "plan_used.json").read_text())
    n = len(used["shots"])            # on screen = slot + half of each neighbouring 1 s fade
    slots = [s["duration"] - (0.5 if i else 0) - (0.5 if i < n - 1 else 0) for i, s in enumerate(used["shots"])]
    assert all(3.5 - 0.05 <= d <= 5.5 + 0.05 for d in slots)
    assert sum(slots) == pytest.approx(mix["duration"], abs=0.05)
    result, _ = check_version(svc, str(project.dir))
    splices = result["metrics"]["automix"]["splices"]
    assert len(splices) == 1 and splices[0]["silences"] == []
    assert not [e for e in result["errors"] if "AutoMix" in e or "duration" in e]
    assert not [w for w in result["warnings"] if "AutoMix" in w]
