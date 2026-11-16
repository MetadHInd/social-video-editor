"""Music the engine composes: the score (pure, deterministic), the video's arc, the synthesizer and the
music.file: compose wiring. Synthetic data only."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from social_video_editor.adapters.files.jsonio import write_json
from social_video_editor.adapters.files.recipes import YamlRecipeRepository
from social_video_editor.application import music as am
from social_video_editor.domain import compose as dc
from social_video_editor.domain.models import Project
from social_video_editor.domain.script import split_sentences

# ── the score ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", list(dc.PRESETS))
def test_same_parameters_same_score(name):
    _, preset = dc.resolve_preset(name)
    secs = dc.template_sections(60, preset["bpm"])
    a = dc.compose(preset, 11, secs, 60, preset_name=name)
    b = dc.compose(preset, 11, secs, 60, preset_name=name)
    assert dc.score_hash(a) == dc.score_hash(b)
    assert dc.score_hash(dc.compose(preset, 12, secs, 60, preset_name=name)) != dc.score_hash(a)
    assert a["events"] and all(0 <= e["t"] < 60 for e in a["events"])
    assert {e["r"] for e in a["events"]} >= {"drums", "bass", "pad", "arp", "lead", "fx"}


def test_presets_sound_different():
    hashes, tempos, keys = set(), set(), set()
    for name in dc.PRESETS:
        _, p = dc.resolve_preset(name)
        hashes.add(dc.score_hash(dc.compose(p, 1, dc.template_sections(30, p["bpm"]), 30, preset_name=name)))
        tempos.add(p["bpm"])
        keys.add((p["key"], p["mode"]))
    assert len(hashes) == len(dc.PRESETS) and len(tempos) == len(dc.PRESETS) and len(keys) == len(dc.PRESETS)


def test_seed_from_text_is_stable():
    assert dc.seed_of(None, "my_video") == dc.seed_of(None, "my_video") == dc.seed_of("", "my_video")
    assert dc.seed_of(7) == 7 and dc.seed_of("7") == 7
    assert dc.seed_of(None, "a") != dc.seed_of(None, "b")


def test_pack_preset_extends_a_base():
    name, p = dc.resolve_preset("channel", {"channel": {"base": "trap", "bpm": 90, "key": "A"}})
    assert name == "channel" and p["bpm"] == 90 and p["key"] == "A" and p["bass"]["sound"] == "808"
    with pytest.raises(ValueError):
        dc.resolve_preset("nope")


def test_chords_are_diatonic():
    assert dc.chord("I", dc.key_root("C"), "major") == [48, 52, 55]
    assert dc.chord("vi", dc.key_root("C"), "major") == [45, 48, 52]          # A minor, kept around C3
    am_ = dc.chord("i", dc.key_root("A"), "minor")
    assert [n % 12 for n in am_] == [9, 0, 4]


def test_grid_is_exact_and_continuous_sections_share_it():
    _, p = dc.resolve_preset("upbeat")
    T = 60 / p["bpm"]
    secs = [{"kind": "intro", "start": 0.0, "end": 5.3}, {"kind": "chorus", "start": 5.3, "end": 20.0}]
    score = dc.compose(p, 3, secs, 20.0, preset_name="upbeat")
    chorus = score["sections"][1]
    assert abs(chorus["start"] - round(chorus["start"] / T) * T) < 1e-4        # moved to the nearest beat
    gaps = [b - a for a, b in zip(score["beats"], score["beats"][1:])]
    assert max(gaps) - min(gaps) < 1e-3                                       # one steady grid


def test_section_after_silence_starts_exactly_with_a_hit():
    _, p = dc.resolve_preset("trap")
    secs = [{"kind": "intro", "start": 0.0, "end": 4.0}, {"kind": "silence", "start": 4.0, "end": 7.123},
            {"kind": "chorus", "start": 7.123, "end": 20.0}]
    score = dc.compose(p, 3, secs, 20.0, preset_name="trap")
    assert score["sections"][2]["start"] == 7.123 and score["sections"][2]["hit"]
    assert 7.123 in score["beats"]
    assert not [e for e in score["events"] if 4.0 + 0.01 < e["t"] < 7.1]          # silence: nothing
    hits = [e for e in score["events"] if e["i"] == "impact"]
    assert [h["t"] for h in hits] == [0.0, 7.123]
    g = score["gates"]
    assert g[0][1] == pytest.approx(4.0, abs=0.6) and g[0][3] == dc.DEAD_CUT and g[1][:3] == [7.123, 20.0, dc.ENTRY_FADE]
    assert g[-1][3] == dc.FINAL_FADE


def test_template_follows_the_arc():
    secs = dc.template_sections(90, 96)
    assert [s["kind"] for s in secs] == ["intro", "verse", "silence", "chorus", "silence", "outro"]
    assert secs[0]["start"] == 0 and secs[-1]["end"] == 90


# ── the video's arc ──────────────────────────────────────────────────────────

def _sentences(texts, length=4.0, gap=0.5):
    out, t = [], 0.3
    for i, text in enumerate(texts):
        out.append({"n": i, "start": round(t, 3), "end": round(t + length, 3), "text": text})
        t += length + gap
    return out


def test_arc_from_script_headings():
    script = """# Pilot
