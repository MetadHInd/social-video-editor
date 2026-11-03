"""Hardware acceleration (VideoToolbox, NVENC, VAAPI, Quick Sync) with automatic fallback to software.

Where hardware is used, and why only there:
- analysis proxies (540 px, thrown away after analysis): hardware decode + hardware encoder;
- draft shot segments (`--draft`, a quick preview): hardware decode + hardware encoder;
- final-quality shot segments: libx264 always (hardware H.264 encoders are visibly worse at the
  same size), and hardware decoding only with VideoToolbox, the one backend tested on real hardware
  for that path.

Selection ($SVE_HWACCEL): empty or "auto" tries the platform's backends in AUTO_ORDER; a backend name
(videotoolbox, nvenc, vaapi, qsv) tries only that one; 0/off/none/no/false forces software. A backend
is used only when FFmpeg lists its hwaccel and encoder AND a one-frame test encode succeeds: FFmpeg
builds from distributions list vaapi/cuda/qsv even on machines without the device or driver.
If a hardware command still fails at runtime, run_with_fallback retries it in software and switches
hardware off for the rest of the process, so one broken driver costs a single failed command.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from ...log import log
from .tools import FFMPEG

SETTING_ENV = "SVE_HWACCEL"
VAAPI_DEVICE_ENV = "SVE_VAAPI_DEVICE"
DEFAULT_VAAPI_DEVICE = "/dev/dri/renderD128"
DISABLED_VALUES = ("0", "no", "none", "off", "false")
AUTO = "auto"
# tried in this order; the first one that passes the test encode is used
AUTO_ORDER = {"darwin": ["videotoolbox"], "linux": ["nvenc", "vaapi", "qsv"], "win32": ["nvenc", "qsv"]}
DRAFT_QUALITY = 22          # like the libx264 draft CRF; each backend maps it to its own quality scale
PROBE_TIMEOUT = 30
LIST_TIMEOUT = 20


@dataclass(frozen=True)
class Backend:
    """One FFmpeg hardware path: the `-hwaccel` decoder and the H.264 encoder of the same device."""
    name: str           # videotoolbox | vaapi | nvenc | qsv
    hwaccel: str        # as listed by `ffmpeg -hwaccels`: videotoolbox | vaapi | cuda | qsv
    encoder: str        # h264_videotoolbox | h264_vaapi | h264_nvenc | h264_qsv

    def decode_args(self) -> list[str]:
        """Hardware decoding; frames come back to system memory, so the software filters still apply
        (and FFmpeg decodes in software by itself when the device cannot handle a codec)."""
        return ["-hwaccel", self.hwaccel]

    def device_args(self) -> list[str]:
        """Global options that open the device (before the inputs): VAAPI and QSV need an explicit
        device for uploading frames to the encoder."""
        if self.name == "vaapi":
            return ["-vaapi_device", os.environ.get(VAAPI_DEVICE_ENV) or DEFAULT_VAAPI_DEVICE]
        if self.name == "qsv":
            return ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"]
        return []

    def upload_filter(self) -> str:
        """Filter appended to the -vf chain so the encoder gets frames it accepts: h264_vaapi only
        takes VAAPI surfaces, h264_qsv wants NV12; VideoToolbox and NVENC take yuv420p as it is."""
        return {"vaapi": "format=nv12,hwupload", "qsv": "format=nv12"}.get(self.name, "")

    def encode_args(self, bitrate: str | None = None, quality: int | None = None) -> list[str]:
        """Encoder options: a target `bitrate` (proxies) or a constant `quality` on the libx264 CRF
        scale (drafts: lower = better), mapped to each encoder's own quality control."""
        args = ["-c:v", self.encoder]
        if bitrate:
            return args + ["-b:v", bitrate]
        if quality is None:
            return args
        q = int(quality)
        if self.name == "videotoolbox":             # -q:v 1-100, higher = better
            return args + ["-q:v", str(max(1, min(100, 100 - 2 * q)))]
        if self.name == "vaapi":                    # constant QP (rc_mode auto → CQP)
            return args + ["-qp", str(q)]
        if self.name == "nvenc":                    # VBR with a quality target and no bitrate cap
            return args + ["-rc", "vbr", "-cq", str(q), "-b:v", "0"]
        if self.name == "qsv":                      # intelligent constant quality
            return args + ["-global_quality", str(q)]
        return args


BACKENDS = {b.name: b for b in (Backend("videotoolbox", "videotoolbox", "h264_videotoolbox"),
                                Backend("nvenc", "cuda", "h264_nvenc"),
                                Backend("vaapi", "vaapi", "h264_vaapi"),
                                Backend("qsv", "qsv", "h264_qsv"))}

_runtime_failure = threading.Event()        # set after the first failed hardware command of this process
_failure_lock = threading.Lock()


# ── selection ────────────────────────────────────────────────────────────────
def parse_setting(value: str | None) -> str | None:
    """$SVE_HWACCEL → "auto", a backend name, or None (software only). Unknown values warn and mean auto."""
    v = (value or "").strip().lower()
    if v in ("", AUTO):
        return AUTO
    if v in DISABLED_VALUES:
        return None
    if v in BACKENDS:
        return v
    log(f"Warning: {SETTING_ENV}={value!r} is not one of auto, {', '.join(BACKENDS)} or 0; using auto")
    return AUTO


def auto_order(platform: str | None = None) -> list[str]:
    """Backends worth trying on `platform` (sys.platform by default), best first."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        platform = "linux"
    return list(AUTO_ORDER.get(platform, []))


def _names(text: str, column: int) -> set[str]:
    """Words in `column` of FFmpeg's listing (exact names: "h264_vaapi" must not match "hevc_vaapi…")."""
    out = set()
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) > column:
            out.add(parts[column])
    return out


