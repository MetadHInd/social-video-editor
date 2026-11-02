"""Ports: the interfaces the application needs from the outside world (typing.Protocol).

Adapters in social_video_editor.adapters implement them; container.py wires them together.
All data crossing a port uses English keys (legacy Spanish files are translated by the files adapter).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from ..domain.models import Project


class MediaProbe(Protocol):
    def probe(self, path: str | Path) -> dict:
        """{"path", "duration", "has_audio", "audio_channels", "audio_index", "width", "height",
        "rotation", "fps", "codec", "pix_fmt", "transfer", "primaries", "hdr", "hlg"}"""

    def duration(self, path: str | Path) -> float: ...


class VideoEncoder(Protocol):
    def make_proxy(self, info: dict, out: Path, hdr_white: float) -> Path:
        """Small SDR copy for analysis."""

    def extract_wav(self, info: dict, out: Path) -> Path:
        """Mono 16 kHz audio for transcription / analysis."""

    def encode_shot(self, shot: dict, info: dict, cfg: dict, stats: dict, has_voice: bool,
                    W: int, H: int, draft: bool, out: Path) -> Path:
        """Renders one video shot from the ORIGINAL source (color, framing, speed, effects, audio)."""

    def encode_frames(self, frames: Iterable[bytes], n: int, W: int, H: int, fps: int, out: Path,
                      audio: dict | None = None) -> Path:
        """Encodes raw RGB frames as a segment like the other shots. Audio: silent, or with `audio`
        {"info", "start", "shot", "voice", "cfg", "gain_db", "fade"} a piece of a source (from
        `start`, source seconds) through the same per-shot audio chain, gain_db lower, faded in/out."""

    def extract_frame(self, video: Path, out_png: Path, at: float | None = None) -> Path:
        """One frame of a segment (at=None → the last frame)."""

    def compose(self, segments: list[dict], segment_files: list[Path], layer: Path | None,
                music: Path | None, sfx: Path | None, cfg: dict, W: int, H: int, total: float,
                has_voice: bool, draft: bool, workdir: Path, out: Path, stems_dir: Path | None = None) -> Path:
        """Joins the segments with their transitions, overlays texts, mixes music/sfx (premaster).
        With stems_dir, also writes original.wav / music.wav / sfx.wav there in the same pass."""

    def master_audio(self, src: Path, out: Path, cfg: dict) -> dict:
        """Brings the audio to the target LUFS / true peak; returns {"before_lufs", "after_lufs",
        "true_peak_dbtp", "gain_db", "converged"[, "warning"]} (EBU R128, as measured by `sve check`)."""

    def measure_loudness(self, path: Path, cfg: dict | None = None) -> dict:
        """{"integrated" LUFS, "true_peak" dBTP, "lra" LU} (EBU R128)."""

    def cover(self, video: Path, t: float, out: Path) -> Path: ...


class VideoAnalyzer(Protocol):
    def samples(self, video: str, per_second: float) -> list[dict]:
        """[{"t", "brightness", "sharpness", "motion", "color"}, …]"""

    def scenes(self, video: str, threshold: float) -> list[list[float]]: ...

    def read_frames(self, video: str, times: Iterable[float]) -> list:
        """BGR frames (numpy arrays) at the given times (missing ones are skipped)."""


class AudioAnalyzer(Protocol):
    def levels(self, wav: str) -> dict:
        """{"step", "db": […], "peaks": […], "median_db"}"""

    def music(self, path: Path) -> dict:
        """{"duration", "tempo", "interval", "beats", "drop"} (see domain.music)"""


class Transcriber(Protocol):
    def transcribe(self, wav: str, settings: dict, workdir: Path) -> list[dict]:
        """[{"word", "start", "end", "prob"}, …]"""


class TextRenderer(Protocol):
    def render_layer(self, texts: list[dict], groups: list[dict], cfg: dict, W: int, H: int,
                     total: float, folder: Path, boxes: list | None = None) -> tuple[Path | None, list[float]]:
        """Draws texts/subtitles as still images + an ffconcat list → (list path, keystroke times).
        If `boxes` is a list it receives the placed boxes {"kind", "text", "x", "y", "w", "h", "start", "end"}."""


class ImageComposer(Protocol):
    def polaroid_frames(self, frame_png: Path, shot: dict, W: int, H: int, fps: int, cfg: dict) -> Iterable[bytes]: ...

    def collage_frames(self, frame_pngs: list[Path], background_png: Path, shot: dict, times: list[float],
                       W: int, H: int, fps: int, cfg: dict) -> Iterable[bytes]: ...

    def contact_sheets(self, info: dict, analysis: dict, frames_at, out_dir: Path, start: float = 0,
                       end: float | None = None, every: float | None = None) -> list[Path]: ...


class SoundEffects(Protocol):
    def mix_track(self, events: list[tuple[str, float, float]], total: float, out: Path) -> Path:
        """Mixes all sound effects (name, second, gain dB) into ONE track."""


class RecipeRepository(Protocol):
    def load(self, name: str, settings: dict | None = None) -> dict:
        """base → recipe → settings, merged and resolved (English keys)."""

    def available(self) -> dict[str, Path]: ...


class ProjectRepository(Protocol):
    def resolve(self, name_or_path: str) -> Path: ...
    def load(self, folder: str | Path) -> Project: ...
    def create(self, name: str, recipe: str, sources: list[str]) -> Path: ...
    def load_plan(self, project: Project) -> dict | None: ...
    def save_plan(self, project: Project, plan: dict) -> Path: ...
    def new_version(self, project: Project) -> Path: ...
    def variant_folder(self, project: Project, suffix: str) -> Path: ...
    def last_version(self, project: Project) -> Path | None: ...
    def versions(self, project: Project) -> list[Path]: ...
    def load_version(self, folder: str | Path, version: str | None = None) -> dict:
        """A rendered version (read only): {"dir", "name", "video", "summary", "plan", "config", "stems"}."""

    def mark_failed(self, folder: Path, suffix: str) -> Path: ...
    def write_json(self, path: Path, data) -> None: ...
    def read_json(self, path: Path) -> dict | list | None: ...
    def load_summary(self, folder: Path) -> dict | None: ...
    def write_text(self, path: Path, text: str) -> None: ...
    def find_music(self, music_cfg: dict, project_dir: Path) -> Path | None: ...
    def music_dir(self) -> Path: ...


class RenderInspector(Protocol):
    """Measures a finished render (sve check)."""

    def loudness(self, path: Path) -> dict:
        """EBU R128: {"integrated", "true_peak", "lra", "t", "momentary", "short_term"} (series every 0.1 s)."""

    def audio(self, path: Path, sr: int = 48000):
        """The audio as a float32 numpy array, shape (samples, 2)."""

    def video_events(self, path: Path, black_min: float, freeze_min: float) -> dict:
        """{"black": [[start, end]], "freeze": [[start, end]]}"""

    def snippet(self, path: Path, start: float, dur: float, out: Path) -> Path:
        """A short wav of the audio for a human to listen to."""


class CacheStore(Protocol):
    """Per-project cache of probes/analyses (reads legacy Spanish cache files too)."""

    def load(self, cache_dir: Path, kind: str, key: str, newer_than: Path | None = None) -> dict | list | None: ...
    def save(self, cache_dir: Path, kind: str, key: str, data) -> Path: ...


class MusicDownloader(Protocol):
    def download(self, query: str, dest: Path) -> Path: ...


class VisualDetector(Protocol):
    """Image–text model (e.g. CLIP): video frames and texts embedded in ONE space (unit vectors).

    `settings` is the `vision` section of the configuration (model, text_model, device, fps…).
    The similarity math lives in domain.products.
    """

    def ready(self, settings: dict, download: bool = False) -> str | None:
        """None when the model can be used; otherwise why not (not installed, not downloaded…).
        download=True may download the model the first time."""

    def model_name(self, settings: dict) -> str: ...

    def frame_embeddings(self, video: str, cache_dir: Path, key: str, settings: dict) -> tuple:
        """(times [n], embeddings [n, d]) of frames sampled `fps` times per second (cached per source)."""

    def text_embeddings(self, texts: list[str], settings: dict):
        """Embeddings [len(texts), d] of texts in any supported language."""
