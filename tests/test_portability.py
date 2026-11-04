"""Portability (Linux, macOS, Windows): font lookup in nested and Windows font folders, missing or grey
emoji fonts, user cache folders, console encoding, torch devices, failed-render folders, recipe
descriptions, FFmpeg version detection and the decoder-thread cap of shot segments (FFmpeg 7.0 - 8.0)."""
from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from social_video_editor.adapters.files.recipes import header_description
from social_video_editor.adapters.pillow import rich_text as rt
from social_video_editor.adapters.system import fonts


def _clear_caches():
    for cached in (fonts._index, fonts.label_font, fonts.emoji_font, rt._emoji_font, rt._emoji_color_ok,
                   rt._emoji_image):
        cached.cache_clear()


def _fake_linux_fonts(tmp_path, monkeypatch, files):
    """Font folders that contain only `files` (paths relative to a fake /usr/share/fonts)."""
    root = tmp_path / "share_fonts"
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"")
    monkeypatch.setattr(fonts, "FONT_DIRS", [root])
    monkeypatch.setattr(fonts, "LINUX_FONT_DIRS", [root])
    _clear_caches()
    monkeypatch.chdir(tmp_path)
    return root


def test_fonts_in_linux_subfolders_and_fallbacks(tmp_path, monkeypatch):
    root = _fake_linux_fonts(tmp_path, monkeypatch, ["opentype/inter/InterDisplay-Black.otf",
                                                     "truetype/dejavu/DejaVuSans-Bold.ttf",
                                                     "truetype/noto/NotoColorEmoji.ttf"])
    try:
        assert fonts.find_font_file("InterDisplay-Black") == (root / "opentype/inter/InterDisplay-Black.otf", 0)
        assert fonts.find_font_file("interdisplay-black:1")[1] == 1                       # case-insensitive
        # missing (macOS-only) fonts fall back to an installed bold sans-serif
        assert fonts.find_font_file("Bradley Hand Bold") == (root / "truetype/dejavu/DejaVuSans-Bold.ttf", 0)
        assert fonts.label_font() == root / "truetype/dejavu/DejaVuSans-Bold.ttf"
        assert fonts.emoji_font() == root / "truetype/noto/NotoColorEmoji.ttf"
    finally:
        _clear_caches()


def test_without_any_font_text_and_emoji_still_render(tmp_path, monkeypatch):
    _fake_linux_fonts(tmp_path, monkeypatch, [])
    try:
        assert fonts.find_font_file("Inter-Black") == (None, 0)
        assert fonts.emoji_font() is None
        im = rt._emoji_image("👀", 40)                         # no emoji font: an empty square, no crash
        assert im.size == (40, 40) and im.getbbox() is None
    finally:
        _clear_caches()


# ── Windows (and every OS): font folders and names, caches, console, devices, failed renders ────────

def _fake_windows_fonts(tmp_path, monkeypatch, files):
    """A fake %WINDIR%/Fonts with `files`, and an empty per-user font folder."""
    env = {"WINDIR": str(tmp_path / "Windows"), "LOCALAPPDATA": str(tmp_path / "Local")}
    system = fonts.windows_font_dirs(env)[0]
    system.mkdir(parents=True)
    for f in files:
        (system / f).write_bytes(b"")
    monkeypatch.setattr(fonts, "FONT_DIRS", fonts.platform_font_dirs("win32", env))
    _clear_caches()
    monkeypatch.chdir(tmp_path)
    return system


def test_windows_font_folders_come_from_the_environment(tmp_path):
    env = {"WINDIR": str(tmp_path / "Windows"), "LOCALAPPDATA": str(tmp_path / "Local")}
    expected = [tmp_path / "Windows" / "Fonts", tmp_path / "Local" / "Microsoft" / "Windows" / "Fonts"]
    assert fonts.windows_font_dirs(env) == expected
    assert fonts.platform_font_dirs("win32", env)[:2] == expected
    assert fonts.windows_font_dirs({})[0] == Path("C:\\Windows") / "Fonts"
    assert fonts.platform_font_dirs("linux", env) == fonts.MACOS_FONT_DIRS + fonts.LINUX_FONT_DIRS
    assert fonts.platform_font_dirs("darwin", env) == fonts.MACOS_FONT_DIRS + fonts.LINUX_FONT_DIRS


