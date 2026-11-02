"""sve report: one compact screen (or JSON) built only from files on disk (no FFmpeg needed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from social_video_editor.cli import main


def _json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _project(ws: Path, name: str = "demo", shots: list | None = None) -> Path:
    """projects/demo with 2 fake sources, cached media info, music, plan, versions, products, check."""
    src = ws / "footage"
    src.mkdir(exist_ok=True)
    for f in ("clip_a.mov", "clip_b.mov"):
        (src / f).write_bytes(b"")
    d = ws / "projects" / name
    (d / "cache").mkdir(parents=True)
    (d / "project.yaml").write_text(
        f"name: {name}\nrecipe: unboxing\nsources:\n  - {src / 'clip_a.mov'}\n  - path: {src / 'clip_b.mov'}\n"
        "settings:\n  music: {start_at: auto, tempo_factor: 1.5}\n", encoding="utf-8")
    # stem-only cache names (earlier versions): used because they record this very file
    _json(d / "cache" / "clip_a_media.json", {"path": str(src / "clip_a.mov"), "duration": 482.0, "hdr": True,
                                              "has_audio": True})
    _json(d / "cache" / "clip_b_media.json", {"path": str(src / "clip_b.mov"), "duration": 12.5, "hdr": False,
                                              "has_audio": False})
    (ws / "music").mkdir()
    (ws / "music" / "Some Song.mp3").write_bytes(b"")
    _json(d / "cache" / "music_Some_Song.json", {"duration": 180, "tempo": 120.0, "interval": 0.5,
                                                 "beats": [], "drop": 21.0})
    shots = shots if shots is not None else [
        {"source": 1, "start": 4.27, "end": 6.47, "role": "hook", "effects": [{"type": "zoom_punch"}]},
        {"source": 0, "start": 9.87, "end": 12.32, "role": "opening", "transition": "flash"},
        {"source": 0, "search": {"range": [100, 130], "dur": 1.5}, "group": "star", "speed": 2},
        {"type": "polaroid", "from_shot": 2, "beats": 2, "group": "star"},
        {"type": "collage", "from_shots": [0, 1, 2], "beats": 4, "transition": "zoom"},
    ]
    _json(d / "plan.json", {"shots": shots, "texts": [
        {"text": "Was it worth it? 👀", "shot": 0, "style": "hook"},
        {"text": "Earrings", "description": "pastel colors ✨", "shot": 1, "style": "card"},
        {"text": "Follow for part 2", "start": 8, "end": 10, "style": "cta"}]})
    for v in ("v1", "v2", "v10_failed"):
        (d / v).mkdir()
    (d / "v1" / "demo.mp4").write_bytes(b"")
    for v, video in (("v2", "demo.mp4"), ("v2SinMusica", "demo_sin_musica.mp4")):
        (d / v).mkdir(exist_ok=True)
        (d / v / video).write_bytes(b"")
        (d / v / video.replace(".mp4", ".srt")).write_text("", encoding="utf-8")
        (d / v / "_tmp_layer.png").write_bytes(b"")            # temporary files are not listed
        _json(d / v / "summary.json", {"video": video, "duration_sec": 21.4, "shots": 5,
                                       "resolution": "1080x1920",
                                       "music": {"file": "Some Song.mp3", "start_sec": 18.733},
                                       "audio": {"after_lufs": "-14.05", "true_peak_dbtp": "-1.2"}})
    _json(d / "cache" / "products.json", {"model": "test-model", "language": "es", "segments": [
        {"source": 0, "start": 30.0, "end": 33.0, "best": [31, 32], "label": "necklace", "confidence": 0.31,
         "score": 0.2},
        {"source": 0, "start": 12.0, "end": 15.5, "best": [12.8, 14.6], "label": "earrings", "confidence": 0.71,
         "score": 0.42}]})
    _json(d / "v2" / "check.json", {"version": "v2", "ok": False, "errors": ["text 2 covers the product"],
                                    "warnings": ["cut 3 is 80 ms off the beat", {"message": "loudness -13.2"}],
                                    "metrics": {"lufs": -14.05, "shots": 5, "nested": {"x": 1}},
                                    "listen": ["listen/01_hook.wav"]})
    (d / "v2" / "listen").mkdir()
    (d / "v2" / "listen" / "01_hook.wav").write_bytes(b"")
    return d


def _run(capsys, *args) -> str:
    main(list(args))
    return capsys.readouterr().out


def test_report_text_is_one_compact_screen(workspace, capsys):
    _project(workspace)
    out = _run(capsys, "report", "demo")
    lines = out.splitlines()
    assert len(lines) <= 50 and max(map(len, lines)) <= 120
    assert lines[0] == "PROJECT demo · recipe unboxing · language es"
    assert "SOURCES 2 · 08:14.50 total" in out and "0 clip_a.mov 08:02.00 HDR" in out and "clip_b.mov 00:12.50 mute" in out
    assert "PLAN 5 shots" in out and "1 group ·" in out and "3 texts (hook 1, card 1, cta 1)" in out
    assert "0 hook s1 4.3-6.5 2.2s +zoom_punch" in out
    assert "1 opening s0 9.9-12.3" in out and "/flash" in out
    assert "2 s0 ?100-130 x2" in out                              # "search" shot (resolved at render time)
    assert "3 polaroid<2" in out and "4 collage<0,1,2" in out and "/zoom" in out
    assert "first text: Was it worth it? 👀" in out
    assert "MUSIC Some Song.mp3 · start_at auto (rendered from 18.73 s) · 120.0 BPM · tempo_factor 1.5" in out
    assert "VERSIONS v1 v2 v2SinMusica v10_failed · latest v2" in out
    assert "v2           demo.mp4 · 21.4 s · 5 shots · -14.05 LUFS · +srt · check.json · listen/ 1 files" in out
    assert "v2SinMusica  demo_sin_musica.mp4" in out and "_tmp_layer" not in out
    assert "PRODUCTS 2 segments (test-model, es)" in out
    assert "earrings s0 12.0-15.5 c0.71 | necklace s0 30.0-33.0 c0.31?" in out    # story order
    assert "⚠ 1 low confidence" in out
    assert "CHECK v2: FAILED · 1 errors · 2 warnings · listen: 1 files" in out
    assert "lufs=-14.05 | shots=5" in out and "nested" not in out
    assert "error: text 2 covers the product" in out and "warning: loudness -13.2" in out


def test_report_json(workspace, capsys):
    _project(workspace)
    r = json.loads(_run(capsys, "report", "demo", "--json"))
    assert r["project"] == "demo" and r["language"] == "es"
    assert [s["duration"] for s in r["sources"]] == [482.0, 12.5]
    assert r["plan"]["shots"] == 5 and len(r["plan"]["timeline"]) == 5
    assert r["plan"]["timeline"][3] == {"n": 3, "type": "polaroid", "role": None, "transition": "cut",
                                        "dur": 1.2, "at": r["plan"]["timeline"][3]["at"], "from": [2],
                                        "group": "star", "beats": 2}
    assert r["music"]["bpm"] == 120.0 and r["music"]["rendered_start"] == 18.733
    assert r["versions"]["latest"]["name"] == "v2" and "summary" not in r["versions"]["latest"]
    assert [v["name"] for v in r["versions"]["variants"]] == ["v2SinMusica"]
    assert r["products"]["low_confidence"] == 1 and r["check"]["ok"] is False


def test_report_of_a_new_project_gives_next_steps(workspace, capsys):
    clip = workspace / "a.mov"
    clip.write_bytes(b"")
    main(["new", "fresh", "--sources", str(clip)])
    out = _run(capsys, "report", "fresh")
    assert "SOURCES 1 · 1 not analyzed yet (sve analyze)" in out and "0 a.mov ?" in out
    assert "PLAN none yet (sve plan <project>)" in out
    assert "MUSIC none" in out
    assert "VERSIONS none yet" in out
    assert "PRODUCTS none (sve products" in out
    assert "CHECK not run" in out


def test_long_plans_are_condensed(workspace, capsys):
    shots = [{"source": 0, "start": 10 + i * 3, "end": 12 + i * 3, "transition": "whip"} for i in range(120)]
    _project(workspace, shots=shots)
    out = _run(capsys, "report", "demo")
    assert len(out.splitlines()) <= 50
    assert "… +" in out and "more (sve view <project>)" in out


def test_legacy_spanish_project_and_alias(workspace, capsys):
    clip = workspace / "a.mov"
    clip.write_bytes(b"")
    d = workspace / "proyectos" / "viejo"
    (d / "v1").mkdir(parents=True)
    (d / "proyecto.yaml").write_text(f"nombre: viejo\nreceta: resena\nfuentes:\n  - ruta: {clip}\n",
                                     encoding="utf-8")
    _json(d / "plan.json", {"tomas": [{"fuente": 0, "ini": 1, "fin": 3, "rol": "gancho", "transicion": "corte"}],
                            "textos": [{"texto": "Hola", "toma": 0, "estilo": "gancho"}]})
    _json(d / "v1" / "resumen.json", {"video": "viejo.mp4", "duracion_seg": 2.0, "tomas": 1,
                                      "audio": {"despues_lufs": "-14.1"}})
    out = _run(capsys, "informe", "viejo")
    assert "recipe review" in out and "0 hook s0 1.0-3.0 2.0s" in out and "1 texts (hook 1)" in out
    assert "v1           viejo.mp4 · 2.0 s · 1 shots · -14.1 LUFS" in out


@pytest.mark.parametrize("bad", ["{not json", "[]"])
def test_unreadable_outputs_are_ignored(workspace, capsys, bad):
    d = _project(workspace)
    (d / "cache" / "products.json").write_text(bad, encoding="utf-8")
    out = _run(capsys, "report", "demo")
    assert "PRODUCTS none" in out