def listed(backend: Backend, hwaccels_text: str, encoders_text: str) -> bool:
    """Whether FFmpeg was built with the backend: its hwaccel in `-hwaccels` and its encoder in `-encoders`."""
    return backend.hwaccel in _names(hwaccels_text, 0) and backend.encoder in _names(encoders_text, 1)


def _ffmpeg_listings() -> tuple[str, str]:
    """(`ffmpeg -hwaccels`, `ffmpeg -encoders`) output; empty strings when FFmpeg cannot run."""
    out = []
    for flag in ("-hwaccels", "-encoders"):
        try:
            r = subprocess.run([FFMPEG, "-hide_banner", flag], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=LIST_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return "", ""
        out.append(r.stdout or "")
    return out[0], out[1]


def probe_command(backend: Backend) -> list[str]:
    """A one-frame encode through the backend's real device (listed ≠ usable: no /dev/dri, no driver)."""
    vf = ",".join(x for x in ("format=nv12", "hwupload" if backend.name == "vaapi" else "") if x)
    return [FFMPEG, "-hide_banner", "-loglevel", "error", *backend.device_args(),
            "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1", "-vf", vf,
            "-c:v", backend.encoder, "-frames:v", "1", "-f", "null", "-"]


def _probe(backend: Backend) -> tuple[bool, str]:
    """(works, first line of FFmpeg's error) for the test encode."""
    try:
        r = subprocess.run(probe_command(backend), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    err = (r.stderr or "").strip().splitlines()
    return r.returncode == 0, (err[0] if err else f"exit {r.returncode}")


@lru_cache(maxsize=1)
def backend() -> Backend | None:
    """The hardware backend of this process (detected once), or None for software."""
    setting = parse_setting(os.environ.get(SETTING_ENV))
    if setting is None:
        return None
    explicit = setting != AUTO
    names = [setting] if explicit else auto_order()
    if not names:
        return None
    hwaccels, encoders = _ffmpeg_listings()
    for name in names:
        b = BACKENDS[name]
        if not listed(b, hwaccels, encoders):
            if explicit:
                log(f"Warning: {SETTING_ENV}={name}, but this FFmpeg has no {b.hwaccel} hwaccel / {b.encoder} "
                    "encoder; using software")
            continue
        ok, why = _probe(b)
        if ok:
            log(f"Hardware acceleration: {b.name} (analysis proxies and drafts)")
            return b
        if explicit:
            log(f"Warning: {SETTING_ENV}={name}, but a test encode with {b.encoder} failed ({why}); using software")
    return None


def active_backend() -> Backend | None:
    """backend(), unless a hardware command already failed in this process."""
    return None if _runtime_failure.is_set() else backend()


def reset() -> None:
    """Forgets the detection and any runtime failure (tests; a long-lived process after a driver change)."""
    backend.cache_clear()
    _runtime_failure.clear()


def _disable(failed: Backend) -> None:
    with _failure_lock:
        if _runtime_failure.is_set():
            return
        _runtime_failure.set()
    log(f"Hardware acceleration ({failed.name}) failed; using software for the rest of this run")


# ── command arguments (hw: a Backend, True = VideoToolbox as before, False/None = software) ──────
def _resolve(hw: Backend | bool | None) -> Backend | None:
    if isinstance(hw, Backend):
        return hw
    return BACKENDS["videotoolbox"] if hw else None


def videotoolbox_available() -> bool:
    """Whether the detected backend is VideoToolbox (kept for older callers)."""
    b = backend()
    return b is not None and b.name == "videotoolbox"


def final_decoder(hw: Backend | bool | None) -> Backend | None:
    """Hardware decoder for FINAL-quality shots: VideoToolbox only (the other backends serve proxies/drafts)."""
    b = _resolve(hw)
    return b if b is not None and b.name == "videotoolbox" else None


def decode_args(hw: Backend | bool | None) -> list[str]:
    b = _resolve(hw)
    return b.decode_args() if b else []


def device_args(hw: Backend | bool | None) -> list[str]:
    b = _resolve(hw)
    return b.device_args() if b else []


def with_upload(vf: str, hw: Backend | bool | None) -> str:
    """The -vf chain plus the backend's upload filter (unchanged in software)."""
    b = _resolve(hw)
    up = b.upload_filter() if b else ""
    return ",".join(x for x in (vf, up) if x)


def proxy_encoder_args(hw: Backend | bool | None, bitrate: str = "3M") -> list[str]:
    b = _resolve(hw)
    if b:
        return b.encode_args(bitrate=bitrate)
    return ["-c:v", "libx264", "-preset", "veryfast", "-b:v", bitrate]


def draft_encoder_args(hw: Backend | bool | None, quality: int = DRAFT_QUALITY) -> list[str]:
    """Hardware encoder for draft segments; [] in software (the caller keeps its libx264 options)."""
    b = _resolve(hw)
    return b.encode_args(quality=quality) if b else []


def run_with_fallback(build: Callable[[Backend | bool], list], run: Callable[[list], object]):
    """Runs build(backend) with the active hardware backend; on failure switches hardware off for the
    process and runs build(False). When the hardware command equals the software one (the caller has
    no hardware path for this backend, e.g. a final shot on VAAPI) it simply runs once in software."""
    hw = active_backend()
    if hw is not None:
        cmd, soft = build(hw), build(False)
        if cmd != soft:
            try:
                return run(cmd)
            except RuntimeError:
                _disable(hw)
            return run(soft)
    return run(build(False))