def test_windows_short_font_file_names_and_fallbacks(tmp_path, monkeypatch):
    system = _fake_windows_fonts(tmp_path, monkeypatch, ["ariblk.ttf", "arialbd.ttf", "SEGOEUIB.TTF", "seguiemj.ttf"])
    try:
        assert fonts.find_font_file("Arial Black") == (system / "ariblk.ttf", 0)
        assert fonts.find_font_file("arial black:1") == (system / "ariblk.ttf", 1)
        found, face = fonts.find_font_file("Segoe UI Bold")                                # any case
        assert found.name.lower() == "segoeuib.ttf" and face == 0
        assert fonts.find_font_file("Bradley Hand Bold") == (system / "ariblk.ttf", 0)     # macOS-only font
        assert fonts.label_font() == system / "arialbd.ttf"
        assert fonts.emoji_font() == system / "seguiemj.ttf"
    finally:
        _clear_caches()


def test_font_face_index_only_from_a_trailing_number(tmp_path, monkeypatch):
    """The drive colon of a Windows path is not a face index ("C:\\x\\a.ttf" used to crash int())."""
    assert fonts.split_face(r"C:\x\a.ttf") == (r"C:\x\a.ttf", 0)
    assert fonts.split_face(r"C:\x\a.ttc:1") == (r"C:\x\a.ttc", 1)
    assert fonts.split_face("Name:2") == ("Name", 2)
    assert fonts.split_face("Name:") == ("Name:", 0)
    system = _fake_windows_fonts(tmp_path, monkeypatch, ["ariblk.ttf"])
    try:
        assert fonts.find_font_file(r"C:\x\a.ttf") == (system / "ariblk.ttf", 0)           # missing → fallback
        ttc = tmp_path / "My Fonts" / "a.ttc"
        ttc.parent.mkdir()
        ttc.write_bytes(b"")
        assert fonts.find_font_file(f"{ttc}:2") == (ttc, 2)                               # absolute path + face
        assert fonts.find_font_file(str(ttc)) == (ttc, 0)
    finally:
        _clear_caches()


def test_missing_fonts_keep_their_size_with_pillows_default_font(tmp_path, monkeypatch):
    from social_video_editor.adapters.pillow import fonts as pillow_fonts

    _fake_linux_fonts(tmp_path, monkeypatch, [])
    try:
        small, big = pillow_fonts.load_font("Inter-Black", 12), pillow_fonts.load_font("Inter-Black", 80)
        if hasattr(big, "size"):                       # Pillow ≥ 10.1: scalable default font
            assert big.getbbox("Hg")[3] > 3 * small.getbbox("Hg")[3]
    finally:
        _clear_caches()


def test_user_cache_folder_per_os(tmp_path, monkeypatch):
    from social_video_editor.adapters.system.tools import user_cache_dir

    local, xdg = tmp_path / "Local", tmp_path / "xdg"
    assert user_cache_dir({"LOCALAPPDATA": str(local)}, "win32") == local / "social_video_editor"
    assert user_cache_dir({}, "win32") == Path.home() / "AppData" / "Local" / "social_video_editor"
    assert user_cache_dir({}, "linux") == Path.home() / ".cache" / "social_video_editor"
    assert user_cache_dir({}, "darwin") == Path.home() / ".cache" / "social_video_editor"
    for platform in ("win32", "linux", "darwin"):                       # XDG_CACHE_HOME wins everywhere
        env = {"LOCALAPPDATA": str(local), "XDG_CACHE_HOME": str(xdg)}
        assert user_cache_dir(env, platform) == xdg / "social_video_editor"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "later"))       # read at call time
    assert user_cache_dir() == tmp_path / "later" / "social_video_editor"


def test_ffmpeg_lookup_uses_path_then_homebrew_only_on_macos(tmp_path, monkeypatch):
    from social_video_editor.adapters.system import tools

    brew = tmp_path / "brew"
    brew.mkdir()
    (brew / "ffmpeg").write_bytes(b"")
    monkeypatch.setattr(tools, "HOMEBREW_BIN_DIRS", (str(tmp_path / "missing"), str(brew)))
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools._find("ffmpeg", "darwin") == str(brew / "ffmpeg")
    assert tools._find("ffmpeg", "win32") == "ffmpeg"                 # bare name: the error names the tool
    assert tools._find("ffmpeg", "linux") == "ffmpeg"
    monkeypatch.setattr(tools.shutil, "which", lambda name: f"C:\\ffmpeg\\bin\\{name}.exe")
    assert tools._find("ffprobe", "win32") == "C:\\ffmpeg\\bin\\ffprobe.exe"
    assert "winget install --id Gyan.FFmpeg -e" in tools.MISSING_FFMPEG and "PATH" in tools.MISSING_FFMPEG


