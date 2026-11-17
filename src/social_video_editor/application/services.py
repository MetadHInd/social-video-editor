"""The set of ports a use case can use (built by container.py)."""
from __future__ import annotations

from dataclasses import dataclass

from ..ports import (AudioAnalyzer, CacheStore, FootageSource, GraphicRenderer, ImageComposer, ImageUpscaler, MediaProbe,
                     MusicDownloader, MusicSynthesizer, PackRepository, PhotoRenderer, ProjectRepository, RecipeRepository,
                     Reasoner, RenderInspector, SoundEffects, TextClassifier, TextRenderer, Transcriber, VideoAnalyzer,
                     VideoEncoder, VisualDetector)


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
    inspector: RenderInspector | None = None
    photos: PhotoRenderer | None = None
    upscaler: ImageUpscaler | None = None
    graphics: GraphicRenderer | None = None
    packs: PackRepository | None = None
    footage: dict[str, FootageSource] | None = None      # by name, for `sve broll`
    classifier: TextClassifier | None = None             # the decider's small model (zero-shot NLI)
    reasoner: Reasoner | None = None                     # the decider's local language model
    synth: MusicSynthesizer | None = None                # composes original music (music.file: compose)
