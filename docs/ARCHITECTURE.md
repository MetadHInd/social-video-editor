# Architecture

`social_video_editor` follows a **hexagonal** (ports and adapters) layout. The domain is pure
Python (no FFmpeg, no files); everything that touches the outside world sits behind a port.

```
src/social_video_editor/
├── domain/        pure logic: models, configuration, pieces (Composite), timeline, beat math,
│                  shot selection, transitions/effects catalogs, text layout, color math,
│                  render checks, product detection math (products.py), still photos (photos.py:
│                  capture time, Ken Burns, slideshow plan, auto-tone curve), playlist mixing
│                  (automix.py) and photo durations fitted to the music (slideshow_timing.py)
├── ports/         typing.Protocol interfaces the application needs
├── adapters/      implementations: ffmpeg/, opencv/, librosa/, whisper/, pillow/, files/, system/, ytdlp/, clip/, upscale/
├── application/   use cases: analyze, plan, render, thumbs, view, report, check, products, music, enhance, project
├── container.py   composition root: wires every port to its adapter (build_services)
├── cli.py         argparse front end (`sve …`)
├── recipes/       packaged YAML recipes (base.yaml + one per video type)
└── data/vocabulary/  packaged product names (es/en) for the vision model
```

Dependency rule: `domain` imports nothing from the other layers; `application` uses `domain` and
the `Services` bundle of ports (`application/services.py`); `adapters` implement ports and may use
`domain`; only `container.py` and `cli.py` know concrete adapters.

## Ports and adapters

| Port (`ports/__init__.py`) | Adapter | Role |
|---|---|---|
| `MediaProbe` | `adapters/ffmpeg/probe.py` · `FfprobeMediaProbe` | Size (with rotation), fps, duration, audio, HDR/HLG flags. |
| `VideoEncoder` | `adapters/ffmpeg/encoder.py` · `FfmpegEncoder` | Proxies, 16 kHz wav, one segment per shot, raw-frame segments, final compose (transitions, text layer, music, sfx; long videos joined in chunks, `render.chunk_*`), loudness mastering, cover; AutoMix song features and the rendered playlist mix (`music_features`, `render_automix`, in `automix_render.py`; plan in `domain/automix.py`). Filter strings live in `adapters/ffmpeg/filters.py`; the HLG→SDR LUT in `luts.py`. |
| `VideoAnalyzer` | `adapters/opencv/video_analyzer.py` · `OpenCvVideoAnalyzer` | Per-frame samples (brightness, sharpness, motion, color), scene changes (PySceneDetect), frame reading. |
| `AudioAnalyzer` | `adapters/librosa/audio_analyzer.py` · `LibrosaAudioAnalyzer` | Loudness/peaks of a source; tempo, beat grid and drop of a song (math in `domain/music.py`). |
| `Transcriber` | `adapters/whisper/transcriber.py` · `WhisperTranscriber` | Word-level faster-whisper transcription, run in a subprocess (`worker.py`) because OpenCV and PyAV libraries clash in one process. |
| `TextRenderer` | `adapters/pillow/text_layer.py` · `PillowTextRenderer` | Draws subtitles and text cards as still PNGs + an ffconcat list; returns keystroke times. Emoji-aware text in `rich_text.py`. |
| `ImageComposer` | `adapters/pillow/composer.py` · `PillowComposer` | Polaroid and collage frames, contact sheets (video frames, or the photos themselves: `photo_sheets`). |
| `PhotoRenderer` | `adapters/ffmpeg/photo_encoder.py` · `FfmpegPhotoEncoder` | Still photos: finding them (folders), and one photo → a segment (EXIF orientation, `photo.auto_tone` exposure fix with local shadow recovery for backlit photos, cover or blurred-background framing, sub-pixel Ken Burns, warmth / vignette, `render.photo_crf`). Tone curve math in `domain/photos.py`. |
| `ImageUpscaler` | `adapters/upscale/realesrgan.py` · `RealEsrganUpscaler` (fallback `lanczos.py` · `LanczosUpscaler`) | `sve enhance`: small photos enlarged ×4 (torch loaded lazily), big ones reduced, into `cache/photos/`. |
| `SoundEffects` | `adapters/ffmpeg/sfx.py` · `FfmpegSoundEffects` | Mixes all sound effects into one track; synthesizes missing `sfx/*.wav`. |
| `RecipeRepository` | `adapters/files/recipes.py` · `YamlRecipeRepository` | `base → recipe → settings` merge and language resolution. |
| `ProjectRepository` | `adapters/files/projects.py` · `YamlProjectRepository` | project.yaml, plan.json, version folders, outputs, music lookup (one track, or `music.playlist`: `find_playlist`). |
| `CacheStore` | `adapters/files/cache.py` · `JsonCacheStore` | Per-project JSON cache (media info, analyses, words, music analyses). |
| `MusicDownloader` | `adapters/ytdlp/downloader.py` · `YtDlpDownloader` | `sve music`. |
| `RenderInspector` | `adapters/ffmpeg/inspector.py` · `FfmpegRenderInspector` | `sve check`: EBU R128 loudness series, decoded audio, black/frozen frames, listening snippets (rules in `domain/check.py`). |
| `VisualDetector` | `adapters/clip/detector.py` · `ClipVisualDetector` | Local CLIP via sentence-transformers (optional `[vision]` extra, imported lazily): frame and text embeddings in one space; frame embeddings cached in `cache/<id>_clip.npy`. Scoring, segmentation and labeling live in `domain/products.py`. |