def _fresh(code: str, env_extra: dict, drop=("NUMBA_CACHE_DIR", "PYTHONIOENCODING", "PYTHONUTF8")):
    """Runs `code` in a new interpreter that finds this source tree; returns the CompletedProcess (bytes)."""
    import social_video_editor

    src = str(Path(social_video_editor.__file__).resolve().parents[1])
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra, PYTHONPATH=os.pathsep.join(filter(None, [src, os.environ.get("PYTHONPATH")])))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=60)


def test_log_never_crashes_on_a_legacy_console_code_page():
    code = ("from social_video_editor.log import log\n"
            "log('raw a \\u2192 b \\u26a0')\n"                      # before configure_console: still no crash
            "from social_video_editor.adapters.system.console import configure_console\n"
            "configure_console()\n"
            "log('a \\u2192 b \\u26a0')\n")
    r = _fresh(code, {"PYTHONIOENCODING": "cp1252"})
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    lines = r.stdout.decode("cp1252").splitlines()
    assert lines[0].endswith("] raw a ? b ?") and lines[1].endswith("] a ? b ?")


def test_console_on_a_windows_pipe_switches_to_utf8():
    code = ("import sys\n"
            "sys.stdout.reconfigure(encoding='cp1252')\n"         # like a redirected Windows console
            "from social_video_editor.adapters.system.console import configure_console\n"
            "configure_console(environ={}, platform='win32')\n"
            "print(sys.stdout.encoding, '\\u2192 \\u26a0 \\U0001F600')\n")
    r = _fresh(code, {})
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert r.stdout.decode("utf-8").split() == ["utf-8", "→", "⚠", "😀"]


def test_log_lines_from_threads_never_interleave(capsys):
    import threading

    from social_video_editor.log import log

    def worker(k):
        for i in range(50):
            log(f"thread {k} line {i} " + "x" * 200)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 400 and all(ln.startswith("[sve ") and ln.endswith("x" * 200) for ln in lines)


def test_numba_cache_goes_to_the_user_cache_without_creating_it(tmp_path):
    from social_video_editor.adapters.system.tools import configure_numba_cache

    env = {"XDG_CACHE_HOME": str(tmp_path / "xdg")}
    configure_numba_cache(env)
    assert env["NUMBA_CACHE_DIR"] == str(tmp_path / "xdg" / "social_video_editor" / "numba")
    env = {"NUMBA_CACHE_DIR": "elsewhere"}
    configure_numba_cache(env)
    assert env == {"NUMBA_CACHE_DIR": "elsewhere"}                      # an explicit setting is kept
    code = ("import os, sys\n"
            "from social_video_editor.adapters.system.tools import configure_numba_cache\n"
            "configure_numba_cache()\n"
            "print(os.environ['NUMBA_CACHE_DIR']); print('numba' in sys.modules)\n")
    r = _fresh(code, {"XDG_CACHE_HOME": str(tmp_path / "fresh")})
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode().splitlines()
    assert out == [str(tmp_path / "fresh" / "social_video_editor" / "numba"), "False"]
    assert not (tmp_path / "fresh").exists()


def test_emoji_left_out_when_the_font_draws_without_color(monkeypatch, capsys):
    """FreeType without support for the font's color tables draws grey glyphs: emoji are left out."""
    from PIL import Image

    _clear_caches()
    monkeypatch.setattr(rt, "_emoji_font", lambda size: (object(), size))
    try:
        for glyph in (Image.new("RGBA", (50, 50), (128, 128, 128, 255)),      # grey outline
                      Image.new("RGBA", (50, 50), (0, 0, 0, 0))):             # nothing drawn
            monkeypatch.setattr(rt, "_render", lambda u, f, s, g=glyph: g.copy())
            rt._emoji_color_ok.cache_clear()
            rt._emoji_image.cache_clear()
            im = rt._emoji_image("👀", 40)
            assert im.size == (40, 40) and im.getbbox() is None
        assert "does not draw in color" in capsys.readouterr().out

        def broken(u, f, s):
            raise OSError("no color glyphs")
        monkeypatch.setattr(rt, "_render", broken)
        rt._emoji_color_ok.cache_clear()
        rt._emoji_image.cache_clear()
        assert rt._emoji_image("👀", 40).getbbox() is None

        monkeypatch.setattr(rt, "_render", lambda u, f, s: Image.new("RGBA", (s, s), (250, 200, 20, 255)))
        rt._emoji_color_ok.cache_clear()
        rt._emoji_image.cache_clear()
        im = rt._emoji_image("👀", 40)                                      # a color glyph is drawn
        assert im.size == (40, 40) and im.getbbox() == (0, 0, 40, 40)
    finally:
        rt._emoji_color_ok.cache_clear()
        rt._emoji_image.cache_clear()