# 1 · Scene
You have full signal and nothing loads.
# 2 · Conflict
You restart it. Nothing.
# 3 · Question
Why is it slow?
# 4 · Explanation
[bars data="A:1, B:2"]
The antenna shares. Everyone at once.
# 6 · Twist and solution
It is not your phone. Use the wifi at those hours.
# 7 · Close
See you."""
    marks = dc.arc_marks(script, split_sentences)
    assert marks == {0: "intro", 1: "silence", 3: "chorus", 4: "chorus", 6: "twist", 7: "outro", 8: "outro"}
    sents = _sentences(["You have full signal and nothing loads.", "You restart it.", "Nothing.", "Why is it slow?",
                        "The antenna shares.", "Everyone at once.", "It is not your phone.",
                        "Use the wifi at those hours.", "See you."], length=3.0)
    total = sents[-1]["end"] + 1
    secs = dc.arc_sections(total, sents, marks=marks)
    assert [s["kind"] for s in secs] == ["intro", "silence", "chorus", "silence", "outro"]
    assert secs[1]["start"] == sents[1]["start"] and secs[2]["start"] == sents[3]["start"]   # on the first word
    assert secs[3]["start"] == sents[6]["start"] and secs[4]["start"] == sents[7]["start"]
    assert secs[-1]["end"] == total


def test_spanish_headings_and_notes():
    text = "# Una nota larga que no es un encabezado aunque diga conflicto y final\nHola.\n# 2 · Conflicto\nNada.\n"
    assert dc.arc_marks(text, split_sentences) == {1: "silence"}


def test_arc_without_headings_uses_the_question_and_the_graphics():
    sents = _sentences(["It loads slowly.", "You restart it.", "Why does it happen?", "Antennas share.",
                        "Everyone at once.", "Seven to nine.", "It is not your phone.", "Use the wifi.",
                        "Try it tonight.", "Bye."])
    total = sents[-1]["end"] + 0.5
    graphic = (sents[4]["start"], sents[5]["end"])
    secs = dc.arc_sections(total, sents, fullscreen=[graphic])
    kinds = [s["kind"] for s in secs]
    assert kinds == ["intro", "silence", "chorus", "silence", "outro"]
    assert secs[2]["start"] == sents[2]["start"] and "question" in secs[2]["why"]
    assert secs[3]["start"] == sents[6]["start"]                     # first sentence after the last graphic
    assert secs[4]["start"] == sents[7]["start"]
    no_silence = dc.arc_sections(total, sents, fullscreen=[graphic], silences=False)
    assert "silence" not in [s["kind"] for s in no_silence]


def test_arc_without_sentences_uses_the_cuts():
    secs = dc.arc_sections(40.0, [], cuts=[5.0, 11.7, 20.0, 29.1, 35.0])
    assert [(s["kind"], s["start"]) for s in secs] == [("intro", 0.0), ("chorus", 11.7), ("outro", 29.1)]
    assert dc.arc_sections(4.0, [])[0]["kind"] == "chorus"


def test_silence_never_too_long_and_short_parts_join():
    sents = _sentences(["One.", "Two.", "Three.", "Why?", "Five."], length=3.0)
    marks = {0: "intro", 1: "silence", 3: "chorus"}
    secs = dc.arc_sections(sents[-1]["end"], sents, marks=marks)
    sil = [s for s in secs if s["kind"] == "silence"][0]
    assert sil["end"] - sil["start"] <= dc.MAX_SILENCE
    assert all(s["end"] - s["start"] >= dc.MIN_SECTION for s in secs if s["kind"] != "silence")


# ── the synthesizer ──────────────────────────────────────────────────────────

def test_render_has_the_length_loudness_and_hash(tmp_path):
    import soundfile as sf

    from social_video_editor.adapters.synth.renderer import NumpySynthesizer, loudness

    _, p = dc.resolve_preset("calm")
    secs = [{"kind": "intro", "start": 0.0, "end": 3.0}, {"kind": "silence", "start": 3.0, "end": 3.5},
            {"kind": "chorus", "start": 3.5, "end": 8.0}]
    score = dc.compose(p, 5, secs, 8.0, preset_name="calm")
    syn = NumpySynthesizer()
    settings = {"mix": p["mix"], "pad": p["pad"], "lufs": -16.0}
    a = syn.render(score, tmp_path / "a.flac", settings)
    b = syn.render(score, tmp_path / "b.wav", settings)
    assert a["sha256"] == b["sha256"] and a["duration"] == 8.0
    x, sr = sf.read(tmp_path / "a.flac")
    assert sr == 48000 and x.shape == (8 * 48000, 2)
    assert abs(loudness(x, sr) + 16.0) < 0.6
    assert abs(x).max() <= 10 ** (-2.0 / 20) + 1e-4
    quiet = x[int(3.1 * sr):int(3.45 * sr)]
    assert abs(quiet).max() < 1e-3                                    # the silence is silent
    y, _ = sf.read(tmp_path / "a.flac", dtype="int32")
    import hashlib

    assert hashlib.sha256((y >> 8).astype("<i4").tobytes()).hexdigest() == a["sha256"]   # the file holds the hash


def test_loudness_matches_bs1770_on_a_sine():
    import numpy as np

    from social_video_editor.adapters.synth.renderer import loudness

    t = np.arange(48000 * 3) / 48000
    x = 0.1 * np.sin(2 * np.pi * 1000 * t)
    assert loudness(np.stack([x, x], 1), 48000) == pytest.approx(-20.0 + 0.0, abs=0.3)   # 1 kHz −20 dBFS ×2 ch


# ── music.file: compose ─────────────────────────────────────────────────────

def _plan(n=8, length=3.0, gap=0.4, question=3):
    shots, sents, t = [], [], 0.0
    for i in range(n):
        text = "Why is it slow?" if i == question else f"Sentence number {i}."
        sents.append({"n": i, "text": text, "source": 0, "start": round(t, 3), "end": round(t + length, 3),
                      "words": [["w", round(t + 0.2, 3), round(t + 0.5, 3)]]})
        shots.append({"source": 0, "start": round(t, 3), "end": round(t + length, 3), "transition": "cut"})
        t += length + gap
    cut = [{"type": "graphic", "template": "bars", "data": "A:1, B:2", "at_sentence": 5, "until_sentence": 5}]
    return {"shots": shots, "sentences": sents, "cutaways": cut, "texts": []}


def _svc(script=None):
    from social_video_editor.adapters.synth.renderer import NumpySynthesizer
    from social_video_editor.domain.models import UserError

    def read_script(project):
        if script is None:
            raise UserError("no script")
        return "script.md", script

    projects = SimpleNamespace(read_script=read_script, exists=lambda p: Path(p).exists(),
                               write_json=lambda p, d: write_json(p, d))
    return SimpleNamespace(projects=projects, synth=NumpySynthesizer())


def _project(tmp_path, settings=None):
    cfg = YamlRecipeRepository().load("explainer", {"music": {"file": "compose"}, **(settings or {})})
    return Project(dir=tmp_path, name="demo", recipe="explainer", sources=[], cfg=cfg)


def test_defaults_do_not_compose():
    repo = YamlRecipeRepository()
    for recipe in ("base", "explainer", "unboxing", "talk", "slideshow"):
        assert not dc.is_compose(repo.load(recipe)["music"])
    assert repo.load("explainer")["music"]["file"] == "none"


def test_prepare_music_composes_for_the_plan(tmp_path):
    import copy

    project = _project(tmp_path)
    plan = _plan()
    cfg = copy.deepcopy(project.cfg)
    svc = _svc()
    before = [(s["start"], s["end"]) for s in plan["shots"]]
    path = am.prepare_music(svc, project, plan, cfg, [{"duration": 100.0}], no_music=False)
    assert path is not None and path.exists() and path.parent.name == am.COMPOSE_DIR
    proof = json.loads(path.with_name(path.stem + am.ORIGIN_SUFFIX).read_text())
    assert proof["seed"] == dc.seed_of(None, "demo") and "generó" in proof["statement"]   # project language: es
    assert proof["audio"]["sha256"] and proof["score_sha256"]
    kinds = [s["kind"] for s in proof["sections"]]
    assert kinds[:3] == ["intro", "silence", "chorus"]
    assert [(s["start"], s["end"]) for s in plan["shots"]] == before          # explainer: cuts are not moved
    assert cfg["music"]["_start"] == 0 and cfg["music"]["fade_in"] <= 0.02
    assert any(s.get("_beats") for s in plan["shots"])
    assert cfg["music"]["_composed"]["preset"] == "upbeat"

    again = am.prepare_music(svc, project, _plan(), copy.deepcopy(project.cfg), [{"duration": 100.0}], False)
    assert again == path                                                      # same timing: cached
    other = am.prepare_music(svc, project, _plan(length=3.5), copy.deepcopy(project.cfg), [{"duration": 100.0}], False)
    assert other != path                                                      # new timing: a new track
    assert am.prepare_music(svc, project, _plan(), copy.deepcopy(project.cfg), [{"duration": 100.0}], True) is None


def test_script_headings_drive_the_sections(tmp_path):
    script = "\n".join(["# Scene", "Sentence number 0.", "Sentence number 1.", "# Question", "Sentence number 2.",
                        "Sentence number 3.", "Sentence number 4.", "Sentence number 5.", "# Solution",
                        "Sentence number 6.", "Sentence number 7."])
    project = _project(tmp_path, {"music": {"file": "compose", "compose": {"preset": "calm", "seed": 4}}})
    from social_video_editor.domain.timeline import compute_timeline

    plan = _plan()
    path, score, made = am.compose_for_video(_svc(script), project, plan, project.cfg,
                                             compute_timeline(plan, project.cfg))
    assert made and score["preset"] == "calm" and score["seed"] == 4
    assert [(s["kind"], s["why"].startswith("script heading")) for s in score["sections"]] == \
        [("intro", True), ("chorus", True), ("outro", True)]


def test_standalone_track_and_listening_copy(tmp_path):
    svc = _svc()
    svc.recipes = YamlRecipeRepository()
    out, proof = am.compose_track(svc, tmp_path / "t.flac", 12.0, "tense", 9, None, {"language": "en"})
    assert out.exists() and (tmp_path / "t.origin.json").exists()
    assert proof["preset"] == "tense" and proof["seed"] == 9 and "generated by the program" in proof["statement"]
    assert proof["audio"]["duration"] == 12.0


def test_cli_parses_compose():
    from social_video_editor.cli import parser

    a = parser().parse_args(["musica", "--componer", "--estilo", "calm", "--segundos", "30", "--semilla", "3"])
    assert a.compose and a.preset == "calm" and a.seconds == 30 and a.seed == 3 and a.query is None


def test_spanish_alias():
    cfg = YamlRecipeRepository().load("explainer", {"musica": {"archivo": "componer",
                                                               "componer": {"estilo": "warm", "semilla": 2}}})
    assert dc.is_compose(cfg["music"]) and dc.settings(cfg["music"])["preset"] == "warm"
