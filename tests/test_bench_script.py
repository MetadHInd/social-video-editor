"""scripts/bench.py: argument parsing, statistics, the hand-written plan and the --compare table.

Nothing is rendered and FFmpeg is never called: the script is imported as a module."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(scope="module")
def bench():
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("sve_bench_script", SCRIPTS / "bench.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.path.remove(str(SCRIPTS))


def result(rev: str, wall: float, render: float, rss: float | None = None, extra: dict | None = None) -> dict:
    stats = {"wall_sec": {"median": wall, "min": wall, "max": wall, "n": 1},
             "render_time_sec": {"median": render, "min": render, "max": render, "n": 1}}
    if rss is not None:
        stats["max_rss_mb"] = {"median": rss, "min": rss, "max": rss, "n": 1}
    return {"env": {"git_rev": rev, "git_dirty": False},
            "scenarios": {"vertical60": {"stats": stats}, **(extra or {})}}


def test_defaults(bench, tmp_path):
    a = bench.parse_args([])
    assert (a.scenario, a.quick, a.repeat, a.warmup, a.draft, a.compare) == ("all", False, 1, False, False, None)
    assert a.media_dir == bench.default_media_dir()
    assert bench.scenario_keys(a) == ["vertical60", "slideshow600"]


def test_options(bench, tmp_path):
    a = bench.parse_args(["--scenario", "slideshow600", "--quick", "--draft", "--repeat", "3", "--warmup",
                          "--media-dir", str(tmp_path / "m"), "--workdir", str(tmp_path / "w"),
                          "--out", str(tmp_path / "r.json")])
    assert (a.repeat, a.warmup, a.media_dir, a.workdir, a.out) == (
        3, True, tmp_path / "m", tmp_path / "w", tmp_path / "r.json")
    assert bench.scenario_keys(a) == ["slideshow600-quick-draft"]
    assert bench.scenario_keys(bench.parse_args(["--quick"])) == ["vertical60-quick", "slideshow600-quick"]
    a = bench.parse_args(["--compare", "a.json", "b.json"])
    assert a.compare == [Path("a.json"), Path("b.json")]


@pytest.mark.parametrize("argv", [["--scenario", "nope"], ["--repeat", "0"], ["--compare", "only_one.json"]])
def test_bad_arguments(bench, argv, capsys):
    with pytest.raises(SystemExit):
        bench.parse_args(argv)


def test_every_scenario_key_has_parameters(bench):
    for name in bench.SCENARIOS:
        assert name in bench.PARAMS and f"{name}-quick" in bench.PARAMS


def test_summarize_median_min_max(bench):
    runs = [{"wall_sec": 10.0, "cpu_sec": 30.0}, {"wall_sec": 14.0, "cpu_sec": None}, {"wall_sec": 11.0}]
    s = bench.summarize(runs)
    assert s["wall_sec"] == {"median": 11.0, "min": 10.0, "max": 14.0, "n": 3}
    assert s["cpu_sec"]["n"] == 1 and "max_rss_mb" not in s


def test_compare_table(bench):
    a = result("abc1234", wall=100.0, render=98.0, rss=512.0)
    b = result("def5678", wall=80.0, render=80.36,
               extra={"slideshow600": {"stats": {"wall_sec": {"median": 300.0}}}})
    b["env"]["git_dirty"] = True
    table = bench.compare_table(a, b, "a.json", "b.json")
    lines = table.splitlines()
    assert lines[0] == "A = a.json (git abc1234)"
    assert lines[1] == "B = b.json (git def5678+dirty)"
    assert "| scenario | metric | A | B | change |" in lines
    assert "| vertical60 | wall_sec | 100.00 | 80.00 | -20.0 % |" in lines
    assert "| vertical60 | render_time_sec | 98.00 | 80.36 | -18.0 % |" in lines
    assert "| vertical60 | max_rss_mb | 512.00 | n/a | n/a |" in lines          # missing in B
    assert "| slideshow600 | wall_sec | n/a | 300.00 | n/a |" in lines         # only in B
    assert not any("cpu_sec" in line for line in lines)                     # in neither: no row


def test_compare_cli(bench, tmp_path, capsys):
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(result("aaa", 50.0, 49.0)), encoding="utf-8")
    pb.write_text(json.dumps(result("bbb", 55.0, 53.9)), encoding="utf-8")
    assert bench.main(["--compare", str(pa), str(pb)]) == 0
    out = capsys.readouterr().out
    assert "| vertical60 | wall_sec | 50.00 | 55.00 | +10.0 % |" in out


def test_vertical_plan_shape(bench):
    p = bench.PARAMS["vertical60"]
    plan = bench.vertical_plan(p["clips"]["count"], p["clips"]["dur"], **p["shots"])
    shots = plan["shots"]
    videos = [s for s in shots if s.get("type", "video") == "video"]
    assert len(shots) == 20 and len(videos) == 18
    assert sum(s["beats"] for s in shots) * 60 / bench.BPM == pytest.approx(60.0)
    assert {s.get("type") for s in shots} >= {"polaroid", "collage"}
    fx = [e["type"] for s in videos for e in s.get("effects", [])]
    assert fx.count("zoom_punch") == 1 and fx.count("beat_pulse") == 6
    # every window fits its clip and no two video shots share one (each renders its own segment)
    assert all(0 <= s["start"] < s["end"] <= p["clips"]["dur"] for s in videos)
    assert len({(s["source"], s["start"]) for s in videos}) == len(videos)
    # drawn shots only reference earlier video shots
    for i, s in enumerate(shots):
        refs = [s["from_shot"]] if s.get("type") == "polaroid" else s.get("from_shots", [])
        assert all(r < i and shots[r].get("type", "video") == "video" for r in refs)


def test_clean_project_refuses_unmarked_workspace(bench, tmp_path):
    project = tmp_path / "projects" / "p"
    (project / "v1").mkdir(parents=True)
    with pytest.raises(bench.BenchError):
        bench.clean_project(tmp_path, project)
    assert (project / "v1").is_dir()
    (tmp_path / bench.MARKER).write_text("x", encoding="utf-8")
    (project / "cache" / "segments").mkdir(parents=True)
    (project / "cache" / "music.json").write_text("{}", encoding="utf-8")
    (project / "v2Draft").mkdir()
    (project / "plan.json").write_text("{}", encoding="utf-8")
    bench.clean_project(tmp_path, project)
    assert sorted(p.name for p in project.iterdir()) == ["cache", "plan.json"]
    assert [p.name for p in (project / "cache").iterdir()] == ["music.json"]


def test_compare_rejects_files_that_are_not_results(bench, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"something": 1}', encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps(result("aaa", 1.0, 1.0)), encoding="utf-8")
    with pytest.raises(SystemExit, match="not a bench results file"):
        bench.main(["--compare", str(bad), str(good)])
    with pytest.raises(SystemExit, match="cannot read"):
        bench.main(["--compare", str(tmp_path / "missing.json"), str(good)])


def test_log_patterns(bench):
    m = bench.VIEW_RE.search("bench_vertical — recipe 'base' — 20 shots — 60.1 s")
    assert m and (m.group(1), m.group(2)) == ("20", "60.1")
    m = bench.LINE_RE.match("[sve 18:26:24] Timeline: 6 shots, 6.3 s, 1080×1920")
    assert m and m.group(1).startswith("Timeline:")