def test_installed_emoji_font_draws_in_color():
    _clear_caches()
    try:
        if fonts.emoji_font() is None:
            pytest.skip("no color emoji font installed")
        assert rt._emoji_color_ok()
        assert rt._emoji_image("😀", 40).getbbox() is not None
    finally:
        _clear_caches()


def _fake_torch(monkeypatch, cuda: bool, mps: bool):
    mps_backend = types.SimpleNamespace(is_available=lambda: mps)
    torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: cuda),
                                  backends=types.SimpleNamespace(mps=mps_backend))
    monkeypatch.setitem(sys.modules, "torch", torch)


@pytest.mark.parametrize("cuda, mps, requested, expected", [
    (True, True, "auto", "cuda"), (False, True, "auto", "mps"), (False, False, "auto", "cpu"),
    (True, False, "cuda", "cuda"), (True, False, "cuda:1", "cuda:1"), (False, True, "mps", "mps"),
    (False, True, "cuda", "cpu"), (True, False, "mps", "cpu"), (True, True, "cpu", "cpu"),
])
def test_torch_device_selection(monkeypatch, capsys, cuda, mps, requested, expected):
    from social_video_editor.adapters.clip import detector
    from social_video_editor.adapters.upscale import realesrgan

    _fake_torch(monkeypatch, cuda, mps)
    monkeypatch.setattr(realesrgan, "_warned_devices", set())
    for _ in range(2):                                                  # the warning is given once
        assert realesrgan.RealEsrganUpscaler().device({"device": requested}) == expected
        assert detector.ClipVisualDetector._device({"device": requested}) == expected
    warnings = capsys.readouterr().out.count("is not available; using the CPU")
    assert warnings == (2 if requested not in ("auto", expected) else 0)


def test_device_defaults(monkeypatch):
    """Real-ESRGAN defaults to auto; CLIP stays on the CPU by default and does not import torch for it."""
    from social_video_editor.adapters.clip import detector
    from social_video_editor.adapters.upscale import realesrgan

    monkeypatch.setitem(sys.modules, "torch", None)                     # any `import torch` would fail
    assert detector.ClipVisualDetector._device({}) == "cpu"
    _fake_torch(monkeypatch, True, False)
    assert realesrgan.RealEsrganUpscaler().device({}) == "cuda"


def test_realesrgan_model_is_looked_up_in_the_user_cache_first():
    from social_video_editor.adapters.system.tools import user_cache_dir
    from social_video_editor.adapters.upscale import realesrgan

    candidates = realesrgan.model_candidates({})
    assert candidates[0] == user_cache_dir() / "models" / realesrgan.MODEL_FILE
    assert Path.home() / ".cache" / "social-video-editor" / "models" / realesrgan.MODEL_FILE in candidates


def _version_folder(tmp_path, name="v3"):
    d = tmp_path / "project" / name
    d.mkdir(parents=True)
    (d / "render.log").write_text("diagnostics", encoding="utf-8")
    return d


def test_failed_render_folder_gets_a_free_name(tmp_path, recipes):
    from social_video_editor.adapters.files.projects import YamlProjectRepository

    repo = YamlProjectRepository(recipes)
    (tmp_path / "project" / "v3_failed").mkdir(parents=True)                     # an earlier failure
    d = _version_folder(tmp_path)
    failed = repo.mark_failed(d, "_failed")
    assert failed == tmp_path / "project" / "v3_failed_2" and (failed / "render.log").is_file()
    assert not d.exists()
    d = _version_folder(tmp_path)
    assert repo.mark_failed(d, "_failed") == tmp_path / "project" / "v3_failed_3"


