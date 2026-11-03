"""Hardware acceleration: $SVE_HWACCEL parsing, per-platform auto order, detection (listing + test encode,
with FFmpeg's answers mocked: no GPU anywhere in CI), command arguments per backend, runtime fallback to
software and the switch-off after the first failure, and the pre-0.5 function names."""
from __future__ import annotations

import subprocess

import pytest

from social_video_editor.adapters.system import hwaccel

HWACCELS = "Hardware acceleration methods:\nvdpau\ncuda\nvaapi\nqsv\ndrm\nvideotoolbox\n"
ENCODERS = ("Encoders:\n V..... = Video\n ------\n"
            " V....D libx264              libx264 H.264 / AVC (codec h264)\n"
            " V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n"
            " V..... h264_qsv             H.264 (Intel Quick Sync Video acceleration) (codec h264)\n"
            " V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)\n"
            " V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n")


@pytest.fixture(autouse=True)
def fresh_detection(monkeypatch):
    """Every test detects again, and no fake backend leaks into the tests that render for real."""
    monkeypatch.delenv("SVE_HWACCEL", raising=False)
    monkeypatch.delenv("SVE_VAAPI_DEVICE", raising=False)
    hwaccel.reset()
    yield
    hwaccel.reset()


def fake_ffmpeg(monkeypatch, working=(), hwaccels=HWACCELS, encoders=ENCODERS):
    """Mocks subprocess.run for hwaccel: the listings above, and a test encode that succeeds only for the
    encoders in `working`. Returns the list of commands that were run."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        assert kw.get("timeout") and kw.get("encoding") == "utf-8"
        if "-hwaccels" in cmd:
            return subprocess.CompletedProcess(cmd, 0, hwaccels, "")
        if "-encoders" in cmd:
            return subprocess.CompletedProcess(cmd, 0, encoders, "")
        enc = cmd[cmd.index("-c:v") + 1]
        if enc in working:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", f"Device creation failed for {enc}\nmore\n")

    monkeypatch.setattr(hwaccel.subprocess, "run", fake_run)
    return calls


def probed(calls) -> list[str]:
    return [c[c.index("-c:v") + 1] for c in calls if "-c:v" in c]


# ── setting and order ────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, expected", [(None, "auto"), ("", "auto"), ("auto", "auto"), (" AUTO ", "auto"),
                                             ("0", None), ("off", None), ("None", None), ("no", None),
                                             ("false", None), ("vaapi", "vaapi"), ("NVENC", "nvenc"),
                                             ("qsv", "qsv"), ("videotoolbox", "videotoolbox")])
def test_parse_setting(value, expected):
    assert hwaccel.parse_setting(value) == expected


def test_unknown_setting_warns_and_means_auto(capsys):
    assert hwaccel.parse_setting("cuda-please") == "auto"
    assert "SVE_HWACCEL='cuda-please'" in capsys.readouterr().out


def test_auto_order_per_platform(monkeypatch):
    assert hwaccel.auto_order("darwin") == ["videotoolbox"]
    assert hwaccel.auto_order("linux") == ["nvenc", "vaapi", "qsv"]
    assert hwaccel.auto_order("win32") == ["nvenc", "qsv"]
    assert hwaccel.auto_order("freebsd14") == []
    monkeypatch.setattr(hwaccel.sys, "platform", "win32")
    assert hwaccel.auto_order() == ["nvenc", "qsv"]


def test_listed_matches_exact_names():
    vaapi = hwaccel.BACKENDS["vaapi"]
    assert hwaccel.listed(vaapi, HWACCELS, ENCODERS)
    assert not hwaccel.listed(vaapi, "Hardware acceleration methods:\ncuda\n", ENCODERS)
    assert not hwaccel.listed(vaapi, HWACCELS, " V....D hevc_vaapi   H.265 (VAAPI)\n")    # only HEVC
    assert not hwaccel.listed(hwaccel.BACKENDS["nvenc"], HWACCELS, " V....D h264_nvenc_x  fake\n")
    assert not hwaccel.listed(vaapi, "", "")


# ── detection ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("platform, working, expected, tried", [
    ("linux", {"h264_nvenc", "h264_vaapi"}, "nvenc", ["h264_nvenc"]),
    ("linux", {"h264_vaapi", "h264_qsv"}, "vaapi", ["h264_nvenc", "h264_vaapi"]),
    ("linux", {"h264_qsv"}, "qsv", ["h264_nvenc", "h264_vaapi", "h264_qsv"]),
    ("linux", set(), None, ["h264_nvenc", "h264_vaapi", "h264_qsv"]),      # listed, but no device
    ("win32", {"h264_qsv", "h264_vaapi"}, "qsv", ["h264_nvenc", "h264_qsv"]),
    ("darwin", {"h264_videotoolbox"}, "videotoolbox", ["h264_videotoolbox"]),
    ("darwin", set(), None, ["h264_videotoolbox"]),
])
def test_auto_detection_follows_platform_order(monkeypatch, platform, working, expected, tried):
    monkeypatch.setattr(hwaccel.sys, "platform", platform)
    calls = fake_ffmpeg(monkeypatch, working)
    b = hwaccel.backend()
    assert (b.name if b else None) == expected
    assert probed(calls) == tried
    hwaccel.backend()
    assert len(calls) == 2 + len(tried)                    # detected once per process (cached)


def test_unlisted_backends_are_not_probed(monkeypatch):
    monkeypatch.setattr(hwaccel.sys, "platform", "linux")
    calls = fake_ffmpeg(monkeypatch, {"h264_qsv"}, hwaccels="Hardware acceleration methods:\nqsv\n")
    assert hwaccel.backend().name == "qsv"
    assert probed(calls) == ["h264_qsv"]


@pytest.mark.parametrize("value", ["0", "off", "none", "no", "false"])
def test_disabled_setting_never_runs_ffmpeg(monkeypatch, value):
    monkeypatch.setattr(hwaccel.sys, "platform", "linux")
    monkeypatch.setenv("SVE_HWACCEL", value)
    calls = fake_ffmpeg(monkeypatch, {"h264_nvenc", "h264_vaapi", "h264_qsv"})
    assert hwaccel.backend() is None
    assert hwaccel.active_backend() is None
    assert calls == []


def test_explicit_backend_is_the_only_one_tried(monkeypatch, capsys):
    monkeypatch.setattr(hwaccel.sys, "platform", "linux")
    monkeypatch.setenv("SVE_HWACCEL", "qsv")
    calls = fake_ffmpeg(monkeypatch, {"h264_nvenc", "h264_qsv"})
    assert hwaccel.backend().name == "qsv"
    assert probed(calls) == ["h264_qsv"]
    # VideoToolbox can be forced elsewhere too (it just fails the probe off macOS)
    hwaccel.reset()
    monkeypatch.setenv("SVE_HWACCEL", "videotoolbox")
    calls = fake_ffmpeg(monkeypatch, set())
    assert hwaccel.backend() is None
    assert probed(calls) == ["h264_videotoolbox"]
    out = capsys.readouterr().out
    assert out.count("SVE_HWACCEL=videotoolbox") == 1 and "Device creation failed" in out


def test_explicit_backend_not_built_in_warns_once(monkeypatch, capsys):
    monkeypatch.setenv("SVE_HWACCEL", "nvenc")
    calls = fake_ffmpeg(monkeypatch, {"h264_nvenc"}, encoders=" V....D libx264   libx264\n")
    assert hwaccel.backend() is None and hwaccel.backend() is None
    assert probed(calls) == []
    assert capsys.readouterr().out.count("no cuda hwaccel / h264_nvenc encoder") == 1


def test_ffmpeg_missing_means_software(monkeypatch):
    def broken(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(hwaccel.subprocess, "run", broken)
    monkeypatch.setattr(hwaccel.sys, "platform", "linux")
    assert hwaccel.backend() is None


def test_probe_timeout_means_unusable(monkeypatch):
    def slow(cmd, **kw):
        if "-hwaccels" in cmd or "-encoders" in cmd:
            return subprocess.CompletedProcess(cmd, 0, HWACCELS if "-hwaccels" in cmd else ENCODERS, "")
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(hwaccel.subprocess, "run", slow)
    monkeypatch.setattr(hwaccel.sys, "platform", "win32")
    assert hwaccel.backend() is None


# ── command arguments ────────────────────────────────────────────────────────
def test_probe_commands_use_the_real_device():
    cmds = {n: hwaccel.probe_command(b) for n, b in hwaccel.BACKENDS.items()}
    tail = ["-f", "lavfi", "-i", "color=black:s=256x256:d=0.1"]
    vaapi = cmds["vaapi"]
    assert vaapi[vaapi.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert vaapi[vaapi.index("-vf") + 1] == "format=nv12,hwupload"
    assert cmds["qsv"][cmds["qsv"].index("-vf") + 1] == "format=nv12"
    assert "-init_hw_device" in cmds["qsv"]
    for name, cmd in cmds.items():
        assert cmd[-7:] == ["-c:v", hwaccel.BACKENDS[name].encoder, "-frames:v", "1", "-f", "null", "-"]
        assert all(x in cmd for x in tail) and "-loglevel" in cmd


def test_backend_arguments(monkeypatch):
    vt, nv, va, qs = (hwaccel.BACKENDS[n] for n in ("videotoolbox", "nvenc", "vaapi", "qsv"))
    assert [b.decode_args() for b in (vt, nv, va, qs)] == [["-hwaccel", "videotoolbox"], ["-hwaccel", "cuda"],
                                                           ["-hwaccel", "vaapi"], ["-hwaccel", "qsv"]]
    assert vt.device_args() == nv.device_args() == []
    assert va.device_args() == ["-vaapi_device", "/dev/dri/renderD128"]
    monkeypatch.setenv("SVE_VAAPI_DEVICE", "/dev/dri/renderD129")
    assert va.device_args() == ["-vaapi_device", "/dev/dri/renderD129"]
    assert qs.device_args() == ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"]
    assert [b.upload_filter() for b in (vt, nv, va, qs)] == ["", "", "format=nv12,hwupload", "format=nv12"]
    # proxies: a bitrate
    assert va.encode_args(bitrate="3M") == ["-c:v", "h264_vaapi", "-b:v", "3M"]
    assert nv.encode_args(bitrate="3M") == ["-c:v", "h264_nvenc", "-b:v", "3M"]
    # drafts: constant quality on each encoder's own scale
    assert vt.encode_args(quality=22) == ["-c:v", "h264_videotoolbox", "-q:v", "56"]
    assert va.encode_args(quality=22) == ["-c:v", "h264_vaapi", "-qp", "22"]
    assert nv.encode_args(quality=22) == ["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "22", "-b:v", "0"]
    assert qs.encode_args(quality=22) == ["-c:v", "h264_qsv", "-global_quality", "22"]
    assert vt.encode_args(quality=80)[-1] == "1" and vt.encode_args(quality=-10)[-1] == "100"   # clamped


def test_module_helpers_accept_backend_true_or_false():
    va = hwaccel.BACKENDS["vaapi"]
    assert hwaccel.with_upload("scale=2:2", va) == "scale=2:2,format=nv12,hwupload"
    assert hwaccel.with_upload("scale=2:2", False) == "scale=2:2"
    assert hwaccel.with_upload("", va) == "format=nv12,hwupload"
    assert hwaccel.device_args(None) == [] and hwaccel.draft_encoder_args(False) == []
    assert hwaccel.final_decoder(va) is None
    assert hwaccel.final_decoder(hwaccel.BACKENDS["videotoolbox"]).name == "videotoolbox"
    assert hwaccel.final_decoder(False) is None


def test_old_api_names_keep_their_meaning(monkeypatch):
    """Pre-0.5 callers: hw=True meant VideoToolbox, hw=False software."""
    assert hwaccel.decode_args(True) == ["-hwaccel", "videotoolbox"]
    assert hwaccel.decode_args(False) == []
    assert hwaccel.proxy_encoder_args(True, "3M") == ["-c:v", "h264_videotoolbox", "-b:v", "3M"]
    assert hwaccel.proxy_encoder_args(False, "3M") == ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "3M"]
    assert hwaccel.proxy_encoder_args(False) == ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "3M"]
    monkeypatch.setattr(hwaccel.sys, "platform", "darwin")
    fake_ffmpeg(monkeypatch, {"h264_videotoolbox"})
    assert hwaccel.videotoolbox_available()
    hwaccel.reset()
    monkeypatch.setattr(hwaccel.sys, "platform", "linux")
    fake_ffmpeg(monkeypatch, {"h264_vaapi"})
    assert not hwaccel.videotoolbox_available()          # a backend, but not VideoToolbox
    hwaccel.reset()
    monkeypatch.setenv("SVE_HWACCEL", "0")
    assert not hwaccel.videotoolbox_available()


# ── runtime fallback ─────────────────────────────────────────────────────────
def use(monkeypatch, name):
    """Detection ends with backend `name` (None: software)."""
    monkeypatch.setenv("SVE_HWACCEL", name or "0")
    fake_ffmpeg(monkeypatch, {hwaccel.BACKENDS[name].encoder} if name else set())
    assert hwaccel.backend() is (hwaccel.BACKENDS[name] if name else None)


def build(hw):
    return ["ffmpeg", *hwaccel.decode_args(hw), "-i", "in.mp4", *hwaccel.proxy_encoder_args(hw), "out.mp4"]


def test_fallback_to_software_and_switch_off_after_first_failure(monkeypatch, capsys):
    use(monkeypatch, "vaapi")
    ran = []

    def run(cmd):
        ran.append(cmd)
        if "h264_vaapi" in cmd:
            raise RuntimeError("driver crashed")
        return "ok"

    assert hwaccel.run_with_fallback(build, run) == "ok"
    assert [("h264_vaapi" in c) for c in ran] == [True, False]
    assert hwaccel.active_backend() is None
    # the next commands go straight to software, and the message appears once
    assert hwaccel.run_with_fallback(build, run) == "ok"
    assert [("h264_vaapi" in c) for c in ran] == [True, False, False]
    out = capsys.readouterr().out
    assert out.count("Hardware acceleration (vaapi) failed; using software for the rest of this run") == 1
    hwaccel.reset()
    assert hwaccel.active_backend().name == "vaapi"


def test_working_hardware_runs_once(monkeypatch):
    use(monkeypatch, "nvenc")
    ran = []
    assert hwaccel.run_with_fallback(build, lambda c: ran.append(c) or "done") == "done"
    assert len(ran) == 1 and "h264_nvenc" in ran[0] and ran[0][1:3] == ["-hwaccel", "cuda"]


def test_without_hardware_path_runs_software_once(monkeypatch):
    """A caller without a hardware path for the backend (final shots on VAAPI) builds the same command:
    it runs once, and a failure is not blamed on the hardware."""
    use(monkeypatch, "vaapi")
    ran = []

    def same(hw):
        return ["ffmpeg", *hwaccel.decode_args(hwaccel.final_decoder(hw)), "-i", "in.mp4", "out.mkv"]

    def fail(cmd):
        ran.append(cmd)
        raise RuntimeError("bad input")

    with pytest.raises(RuntimeError, match="bad input"):
        hwaccel.run_with_fallback(same, fail)
    assert len(ran) == 1 and "-hwaccel" not in ran[0]
    assert hwaccel.active_backend().name == "vaapi"


def test_software_failure_after_hardware_failure_propagates(monkeypatch):
    use(monkeypatch, "qsv")

    def run(cmd):
        raise RuntimeError("fails everywhere")

    with pytest.raises(RuntimeError, match="fails everywhere"):
        hwaccel.run_with_fallback(build, run)


def test_no_backend_runs_software(monkeypatch):
    use(monkeypatch, None)
    ran = []
    hwaccel.run_with_fallback(build, ran.append)
    assert ran == [build(False)]


# ── wiring in the FFmpeg encoder ─────────────────────────────────────────────
def _commands(monkeypatch, tmp_path, name, call):
    """(hardware command, software command) the encoder builds with backend `name` (nothing runs)."""
    from social_video_editor.adapters.ffmpeg import encoder as enc_mod

    for f in ("prescale", "sdr_chain", "correction_chain", "motion", "flash_filters"):
        monkeypatch.setattr(enc_mod.filters, f, lambda *a, **k: "")
    monkeypatch.setattr(enc_mod, "ffmpeg_version", lambda: (8, 1))
    built = []

    def capture(build, run):
        built.append(([str(x) for x in build(hwaccel.BACKENDS[name])], [str(x) for x in build(False)]))
        raise RuntimeError("stop: captured")

    monkeypatch.setattr(enc_mod, "run_with_fallback", capture)
    with pytest.raises(RuntimeError, match="captured"):
        call(enc_mod.FfmpegEncoder())
    return built[0]


def _opt(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


INFO = {"path": "clip.mp4", "has_audio": True, "audio_index": 1, "width": 1920, "height": 1080, "duration": 3.0}
SHOT = {"start": 0.5, "end": 2.5, "dur": 2.0, "index": 0}


def test_proxy_uses_the_backend(monkeypatch, tmp_path):
    hw, soft = _commands(monkeypatch, tmp_path, "vaapi",
                         lambda e: e.make_proxy(INFO, tmp_path / "p.mp4", 0.62))
    assert hw[1:5] == ["-vaapi_device", "/dev/dri/renderD128", "-hwaccel", "vaapi"]
    assert _opt(hw, "-vf").endswith(",fps=30,format=nv12,hwupload")
    assert _opt(hw, "-c:v") == "h264_vaapi" and _opt(hw, "-b:v") == "3M"
    assert "-hwaccel" not in soft and _opt(soft, "-c:v") == "libx264" and _opt(soft, "-vf").endswith(",fps=30")


@pytest.mark.parametrize("name, encoder, upload", [("nvenc", "h264_nvenc", ""), ("qsv", "h264_qsv", ",format=nv12"),
                                                   ("vaapi", "h264_vaapi", ",format=nv12,hwupload"),
                                                   ("videotoolbox", "h264_videotoolbox", "")])
def test_draft_shots_use_the_hardware_encoder(monkeypatch, tmp_path, cfg, name, encoder, upload):
    hw, soft = _commands(monkeypatch, tmp_path, name, lambda e: e.encode_shot(
        SHOT, INFO, cfg, {}, False, 540, 960, True, tmp_path / "s.mkv"))
    assert _opt(hw, "-c:v") == encoder and "-pix_fmt" not in hw and "-preset" not in hw
    assert _opt(hw, "-hwaccel") == hwaccel.BACKENDS[name].hwaccel
    assert hw.index("-hwaccel") < hw.index("-i")
    assert _opt(hw, "-vf") == _opt(soft, "-vf") + upload
    assert _opt(soft, "-c:v") == "libx264" and _opt(soft, "-crf") == "22" and _opt(soft, "-preset") == "ultrafast"


@pytest.mark.parametrize("name", ["nvenc", "vaapi", "qsv"])
def test_final_shots_stay_software_off_macos(monkeypatch, tmp_path, cfg, name):
    hw, soft = _commands(monkeypatch, tmp_path, name, lambda e: e.encode_shot(
        SHOT, INFO, cfg, {}, False, 540, 960, False, tmp_path / "s.mkv"))
    assert hw == soft                                   # → run_with_fallback runs it once, in software
    assert _opt(soft, "-c:v") == "libx264" and _opt(soft, "-crf") == "12" and "-hwaccel" not in soft


def test_final_shots_keep_videotoolbox_decode_and_libx264(monkeypatch, tmp_path, cfg):
    hw, soft = _commands(monkeypatch, tmp_path, "videotoolbox", lambda e: e.encode_shot(
        SHOT, INFO, cfg, {}, False, 540, 960, False, tmp_path / "s.mkv"))
    assert hw[1:3] == ["-hwaccel", "videotoolbox"] and "-hwaccel" not in soft
    assert _opt(hw, "-c:v") == "libx264" and _opt(hw, "-crf") == "12" and _opt(hw, "-pix_fmt") == "yuv420p"
    assert _opt(hw, "-vf") == _opt(soft, "-vf")
