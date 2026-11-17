"""`sve check` without --version: the most recently rendered version, draft and variants included."""
from __future__ import annotations

import json
import os

import pytest

from social_video_editor.adapters.files.projects import YamlProjectRepository


def _version(folder, name, when, video=True):
    d = folder / name
    d.mkdir()
    (d / "summary.json").write_text(json.dumps({"video": "demo.mp4"}), encoding="utf-8")
    if video:
        (d / "demo.mp4").write_bytes(b"")
    os.utime(d / "summary.json", (when, when))
    return d


def test_latest_rendered_includes_drafts(tmp_path, recipes):
    repo, d = YamlProjectRepository(recipes), tmp_path / "demo"
    d.mkdir()
    assert repo.latest_rendered(d) is None
    with pytest.raises(SystemExit, match="Version folder not found"):
        repo.load_version(d)
    _version(d, "v1Borrador", 100)                      # only drafts: the old code looked for v1 and failed
    _version(d, "v1Borrador_2", 200)
    assert repo.load_version(d)["name"] == "v1Borrador_2"
    _version(d, "v1", 300)                              # a final render after the drafts
    assert repo.latest_rendered(d).name == "v1"
    _version(d, "v1SinMusica", 400)                     # its variant, rendered later
    assert repo.latest_rendered(d).name == "v1SinMusica"
    _version(d, "v2_fallida", 900)                      # a failed render never counts
    _version(d, "v2Draft_failed", 901)
    assert repo.latest_rendered(d).name == "v1SinMusica"
    _version(d, "v2", 500, video=False)                 # no video in it: a folder with one wins
    assert repo.latest_rendered(d).name == "v1SinMusica"
    _version(d, "v10Draft", 50)                         # the number orders first (copies keep old dates)
    assert repo.latest_rendered(d).name == "v10Draft"
    assert repo.load_version(d, "v1")["name"] == "v1"   # an explicit version is still obeyed


def test_cli_says_which_version_it_checked(monkeypatch, capsys):
    from social_video_editor import cli
    from social_video_editor.application import check

    result = {"version": "v1Borrador", "ok": True, "errors": [], "warnings": [], "metrics": {}, "listen": []}
    monkeypatch.setattr(check, "check_version", lambda svc, project, version, out: (result, []))
    monkeypatch.setattr(cli, "build_services", lambda: None)
    cli.main(["verificar", "demo"])
    out = capsys.readouterr().out
    assert "latest rendered version: v1Borrador" in out and "Check v1Borrador: OK" in out
    cli.main(["check", "demo", "--version", "v1Borrador"])
    assert "latest rendered" not in capsys.readouterr().out
