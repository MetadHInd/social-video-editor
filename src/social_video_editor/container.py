"""Composition root: wires every port to its adapter."""
from __future__ import annotations

from .adapters.clip.detector import ClipVisualDetector
from .adapters.ffmpeg.encoder import FfmpegEncoder
from .adapters.ffmpeg.inspector import FfmpegRenderInspector
from .adapters.ffmpeg.photo_encoder import FfmpegPhotoEncoder
from .adapters.ffmpeg.probe import FfprobeMediaProbe
from .adapters.ffmpeg.sfx import FfmpegSoundEffects
from .adapters.files.cache import JsonCacheStore
from .adapters.files.projects import YamlProjectRepository
from .adapters.files.recipes import YamlRecipeRepository
from .adapters.files.workspace import subdir
from .adapters.librosa.audio_analyzer import LibrosaAudioAnalyzer
from .adapters.opencv.video_analyzer import OpenCvVideoAnalyzer
from .adapters.pillow.composer import PillowComposer
from .adapters.pillow.text_layer import PillowTextRenderer
from .adapters.upscale.lanczos import LanczosUpscaler
from .adapters.upscale.realesrgan import RealEsrganUpscaler
from .adapters.whisper.transcriber import WhisperTranscriber
from .adapters.ytdlp.downloader import YtDlpDownloader
from .application.services import Services


def build_services() -> Services:
    recipes = YamlRecipeRepository()
    encoder = FfmpegEncoder()
    return Services(
        probe=FfprobeMediaProbe(),
        encoder=encoder,
        video=OpenCvVideoAnalyzer(),
        audio=LibrosaAudioAnalyzer(),
        transcriber=WhisperTranscriber(),
        text=PillowTextRenderer(),
        composer=PillowComposer(),
        sfx=FfmpegSoundEffects(lambda: subdir("sfx")),
        recipes=recipes,
        projects=YamlProjectRepository(recipes),
        cache=JsonCacheStore(),
        downloader=YtDlpDownloader(),
        visual=ClipVisualDetector(),     # lazy: loads sentence-transformers only when vision is used
        inspector=FfmpegRenderInspector(),
        photos=FfmpegPhotoEncoder(encoder),
        upscaler=RealEsrganUpscaler(fallback=LanczosUpscaler()),   # lazy: torch only loaded by sve enhance
    )