def test_failed_render_folder_that_cannot_be_renamed_keeps_its_name(tmp_path, recipes, monkeypatch, capsys):
    """Windows: a file still open in the folder blocks the rename. Retried, then left alone — never raised
    from inside the render's exception handler."""
    from social_video_editor.adapters.files import projects

    repo = projects.YamlProjectRepository(recipes)
    d = _version_folder(tmp_path)
    calls, sleeps, real = [], [], os.replace

    def locked(src, dst):
        calls.append(dst)
        raise PermissionError(13, "The process cannot access the file")
    monkeypatch.setattr(projects.os, "replace", locked)
    monkeypatch.setattr(projects.time, "sleep", sleeps.append)
    assert repo.mark_failed(d, "_failed") == d and d.is_dir()
    assert len(calls) == 3 and sleeps == [0.3, 0.3]
    assert "could not rename v3 to v3_failed" in capsys.readouterr().out

    calls.clear()

    def locked_once(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise PermissionError(13, "busy")
        real(src, dst)
    monkeypatch.setattr(projects.os, "replace", locked_once)
    assert repo.mark_failed(d, "_failed") == tmp_path / "project" / "v3_failed" and len(calls) == 2


def test_files_open_elsewhere_are_left_in_place(tmp_path, recipes, monkeypatch, capsys):
    from social_video_editor.adapters.files.projects import YamlProjectRepository

    repo = YamlProjectRepository(recipes)
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "b.wav").write_bytes(b"")

    def locked(self, missing_ok=False):
        raise PermissionError(13, "in use")
    with monkeypatch.context() as m:
        m.setattr(Path, "unlink", locked)
        repo.remove(tmp_path / "a.wav")
        repo.clear_files(tmp_path, "*.wav")
    assert (tmp_path / "a.wav").exists() and (tmp_path / "b.wav").exists()
    assert capsys.readouterr().out.count("could not delete") == 3


def test_recipe_description_skips_decorative_comment_lines():
    text = "# ─────────────\n#\n#   ==== \n# BASE RECIPE: defaults\n# more\n\nlanguage: es\n"
    assert header_description(text) == "BASE RECIPE: defaults"
    assert header_description("# RECIPE: MINE\nduration: {target: 9}\n") == "RECIPE: MINE"
    assert header_description("# ────\nlanguage: es\n# RECIPE: too late\n") == ""      # only the header
    assert header_description("") == ""


def test_packaged_recipe_descriptions_are_meaningful(recipes):
    for name in recipes.available():
        desc = recipes.description(name)
        assert desc and any(ch.isalpha() for ch in desc), name
        assert "─" not in desc and not desc.startswith("#"), name
    assert recipes.description("base").startswith("BASE RECIPE")



def test_ffmpeg_version_parsing():
    from social_video_editor.adapters.system.tools import parse_ffmpeg_version as parse

    assert parse("ffmpeg version 7.1.1-1ubuntu4.2 Copyright (c) 2000-2025") == (7, 1)
    assert parse("ffmpeg version n8.1-12-gabcdef Copyright") == (8, 1)
    assert parse("ffmpeg version 8.0 Copyright") == (8, 0)
    assert parse("ffmpeg version N-118000-gabcdef Copyright") is None             # git snapshot
    assert parse("ffmpeg version 2025-01-15-git-abc-full_build") is None
    assert parse("") is None


@pytest.mark.parametrize("version, override, expected", [
    ((7, 0), None, "2"), ((7, 1), None, "2"), ((8, 0), None, "2"),      # can deadlock: capped
    ((8, 1), None, None), ((9, 0), None, None), ((6, 1), None, None),   # FFmpeg's own default
    (None, None, "2"),                                                  # unknown version: safe side
    ((7, 1), "auto", None), ((7, 1), "0", None), ((9, 0), " 6 ", "6"), ((8, 1), "1", "1"),
])
def test_segment_decoder_threads(version, override, expected):
    from social_video_editor.adapters.ffmpeg.encoder import segment_decoder_threads

    assert segment_decoder_threads(version, override) == expected


@pytest.mark.parametrize("bad", ["-1", "65", "2; rm -rf /", "two", "1.5"])
def test_segment_decoder_threads_rejects_bad_overrides(bad):
    from social_video_editor.adapters.ffmpeg.encoder import segment_decoder_threads
    from social_video_editor.domain.models import UserError

    with pytest.raises(UserError, match="SVE_DECODER_THREADS"):
        segment_decoder_threads((9, 0), bad)


