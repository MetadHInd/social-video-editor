"""`sve broll <project> --unused [--yes]`: clips no plan uses + the search cache; deleted only with --yes."""
from __future__ import annotations

import json

import pytest
import yaml

from social_video_editor import cli
from social_video_editor.domain import footage as fg


def _project(workspace):
    d = workspace / "projects" / "demo"
    (d / "footage").mkdir(parents=True)
    (d / "cache" / "footage_frames").mkdir(parents=True)
    (workspace / "talk.mov").write_bytes(b"")
    (d / "project.yaml").write_text(yaml.safe_dump({"name": "demo", "recipe": "explainer",
                                                    "sources": [str(workspace / "talk.mov")]}), encoding="utf-8")
    clips = {"footage/pexels_1_phone.mp4": 2_000_000, "footage/pexels_2_old.mp4": 3_500_000,
             "footage/pixabay_3_flag.mp4": 1_000_000, "footage/wikimedia_4_prev.webm": 500_000}
    for f, size in clips.items():
        (d / f).write_bytes(b"\0" * size)
        (d / "cache" / "footage_frames" / f"{f.split('/')[1].rsplit('.', 1)[0]}.jpg").write_bytes(b"jpg")
    (d / "footage" / "mine.mp4").write_bytes(b"\0" * 10)                       # no record: the user's, never touched
    records = [{"file": "footage/pexels_1_phone.mp4", "source": "pexels", "used": True, "query": "a phone"},
               {"file": "footage/pexels_2_old.mp4", "source": "pexels", "used": False, "query": "an old phone"},
               # marked unused, but the plan names it (the flag is older than a hand edit): kept
               {"file": "footage/pixabay_3_flag.mp4", "source": "pixabay", "used": False, "query": "a flag"},
               # only the previous plan names it: kept as well
               {"file": "footage/wikimedia_4_prev.webm", "source": "wikimedia", "used": False},
               {"file": "footage/pexels_5_gone.mp4", "source": "pexels", "used": False}]       # its file is gone
    (d / "footage" / "credits.json").write_text(json.dumps(records), encoding="utf-8")
    plan = {"shots": [], "cutaways": [{"type": "broll", "query": "a phone", "file": "footage/pexels_1_phone.mp4"},
                                      {"type": "broll", "file": str(d / "footage" / "pixabay_3_flag.mp4")}]}
    (d / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (d / "plan_previous.json").write_text(json.dumps({"cutaways": [{"file": "footage\\wikimedia_4_prev.webm"}]}),
                                          encoding="utf-8")
    (d / "cache" / "footage_search.json").write_text(json.dumps({"k": {"at": 1, "candidates": [1] * 4000}}))
    return d


def test_plan_files_and_unused_clips():
    plan = {"cutaways": [{"file": "footage/a.mp4"}, {"file": "/x/proj/footage/b.mp4"}, {"file": "C:\\p\\footage\\c.mp4"},
                         {"file": "clips/mine.mp4"}], "shots": [{"source": 0}], "note": "my footage/ notes"}
    assert fg.plan_files(plan) == {"footage/a.mp4", "footage/b.mp4", "footage/c.mp4"}        # not a sentence
    assert fg.plan_files(None) == set()
    recs = [{"file": "footage/a.mp4", "used": False}, {"file": "footage/z.mp4", "used": True}]
    assert fg.unused_clips(recs, {"footage/a.mp4"}) == ([recs[1]], [recs[0]])
    assert fg.megabytes(2_500_000) == "2.5 MB" and fg.megabytes(4_000) == "4 kB"


def test_unused_lists_without_yes_and_deletes_only_with_it(workspace, monkeypatch, capsys):
    monkeypatch.chdir(workspace.parent)
    d = _project(workspace)
    before = sorted(p.relative_to(d).as_posix() for p in d.rglob("*"))
    cli.main(["broll", "demo", "--unused"])
    out = capsys.readouterr().out
    assert "not used by any plan: 2 clips, 1 cache files · 3.5 MB" in out
    assert "footage/pexels_2_old.mp4 · 3.5 MB · “an old phone”" in out
    assert "footage/pexels_5_gone.mp4 · 0 kB (file already gone: only its record)" in out
    assert "cache/footage_search.json" in out and "Nothing was deleted" in out
    assert "Kept: 2 clips marked unused that a plan still names" in out
    assert sorted(p.relative_to(d).as_posix() for p in d.rglob("*")) == before          # nothing changed

    cli.main(["tomas", "demo", "--sin-usar", "--si"])                                   # Spanish aliases
    out = capsys.readouterr().out
    assert "B-roll deleted: 2 clips, 1 cache files" in out
    left = sorted(p.name for p in (d / "footage").iterdir())
    assert left == ["credits.json", "mine.mp4", "pexels_1_phone.mp4", "pixabay_3_flag.mp4", "wikimedia_4_prev.webm"]
    records = json.loads((d / "footage" / "credits.json").read_text(encoding="utf-8"))
    assert [r["file"] for r in records] == ["footage/pexels_1_phone.mp4", "footage/pixabay_3_flag.mp4",
                                            "footage/wikimedia_4_prev.webm"]
    assert not (d / "cache" / "footage_search.json").exists()
    assert sorted(p.name for p in (d / "cache" / "footage_frames").iterdir()) == [
        "pexels_1_phone.jpg", "pixabay_3_flag.jpg", "wikimedia_4_prev.jpg"]

    cli.main(["broll", "demo", "--unused", "--yes"])                                    # again: nothing left
    assert "every clip in footage/ is used by the plan" in capsys.readouterr().out


def test_yes_alone_is_refused(workspace, monkeypatch):
    monkeypatch.chdir(workspace.parent)
    _project(workspace)
    with pytest.raises(SystemExit, match="--yes only goes with --unused"):
        cli.main(["broll", "demo", "--yes"])
