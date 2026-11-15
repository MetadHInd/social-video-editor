"""`sve report` fits one screen for every recipe: TO DO first (what the engine could not settle, with the
command that does it), then the summary; long sections collapse ("… N more (--full)"); --json keeps all."""
from __future__ import annotations

import json

import pytest

import report_projects as rp
from social_video_editor.cli import main


def _run(capsys, *args) -> str:
    main(list(args))
    return capsys.readouterr().out


@pytest.mark.parametrize("recipe", sorted(rp.RECIPES))
def test_every_recipe_fits_one_screen_with_the_to_do_first(workspace, capsys, recipe):
    d = rp.RECIPES[recipe](workspace)
    lines = _run(capsys, "report", d.name).splitlines()
    assert len(lines) <= 50 and max(map(len, lines)) <= 120, "\n".join(lines)
    assert lines[0].startswith(f"PROJECT {d.name} · recipe {recipe}")
    assert lines[1].startswith("TO DO ")
    full = _run(capsys, "report", d.name, "--full").splitlines()
    assert len(full) >= len(lines) and not any("(--full" in x for x in full)


def test_busy_explainer_says_what_to_do_and_collapses_the_rest(workspace, capsys):
    d = rp.busy_explainer(workspace)
    out = _run(capsys, "report", d.name)
    todo = out.split("SOURCES")[0]
    assert "⚠ DECISIONS (4) left by sve make v2: choose, then sve make <project>" in todo
    assert "1. sentence 7 was not found in the recording" in todo and "→ record it again · remove it from script.md" in todo
    assert todo.count("→ record it again") == 1                    # the same options are not repeated
    assert "… +1 more decisions (--full)" in todo
    assert "b-roll cues without a clip → sve broll <project>" in todo
    assert "3 clips in footage/ no plan uses (3.6 MB) → sve broll <project> --unused --yes" in todo
    assert "check v2: 5 errors" in todo and "sve make <project>" in todo
    assert "script sentences not found" not in todo                # already one of the decisions
    assert "more sentences not found (--full)" in out and "more errors (--full)" in out
    assert "more warnings (--full)" in out

    full = _run(capsys, "report", d.name, "--full")
    assert "4. sentence 31 was not found" in full and "fixed: clip 3 replaced" in full

    r = json.loads(_run(capsys, "report", d.name, "--json"))
    assert len(r["review"]["pending"]) == 4 and len(r["check"]["warnings"]) == 9
    assert r["footage"] == {"unused": 3, "bytes": 3_600_000 + len(json.dumps({"k": {"at": 1, "candidates": []}})),
                            "cache": 1}
    assert [x.get("kind") for x in r["todo"]][:1] == ["decisions"] and all(x["do"] for x in r["todo"])
    assert len(r["plan"]["script"]["missing"]) > 3                  # nothing collapsed in JSON


def test_drafts_only_project_reports_its_latest_draft(workspace, capsys):
    d = rp.slideshow(workspace, 5)                                  # sve make renders drafts: v1Borrador only
    out = _run(capsys, "report", d.name)
    assert "VERSIONS v1Borrador · latest v1Borrador" in out
    assert "⚠ v1Borrador was not checked → sve check <project>" in out


def test_a_new_project_says_the_next_command(workspace, capsys):
    clip = workspace / "a.mov"
    clip.write_bytes(b"")
    main(["new", "fresh", "--sources", str(clip)])
    out = _run(capsys, "informe", "fresh", "--completo")             # Spanish aliases
    assert "⚠ no plan yet → sve make <project> (or sve plan <project>)" in out
    assert "⚠ 1 sources not analyzed yet → sve analyze <project>" in out


def test_nothing_to_do(workspace, capsys):
    d = rp.talk(workspace)
    (d / "v1SinMusica" / "check.json").write_text(json.dumps({"version": "v1SinMusica", "ok": True, "errors": [],
                                                              "warnings": [], "metrics": {}, "listen": []}))
    assert "TO DO nothing: the engine settled everything it found" in _run(capsys, "report", d.name)