def _shot_command(tmp_path, monkeypatch, cfg, version, env=None) -> list[str]:
    """The FFmpeg command encode_shot builds (filters stubbed, nothing is run)."""
    from social_video_editor.adapters.ffmpeg import encoder as enc_mod

    for name in ("prescale", "sdr_chain", "correction_chain", "motion", "flash_filters"):
        monkeypatch.setattr(enc_mod.filters, name, lambda *a, **k: "")
    monkeypatch.setattr(enc_mod, "ffmpeg_version", lambda: version)
    if env is None:
        monkeypatch.delenv("SVE_DECODER_THREADS", raising=False)
    else:
        monkeypatch.setenv("SVE_DECODER_THREADS", env)
    built = []

    def capture(build, run):
        built.append([str(x) for x in build(False)])
        raise RuntimeError("stop: command captured")

    monkeypatch.setattr(enc_mod, "run_with_fallback", capture)
    shot = {"start": 0.5, "end": 2.5, "dur": 2.0, "index": 0}
    info = {"path": "clip.mp4", "has_audio": True, "audio_index": 1}
    with pytest.raises(RuntimeError, match="captured"):
        enc_mod.FfmpegEncoder().encode_shot(shot, info, cfg, {}, False, 540, 960, True, tmp_path / "s.mkv")
    return built[0]


def _input_threads(cmd: list[str]) -> str | None:
    i = cmd.index("-i")
    return cmd[cmd.index("-threads") + 1] if "-threads" in cmd[:i] else None


def test_shot_segments_cap_decoder_threads_only_where_ffmpeg_deadlocks(tmp_path, monkeypatch, cfg):
    """Regression: FFmpeg 7.0 - 8.0 can hang forever on a shot whose source decoder runs 5+ frame threads
    while its audio chain buffers (afftdn) and pads (apad). There the shot input gets 2 threads."""
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (7, 1))) == "2"
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (8, 0))) == "2"
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (8, 1))) is None
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (8, 1), env="4")) == "4"
    assert _input_threads(_shot_command(tmp_path, monkeypatch, cfg, (7, 1), env="auto")) is None


# ── FFmpeg I/O: missing binaries, temporary files, pipes, caches (no FFmpeg needed) ──────────────

def test_missing_ffmpeg_is_a_user_error(monkeypatch, tmp_path):
    from social_video_editor.adapters.ffmpeg import probe, runner
    from social_video_editor.domain.models import UserError

    nowhere = str(tmp_path / "no-ffmpeg" / "ffmpeg")
    monkeypatch.setattr(runner, "FFMPEG", nowhere)
    with pytest.raises(UserError, match="FFmpeg"):
        runner.run(["ffmpeg", "-version"])
    with pytest.raises(UserError, match="FFmpeg"):
        runner.stderr_of(["-version"])
    monkeypatch.setattr(probe, "FFPROBE", nowhere.replace("ffmpeg", "ffprobe"))
    with pytest.raises(UserError, match="FFmpeg"):
        probe.FfprobeMediaProbe().probe(tmp_path / "clip.mp4")


def test_temporary_names_are_unique_per_thread_and_keep_the_extension(tmp_path):
    import threading

    from social_video_editor.adapters.ffmpeg.encoder import _tmp_for

    out = tmp_path / "seg_ab12.mkv"
    names = []
    threads = [threading.Thread(target=lambda: names.append(_tmp_for(out))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(names)) == 4 and len({_tmp_for(out), *names}) == 5
    assert all(p.parent == tmp_path and p.suffix == ".mkv" and p.name.startswith("seg_ab12.") for p in names)


def test_publish_retries_a_locked_file_on_windows_only(tmp_path, monkeypatch):
    from social_video_editor.adapters.ffmpeg import encoder as enc

    real_replace, calls = enc.os.replace, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) < 3:
            raise PermissionError("locked by another process")
        real_replace(src, dst)

    monkeypatch.setattr(enc.os, "replace", flaky)
    monkeypatch.setattr(enc.time, "sleep", lambda s: None)
    tmp, out = tmp_path / "a.tmp.mkv", tmp_path / "a.mkv"
    tmp.write_bytes(b"x")
    monkeypatch.setattr(enc, "_WINDOWS", False)
    with pytest.raises(PermissionError):
        enc._publish(tmp, out)
    monkeypatch.setattr(enc, "_WINDOWS", True)
    calls.clear()
    assert enc._publish(tmp, out) == out and out.read_bytes() == b"x" and not tmp.exists() and len(calls) == 3


