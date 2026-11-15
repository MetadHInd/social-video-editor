"""Synthetic projects of every recipe for `sve report` (files on disk only: no FFmpeg, no models).

`busy_explainer` is the worst normal case: a script with sentences missing, said twice or matched
poorly, many cutaways and warnings, b-roll to fetch and clips left over, a failed check and decisions
left by `sve make`. Used by tests/test_report_screen.py (and to measure the report's length)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from social_video_editor.domain import script_align as sa
from social_video_editor.domain.explainer import build_plan
from social_video_editor.domain.graphics import TEMPLATES
from social_video_editor.domain.script import parse_script


def _json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _base(ws: Path, name: str, recipe: str, n_sources: int = 1, settings: dict | None = None,
          duration: float = 300.0) -> Path:
    src = ws / "media"
    src.mkdir(exist_ok=True)
    d = ws / "projects" / name
    (d / "cache").mkdir(parents=True)
    files = []
    for i in range(n_sources):
        f = src / f"{name}_{i}.mov"
        f.write_bytes(b"")
        files.append(str(f))
        _json(d / "cache" / f"{name}_{i}_media.json", {"path": str(f), "duration": duration, "hdr": False,
                                                        "has_audio": True})
    (d / "project.yaml").write_text(yaml.safe_dump({"name": name, "recipe": recipe, "sources": files,
                                                    "settings": settings or {}}), encoding="utf-8")
    return d


def _version(d: Path, name: str, video: str, check: dict | None = None) -> None:
    v = d / name
    v.mkdir()
    (v / video).write_bytes(b"")
    (v / video.replace(".mp4", ".srt")).write_text("", encoding="utf-8")
    _json(v / "summary.json", {"video": video, "duration_sec": 61.2, "shots": 30, "resolution": "1080x1920",
                               "audio": {"after_lufs": "-14.0", "true_peak_dbtp": "-1.1"}})
    if check:
        _json(v / "check.json", check)


def _check(version: str, errors: int, warnings: int) -> dict:
    return {"version": version, "ok": not errors, "errors": [f"text {k} is outside the safe zone" for k in range(errors)],
            "warnings": [f"cut {k} is 90 ms off the beat" for k in range(warnings)],
            "metrics": {"lufs": -14.0, "shots": 30}, "listen": [f"listen/0{k}_jump.wav" for k in range(min(warnings, 4))]}


def unboxing(ws: Path) -> Path:
    d = _base(ws, "unbox", "unboxing", 2)
    shots = [{"source": i % 2, "start": 10 + i * 4, "end": 12.5 + i * 4, "role": "hook" if i == 0 else "story",
              "transition": "flash" if i % 5 == 0 else "cut"} for i in range(24)]
    _json(d / "plan.json", {"shots": shots, "texts": [{"text": f"Product {k}", "shot": k, "style": "card"}
                                                      for k in range(8)]})
    _json(d / "cache" / "products.json", {"model": "m", "language": "es", "segments": [
        {"source": 0, "start": 10.0 + 6 * k, "end": 14.0 + 6 * k, "label": f"item{k}", "confidence": 0.3 + k * 0.05}
        for k in range(12)]})
    for v in ("v1", "v2"):
        _version(d, v, "unbox.mp4", _check(v, 0, 2) if v == "v2" else None)
    _version(d, "v2SinMusica", "unbox_sin_musica.mp4")
    return d


def haul(ws: Path) -> Path:
    d = _base(ws, "haul", "haul", 3)
    shots = [{"source": i % 3, "start": 5 + i * 3, "end": 7 + i * 3} for i in range(40)]
    _json(d / "plan.json", {"shots": shots, "texts": [{"text": "Haul time", "shot": 0, "style": "hook"},
                                                      {"text": "Follow", "start": 50, "end": 53, "style": "cta"}]})
    _version(d, "v1", "haul.mp4", _check("v1", 1, 5))
    return d


def talk(ws: Path) -> Path:
    d = _base(ws, "meeting", "talk", 1, duration=3600.0)
    shots = [{"source": 0, "start": 30 * i, "end": 30 * i + 27, "transition": "soft_cut"} for i in range(110)]
    _json(d / "plan.json", {"shots": shots, "texts": []})
    _version(d, "v1SinMusica", "meeting_sin_musica.mp4")
    return d


def slideshow(ws: Path, n: int = 300) -> Path:
    album = ws / "album"
    album.mkdir()
    for i in range(n):
        (album / f"img_{i:03d}.jpg").write_bytes(b"")
    d = ws / "projects" / "trip"
    (d / "cache").mkdir(parents=True)
    (ws / "music" / "mix").mkdir(parents=True)
    for k in range(6):
        (ws / "music" / "mix" / f"Track {k}.mp3").write_bytes(b"")
    (d / "project.yaml").write_text(f"name: trip\nrecipe: slideshow\nsources:\n  - {album}\n"
                                    "settings:\n  music: {playlist: [mix]}\n", encoding="utf-8")
    shots = [{"type": "photo", "source": str(album / f"img_{i:03d}.jpg"), "duration": 4.0} for i in range(n)]
    _json(d / "plan.json", {"shots": shots, "texts": []})
    _version(d, "v1Borrador", "trip_borrador.mp4")
    return d


_WORDS = ("amber basil cedar dune ember fjord grove harbor iris jasper kelp lagoon meadow nectar orchid pebble quartz "
          "river sable tundra umber velvet willow yarrow zephyr anchor bramble cobalt delta fennel garnet heron indigo "
          "juniper kestrel linden maple nimbus onyx poplar").split()


def _sentence(k: int) -> str:
    """Sentences with words of their own (the matcher tells them apart), seven words each."""
    return " ".join(f"{_WORDS[(k * 7 + j) % len(_WORDS)]}{k}" for j in range(7)).capitalize() + "."


def busy_explainer(ws: Path) -> Path:
    """40 sentences: 5 never recorded, 6 said twice, 4 matched poorly; 16 cutaways (6 b-roll, 4 without a
    clip), warnings, unused clips, a failed check of the latest render and 4 decisions left by `sve make`."""
    d = _base(ws, "lesson", "explainer", 1, duration=400.0)
    lines = []
    for k in range(40):
        lines.append(_sentence(k))
        if k % 6 == 2:
            lines.append(f'[bars title="Figure {k}" data="A:{k}, B:{k + 5}" unit="%"]')
        elif k % 6 == 4:
            lines.append(f'[broll query="a phone showing topic {k}"]')
        elif k % 6 == 5 and k < 30:
            lines.append(f'[title text="Part {k}"]')
    script = parse_script("\n".join(lines) + "\n", templates=set(TEMPLATES))
    words, t = [], 0.5
    for k, s in enumerate(script.sentences):
        if k % 8 == 7:
            continue                                      # never recorded
        said = s.text.split()
        if k % 7 == 3:                                    # said twice
            said = said + said
        if k % 10 == 5:                                   # misheard
            said = said[:4] + ["something", "else", "entirely"] + said[-2:]
        for w in said:
            words.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2), "prob": 1})
            t += 0.35
        t += 0.8
    plan = build_plan(script, sa.align([s.text for s in script.sentences], sa.transcript([{"words": words}]), {}),
                      {}, "script.md")
    brolls = [c for c in plan["cutaways"] if c.get("type") == "broll"]
    for c in brolls[:2]:
        c["file"] = f"footage/pexels_{brolls.index(c)}_phone.mp4"
    plan["shorts"] = [{"name": "packets", "from_sentence": 1, "to_sentence": 4},
                      {"name": "auto-1", "from_sentence": 10, "to_sentence": 16, "_auto": "shorts", "score": 5.4,
                       "why": "starts with a question, a graphic inside, about 40 s"}]
    _json(d / "plan.json", plan)
    _json(d / "cache" / "lesson_0_analysis.json", {"words": words, "transcription": {
        "engine": "faster-whisper", "model": "small", "device": "CPU (int8)"}})
    (d / "script.md").write_text("\n".join(lines), encoding="utf-8")
    records = [{"file": f"footage/pexels_{k}_phone.mp4", "source": "pexels", "used": k < 2, "query": "a phone"}
               for k in range(5)]
    for r in records:
        (d / r["file"]).parent.mkdir(exist_ok=True)
        (d / r["file"]).write_bytes(b"\0" * 1_200_000)
    _json(d / "footage" / "credits.json", records)
    _json(d / "cache" / "footage_search.json", {"k": {"at": 1, "candidates": []}})
    for v in ("v1Borrador", "v1Borrador_2", "v1", "v1SinMusica", "v2Borrador"):
        _version(d, v, f"lesson{'' if v[2:3] == '' else ''}.mp4")
    _version(d, "v2", "lesson.mp4", _check("v2", 5, 9))
    summary = json.loads((d / "v2" / "summary.json").read_text(encoding="utf-8"))
    summary["breaths"] = {"lesson_0": {"pauses": 34, "with_sound": 21, "seconds": 9.6, "depth_db": 12, "words": 230}}
    summary["music_levels"] = {"voice_lufs": -18.2, "music_lufs": -20.1, "gain_db": -11.9, "under_voice_db": 10,
                               "under_pause_db": 4, "intro_db": 4, "speech_spans": 31}
    _json(d / "v2" / "summary.json", summary)
    _json(d / "review.json", {"version": "v2", "renders": 2, "fixed": ["text 1 moved up", "clip 3 replaced"],
                              "pending": [{"code": "sentence_unmatched", "message": f"sentence {k} was not found in the "
                                           "recording: record it again and run sve make",
                                           "options": ["record it again", "remove it from script.md"]}
                                          for k in (7, 15, 23, 31)]})
    return d


RECIPES = {"unboxing": unboxing, "haul": haul, "talk": talk, "slideshow": slideshow, "explainer": busy_explainer}