Supporting adapter modules: `adapters/files/legacy_es.py` translates Spanish files written by
earlier versions **when they are loaded** (the domain only sees English keys; tables in
[LEGACY_KEYS.md](LEGACY_KEYS.md)); `adapters/files/workspace.py` resolves the workspace
(`$SVE_WORKSPACE` or the current folder); `adapters/files/atomic.py` gives unique temporary names
and atomic publishing for every cached file; `adapters/system/` holds FFmpeg paths, font lookup and
hardware acceleration (`hwaccel.py`).

Hardware acceleration backends: VideoToolbox (macOS), NVENC (`cuda`), VAAPI and Quick Sync (`qsv`);
`auto` tries VideoToolbox on macOS, NVENC → VAAPI → Quick Sync on Linux and NVENC → Quick Sync on
Windows. A backend is used only when FFmpeg lists its hwaccel and encoder **and** a one-frame test
encode works; a hardware command that still fails is retried in software and hardware stays off for
the rest of the process. They decode and encode analysis proxies and draft segments only; final
segments are always libx264, decoded by VideoToolbox when it is available (software elsewhere).
`SVE_HWACCEL` = `auto` (default) / `videotoolbox` / `nvenc` / `vaapi` / `qsv` / `0` (software only);
`SVE_VAAPI_DEVICE` = the VAAPI render node (default `/dev/dri/renderD128`).

## The video as a Composite (`domain/pieces.py`)

```
Sequence (whole video)                     composite
  ├── VideoShot        cut of a source     leaf
  ├── Group "star"                         composite (consecutive shots with the same "group")
  │     ├── VideoShot (fast)
  │     ├── VideoShot (slow, "continuous")
  │     └── PolaroidShot (from_shot = k)   leaf drawn from another shot's last frame
  ├── CollageShot (from_shots = […])       leaf drawn from several shots' frames
  └── PhotoShot        a still photo       leaf (own "duration", Ken Burns "motion")
```

- Every `Piece` answers `duration()`, `children()`, `walk()`, `shots()` and `dependencies()`.
- Leaves (`Shot`) wrap the plan's dict **without copying** it, so beat alignment writes straight
  into the plan. They know how to fit a beat-aligned duration (`fit_to_duration`, `shift_end`),
  which source window they play (`source_window`), which sound effects they trigger
  (`sound_events`) and how to describe themselves (`summary`, `describe`).
- `DrawnShot` (polaroid, collage) has its own `dur` (default 0.6 s per beat).
- `Sequence.from_plan(plan)` builds the tree; `Sequence.render_order()` returns shot indices so that
  every drawn shot is rendered after the shots it depends on, and raises `PlanError` for missing
  or circular references before anything is encoded.
- Rendering is polymorphic: `piece.accept(visitor)` calls `visit_video / visit_polaroid /
  visit_collage / visit_photo` on a `PieceVisitor` (the renderer is `application/render.py:ShotRenderer`).

`domain/timeline.py` places the leaves on the output timeline (`Clip`: duration in whole frames,
overlap of the entry transition, start/end), aligns cuts to music beats (`snap_cuts_to_beats`,
`align_to_beats`) and converts shot-anchored texts to absolute times (`absolute_texts`).

## Text layout Composite (`domain/text_layout.py`)

```
Layer                     everything over the video
  ├── Caption             one auto-subtitle image (active word highlighted), optional pop
  └── Card                a plan text
        ├── TextElement   title units, typed one by one ("typewriter") or shown at once
        └── TextElement   optional description, typed after the title
```

Every node answers `cuts()` (instants where the picture changes) and `state(t)`; the layer turns
the cuts into a list of still frames with durations, which the Pillow adapter draws once each.
Typing speed adapts so a card is always fully typed with time left to read (≤ 75 % of its
duration). Units come from `domain/text_units.py` (a composed emoji is one unit).