def test_stderr_drain_prevents_the_two_pipe_deadlock():
    """The child fills its stderr pipe before reading stdin, the parent writes megabytes to stdin:
    without the drain thread both block forever (pipe buffers: 4 KiB on Windows, 64 KiB on Linux)."""
    import subprocess
    import sys
    import threading

    from social_video_editor.adapters.ffmpeg.runner import StderrDrain

    child = ("import sys; sys.stderr.buffer.write(b'e' * 300000 + 'é✓ last words'.encode()); sys.stderr.flush(); "
             "n = len(sys.stdin.buffer.read()); sys.stdout.write(str(n))")
    p = subprocess.Popen([sys.executable, "-c", child], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    watchdog = threading.Timer(60, p.kill)
    watchdog.start()
    try:
        drain = StderrDrain(p.stderr, limit=1000)
        reader = threading.Thread(target=lambda: setattr(p, "result", p.stdout.read()), daemon=True)
        reader.start()
        for _ in range(64):
            p.stdin.write(b"\0" * 65536)
        p.stdin.close()
        assert p.wait() == 0
        reader.join(10)
        assert p.result == str(64 * 65536).encode()
        text = drain.text()
        assert text.endswith("é✓ last words") and len(text.encode()) <= 1000
    finally:
        watchdog.cancel()
        p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()


def test_encode_frames_kills_an_ffmpeg_that_errored_and_stalled(tmp_path, monkeypatch):
    """FFmpeg 8.1 sometimes stays alive, reading nothing, after a fatal error: encode_frames kills it
    once it printed an error and made no progress for FRAMES_STALL_SEC, and raises that error."""
    import subprocess
    import sys
    import time

    from social_video_editor.adapters.ffmpeg import encoder as enc

    stalled = [sys.executable, "-c", "import sys, time; sys.stderr.write('fatal: encoder broke'); "
                                     "sys.stderr.flush(); time.sleep(600)"]
    monkeypatch.setattr(enc, "popen", lambda cmd, **kw: subprocess.Popen(stalled, **kw))
    monkeypatch.setattr(enc, "FRAMES_STALL_SEC", 1.0)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="fatal: encoder broke"):
        enc.FfmpegEncoder().encode_frames((bytes(1 << 20) for _ in range(50)), 50, 512, 512, 10,
                                          tmp_path / "seg.mkv")
    assert time.monotonic() - t0 < 30
    assert list(tmp_path.glob("seg*")) == []


def test_hlg_lut_generated_once_under_concurrent_calls(tmp_path, monkeypatch):
    import threading

    from social_video_editor.adapters.ffmpeg import luts

    made = []
    real = luts.hlg_to_sdr_lut
    monkeypatch.setattr(luts, "hlg_to_sdr_lut", lambda *a: made.append(1) or real(*a))
    paths: list = []
    threads = [threading.Thread(target=lambda: paths.append(luts.hlg_lut_path(size=9))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(paths)) == 1 and made == [1]
    data = paths[0].read_bytes()
    assert data.startswith(b'TITLE "HLG to SDR709"\nLUT_3D_SIZE 9\n') and b"\r" not in data
    assert [p.name for p in paths[0].parent.iterdir()] == [paths[0].name]       # no temporary file left


def test_ffmpeg_list_files_are_utf8_with_lf_line_ends(tmp_path, cfg):
    from social_video_editor.adapters.pillow.text_layer import PillowTextRenderer

    texts = [{"text": "Café ✓", "start": 0.0, "end": 0.5, "style": "label"}]
    lst, _ = PillowTextRenderer().render_layer(texts, [], cfg, 120, 200, 1.0, tmp_path / "it's a layer")
    raw = lst.read_bytes()
    assert b"\r" not in raw and raw.startswith(b"ffconcat version 1.0\nfile 'c00000.png'\n")


def test_rubberband_lookup(monkeypatch):
    import sys

    from social_video_editor.adapters.ffmpeg import automix_render as am

    monkeypatch.setattr(am.shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "platform", "win32")
    assert am.rubberband_path() is None                       # no POSIX fallback paths on Windows
    monkeypatch.setattr(am.shutil, "which", lambda name: r"C:\Tools\rubberband.exe")
    assert am.rubberband_path() == r"C:\Tools\rubberband.exe"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(am.shutil, "which", lambda name: "/usr/bin/rubberband")
    assert am.rubberband_path() == "/usr/bin/rubberband"
