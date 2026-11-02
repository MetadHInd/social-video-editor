"""The set of ports a use case can use (built by container.py)."""
from __future__ import annotations

from dataclasses import dataclass

from ..ports import (AudioAnalyzer, CacheStore, ImageComposer, MediaProbe, MusicDownloader, ProjectRepository,
                     RecipeRepository, SoundEffects, TextRenderer, Transcriber, VideoAnalyzer, VideoEncoder,
                     VisualDetector)


@dataclass
class Services:
    probe: MediaProbe
    encoder: VideoEncoder
    video: VideoAnalyzer
    audio: AudioAnalyzer
    transcriber: Transcriber
    text: TextRenderer
    composer: ImageComposer
    sfx: SoundEffects
    recipes: RecipeRepository
    projects: ProjectRepository
    cache: CacheStore
    downloader: MusicDownloader
    visual: VisualDetector | None = None
