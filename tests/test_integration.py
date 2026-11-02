"""Tests with FFmpeg on synthetic media generated on the fly (nothing from the user)."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from conftest import requires_ffmpeg
from social_video_editor.adapters.ffmpeg import filters
from social_video_editor.adapters.ffmpeg.encoder import FfmpegEncoder
from social_video_editor.adapters.ffmpeg.luts import hlg_lut_path
from social_video_editor.adapters.ffmpeg.probe import FfprobeMediaProbe
from social_video_editor.adapters.files.jsonio import read_json
from social_video_editor.adapters.system.tools import FFMPEG, FFPROBE
from social_video_editor.domain.timeline import compute_timeline

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def ff(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def probe(path: Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


# ── HDR (HLG) → SDR ──────────────────────────────────────────────────────────

def test_hlg_lut_generated_in_user_cache(tmp_path):
    lut = hlg_lut_path(size=17)
    assert lut.parent == tmp_path / "user_cache" / "social_video_editor" / "luts"     # never in the package
    lines = lut.read_text().splitlines()
    assert lines[0] == 'TITLE "HLG to SDR709"'
    assert lines[1] == "LUT_3D_SIZE 17"
    data = np.array([[float(x) for x in ln.split()] for ln in lines[2:]])
    assert data.shape == (17 ** 3, 3)
    assert data.min() >= 0 and data.max() <= 1
    assert np.allclose(data[0], 0)                                     # black → black
    assert np.all(np.diff(data[:17, 0]) >= 0)                          # monotonic R ramp
    assert hlg_lut_path(size=17) == lut                                # second time: reused


def test_sdr_chain_converts_hlg(tmp_path):
    hlg = tmp_path / "hlg.mp4"
    # tags on the frames (setparams): FFmpeg 7+ ignores output -color_trc if the frame lacks it
    ff("-f", "lavfi", "-i", "testsrc2=s=320x568:d=0.5:r=30", "-vf",
       "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
       "-c:v", "libx264", hlg)
    info = FfprobeMediaProbe().probe(hlg)
    assert info["hlg"] and info["hdr"] and not info["has_audio"]
    chain = filters.sdr_chain(info)
    assert "lut3d=" in chain and str(tmp_path / "user_cache") in chain
    sdr = tmp_path / "sdr.mp4"
    ff("-i", hlg, "-vf", f"scale=270:480,{chain}", "-c:v", "libx264", "-preset", "ultrafast", sdr)
    v = probe(sdr)["streams"][0]
    assert (v["width"], v["height"], v["pix_fmt"]) == (270, 480, "yuv420p")
    assert v.get("color_transfer") == "bt709"
    # an SDR source uses no LUT
    assert "lut3d" not in filters.sdr_chain({"hlg": False, "hdr": False})


# ── Full render (draft) ──────────────────────────────────────────────────────

def _synthetic_project(workspace: Path, legacy: bool = False) -> Path:
    sources = workspace / "sources"
    sources.mkdir()
    clip = sources / "clip.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=540x960:d=4:r=30",
       "-f", "lavfi", "-i", "sine=f=330:d=4:sample_rate=48000",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip)
    music = workspace / ("musica" if legacy else "music")
    music.mkdir()
    # click track at 120 BPM (one beat every 0.5 s) over a low tone
    ff("-f", "lavfi", "-i",
       "aevalsrc='0.8*sin(2*PI*1000*t)*exp(-mod(t,0.5)*40)+0.1*sin(2*PI*110*t)':d=20:s=44100",
       "-ac", "2", music / "clicks.wav")
    d = workspace / ("proyectos" if legacy else "projects") / "demo"
    d.mkdir(parents=True)
    if legacy:      # the same project as written by the earlier Spanish version
        project = {"nombre": "demo", "receta": "unboxing", "fuentes": [{"ruta": str(clip)}],
                   "ajustes": {"subtitulos": {"auto": False}, "transcripcion": {"modelo": "tiny"},
                               "collage": {"subdivision": 4}}}
        plan = {"tomas": [
            {"fuente": 0, "ini": 0.5, "fin": 2.5, "golpes": 4, "transicion": "corte",
             "efectos": [{"tipo": "zoom_golpe", "t": 0}, {"tipo": "pulso_ritmo"}]},
            {"tipo": "polaroid", "de_toma": 0, "golpes": 2, "texto": "Foto 📸", "transicion": "corte"},
            {"tipo": "collage", "de_tomas": [0, 0, 0], "fondo_de": 0, "golpes": 4, "columnas": 3,
             "transicion": "zoom"}],
            "textos": [{"texto": "Hola 👀", "descripcion": "prueba ✨", "toma": 0, "estilo": "tarjeta"},
                       {"texto": "fin", "toma": 2, "estilo": "etiqueta"}]}
        name = "proyecto.yaml"
    else:
        project = {"name": "demo", "recipe": "unboxing", "sources": [{"path": str(clip)}],
                   "settings": {"subtitles": {"auto": False}, "transcription": {"model": "tiny"},
                                "collage": {"subdivision": 4}}}
        plan = {"shots": [
            {"source": 0, "start": 0.5, "end": 2.5, "beats": 4, "transition": "cut",
             "effects": [{"type": "zoom_punch", "t": 0}, {"type": "beat_pulse"}]},
            {"type": "polaroid", "from_shot": 0, "beats": 2, "text": "Foto 📸", "transition": "cut"},
            {"type": "collage", "from_shots": [0, 0, 0], "background_from": 0, "beats": 4, "columns": 3,
             "transition": "zoom"}],
            "texts": [{"text": "Hola 👀", "description": "prueba ✨", "shot": 0, "style": "card"},
                      {"text": "fin", "shot": 2, "style": "label"}]}
        name = "project.yaml"
    (d / name).write_text(yaml.safe_dump(project, allow_unicode=True), encoding="utf-8")
    (d / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return d


def test_draft_render_end_to_end(workspace, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")             # Whisper never downloads models in tests
    monkeypatch.chdir(workspace.parent)                   # the workspace comes from SVE_WORKSPACE, not the cwd
    d = _synthetic_project(workspace)
    from social_video_editor.cli import main

    main(["render", "demo", "--draft"])

    vdir = d / "v1Borrador"                               # default language: es
    final = vdir / "demo_borrador.mp4"
    assert final.exists()
    for extra in ("demo_borrador.srt", "demo_borrador_portada.jpg", "plan_used.json", "config_used.json",
                  "summary.json"):
        assert (vdir / extra).exists(), extra
    assert not (vdir / "_layer").exists() and not (vdir / "_sfx.wav").exists()

    plan_used, cfg_used = read_json(vdir / "plan_used.json"), read_json(vdir / "config_used.json")
    assert "shots" in plan_used and "output" in cfg_used and cfg_used["language"] == "es"   # written in English
    tl = compute_timeline(plan_used, cfg_used)
    total = tl.total
    assert 4.0 <= total <= 6.0
    assert [c.piece.kind for c in tl] == ["video", "polaroid", "collage"]
    summary = read_json(vdir / "summary.json")
    assert summary["resolution"] == "540x960"
    assert summary["duration_sec"] == pytest.approx(total, abs=0.01)
    assert summary["music"]["file"] == "clicks.wav"
    assert [s["type"] for s in summary["shot_details"]] == ["video", "polaroid", "collage"]

    s = probe(final)
    v = next(x for x in s["streams"] if x["codec_type"] == "video")
    a = next(x for x in s["streams"] if x["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (540, 960)
    assert a["sample_rate"] == "48000"
    assert abs(float(s["format"]["duration"]) - total) <= 1 / 30 + 1e-3
    assert abs(float(v["duration"]) - total) <= 1 / 30 + 1e-3

    lufs = float(summary["audio"]["after_lufs"])
    assert lufs == pytest.approx(-14, abs=1.0)
    measured = FfmpegEncoder().measure_loudness(final, cfg_used)
    assert measured["integrated"] == pytest.approx(lufs, abs=0.15)          # mastering == check measurement
    assert measured["integrated"] == pytest.approx(-14, abs=0.5) and measured["true_peak"] <= -1.0
    assert summary["audio"]["converged"] is True

    # sound effects generated in <workspace>/sfx (polaroid shutter, collage pops, typing, whoosh)
    assert {p.stem for p in (workspace / "sfx").glob("*.wav")} >= {"shutter", "pop", "keystroke", "whoosh"}
    assert summary["sound_effects"] > 10
    assert summary["texts"] == 2
    # segments in the project cache; SDR source → no LUT needed
    assert list((d / "cache" / "segments").glob("polaroid_*.mkv"))
    assert list((d / "cache" / "segments").glob("collage_*.mkv"))
    # drawn shots carry the source sound that continues after their shot (not silence) and keep
    # their exact length (audio only)
    from social_video_editor.adapters.ffmpeg.inspector import FfmpegRenderInspector
    for kind, clip in (("polaroid", tl[1]), ("collage", tl[2])):
        seg = next((d / "cache" / "segments").glob(f"{kind}_*.mkv"))
        x = FfmpegRenderInspector().audio(seg)
        assert np.sqrt(np.mean(x ** 2)) > 0.003, kind
        assert len(x) / 48000 == pytest.approx(clip.dur, abs=1 / 30)
    # typing click sounds at most sfx.typing_max_per_sec
    from social_video_editor.domain.check import burst_rate
    assert all(burst_rate(b) <= 16 for b in summary["typing"])
    assert not (workspace.parent / "user_cache").exists()


def test_legacy_spanish_project_renders_the_same_timeline(workspace, monkeypatch):
    """A project written by the earlier Spanish version (proyecto.yaml, proyectos/, musica/, Spanish plan)
    renders unchanged; an English run gives English folder names."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    d = _synthetic_project(workspace, legacy=True)
    from social_video_editor.application.render import prepare_edit
    from social_video_editor.container import build_services

    svc = build_services()
    p = svc.projects.load(svc.projects.resolve("demo"))
    assert p.dir == d and p.file.name == "proyecto.yaml"
    plan, _, _, music, tl = prepare_edit(svc, p, svc.projects.load_plan(p), copy.deepcopy(p.cfg))
    assert music.name == "clicks.wav"
    assert [c.piece.kind for c in tl] == ["video", "polaroid", "collage"]
    assert 4.0 <= tl.total <= 6.0
    assert plan["texts"][0]["style"] == "card" and plan["shots"][0]["_beats"]
    from social_video_editor.cli import main
    monkeypatch.chdir(workspace.parent)
    data = yaml.safe_load((d / "proyecto.yaml").read_text(encoding="utf-8"))
    data["ajustes"]["idioma"] = "en"                                       # Spanish key for `language`
    (d / "proyecto.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    main(["render", "demo", "--draft", "--sin-musica"])
    assert (d / "v1DraftNoMusic" / "demo_draft_no_music.mp4").exists()
    assert (d / "v1DraftNoMusic" / "demo_draft_no_music_cover.jpg").exists()