## Data flow

```
sources ──analyze──▶ cache/<id>_media.json, <id>_analysis.json (samples, scenes, audio, words, voice)
                      │
                      ├──thumbs──▶ cache/thumbs/ contact sheets + transcript (to look before deciding)
                      ▼
                    plan ──▶ plan.json (editable: shots + texts + extra_candidates)
                      │
                      ▼
                    render
                      1. resolve "search" windows (best_window), pick music, start offset, beat alignment
                      2. timeline → render_order → one cached segment per shot (cache/segments/)
                      3. compose: transitions + text layer + music (ducking) + sfx → premaster
                      4. master to −14 LUFS / −1 dBTP → vN/<name>.mp4, .srt, cover,
                         plan_used.json, config_used.json, summary.json
                      ▼
                    check  (`sve check`) ──▶ vN/check.json + listen/ excerpts
                      ▼
                    report ──▶ one screen / JSON from the files above (reads only)
```

`<id>` is the file name plus a short hash of the source's resolved path, size and modification
time (`clip_0001_3fa2c9d1`), so two clips with the same name in different folders never share a
cache and a replaced file is analyzed again. Caches written by earlier versions under the bare
file name (`clip_0001_media.json` / `_info.json`) keep being used while they record the same
resolved path (and size, once recorded).

Photo slideshows: folder sources are expanded to their photos (`application/analyze.py:expand_sources`;
photos only get a media info, no proxy or analysis); `sve enhance` (`application/enhance.py`,
`ImageUpscaler` port) writes resized copies to `cache/photos/`; `sve plan` writes one `photo` shot per
photo in chronological order (`domain/photos.py`); render step 1 with `music.playlist` analyzes every
song, plans and renders one AutoMix WAV (`application/music.py:prepare_playlist`, `domain/automix.py`)
and, with `photo.fit_to_music`, fits the photo durations to it (`domain/slideshow_timing.py`); step 2
renders each photo through the `PhotoRenderer` port; step 3 joins long timelines in chunks.

With the optional vision model (`application/products.py`, `VisualDetector` port):
`sve products` writes `cache/products.json` (summarized by `sve report`); `plan` proposes one shot
per detected product when `vision.product_plan` is on (the `unboxing` recipe) and falls back to
the regular planner otherwise; render step 1 scores `search` windows with it for `search.text`
(whenever the model is available) and for `criterion: product` when `vision.product_search` is
on (opt-in). `vision.enabled` (`auto` / `true` / `false`) decides when the model may be loaded or
downloaded.

Segment files are keyed by a hash of everything that affects them (shot keys, source, size,
relevant config, draft flag, `SEGMENT_VERSION`), so re-rendering after a plan edit only encodes
the shots that changed.

## Configuration

`domain/config.py`: `deep_merge` of `recipes/base.yaml → recipes/<type>.yaml → project settings`,
then `resolve()` applies `language` (picks `{es: …, en: …}` values, fills
`transcription.language`). Version-folder and file-name labels per language and the export
profiles are defined there too.

## Extension points

| To add… | Where |
|---|---|
| **A transition** | `domain/transitions.py:TRANSITIONS` (duration, fixed, sound) and `adapters/ffmpeg/filters.py:XFADE` (FFmpeg xfade name). Optional Spanish alias in `legacy_es.TRANSITIONS` + `docs/LEGACY_KEYS.md` (a test checks they match). |
| **A shot effect** | `domain/effects.py:EFFECT_TYPES` and its drawing in `adapters/ffmpeg/filters.py` (`zoom_expr`, `motion`, `flash_filters`…). If it adds a shot key that changes the picture, add it to `SHOT_SIGNATURE_KEYS` in `application/render.py`. |
| **A shot type** | Subclass `DrawnShot` (or `Shot`) in `domain/pieces.py`, register it in `SHOT_TYPES`, add `visit_<kind>` to `PieceVisitor` and `ShotRenderer`, and the drawing method to the `ImageComposer` port + Pillow adapter. |
| **A text style** | `texts.styles` in `recipes/base.yaml` (or in a recipe / project settings). |
| **A recipe** | `<workspace>/recipes/<name>.yaml` (or `src/social_video_editor/recipes/` to ship it). Only write what differs from `base.yaml` (a key nothing reads is silently ignored). |
| **An export profile** | `EXPORT_PROFILES` in `domain/config.py` (and its label per language in `_LABELS` if it needs one). |
| **An adapter** | Implement the Protocol from `ports/__init__.py` in `adapters/<tech>/` and wire it in `container.py:build_services`. Use cases don't change. |
| **A use case / command** | `application/<name>.py` taking `Services`, plus a subparser in `cli.py`. |
