# Architecture

`social_video_editor` follows a **hexagonal** (ports and adapters) layout. The domain is pure
Python (no FFmpeg, no files); everything that touches the outside world sits behind a port.

```
src/social_video_editor/
├── domain/        pure logic: models, configuration, pieces (Composite), timeline, beat math,
│                  shot selection, transitions/effects catalogs, text layout, color math
├── ports/         typing.Protocol interfaces the application needs
├── adapters/      implementations: ffmpeg/, opencv/, librosa/, whisper/, pillow/, files/, system/, ytdlp/
├── application/   use cases: analyze, plan, render, thumbs, view, report, music, project
├── container.py   composition root: wires every port to its adapter (build_services)
├── cli.py         argparse front end (`sve …`)
└── recipes/       packaged YAML recipes (base.yaml + one per video type)
```

Dependency rule: `domain` imports nothing from the other layers; `application` uses `domain` and
the `Services` bundle of ports (`application/services.py`); `adapters` implement ports and may use
`domain`; only `container.py` and `cli.py` know concrete adapters.

## Ports and adapters

| Port (`ports/__init__.py`) | Adapter | Role |
|---|---|---|
| `MediaProbe` | `adapters/ffmpeg/probe.py` · `FfprobeMediaProbe` | Size (with rotation), fps, duration, audio, HDR/HLG flags. |
| `VideoEncoder` | `adapters/ffmpeg/encoder.py` · `FfmpegEncoder` | Proxies, 16 kHz wav, one segment per shot, raw-frame segments, final compose (transitions, text layer, music, sfx), loudness mastering, cover. Filter strings live in `adapters/ffmpeg/filters.py`; the HLG→SDR LUT in `luts.py`. |
| `VideoAnalyzer` | `adapters/opencv/video_analyzer.py` · `OpenCvVideoAnalyzer` | Per-frame samples (brightness, sharpness, motion, color), scene changes (PySceneDetect), frame reading. |
| `AudioAnalyzer` | `adapters/librosa/audio_analyzer.py` · `LibrosaAudioAnalyzer` | Loudness/peaks of a source; tempo, beat grid and drop of a song (math in `domain/music.py`). |
| `Transcriber` | `adapters/whisper/transcriber.py` · `WhisperTranscriber` | Word-level faster-whisper transcription, run in a subprocess (`worker.py`) because OpenCV and PyAV libraries clash in one process. |
| `TextRenderer` | `adapters/pillow/text_layer.py` · `PillowTextRenderer` | Draws subtitles and text cards as still PNGs + an ffconcat list; returns keystroke times. Emoji-aware text in `rich_text.py`. |
| `ImageComposer` | `adapters/pillow/composer.py` · `PillowComposer` | Polaroid and collage frames, contact sheets. |
| `SoundEffects` | `adapters/ffmpeg/sfx.py` · `FfmpegSoundEffects` | Mixes all sound effects into one track; synthesizes missing `sfx/*.wav`. |
| `RecipeRepository` | `adapters/files/recipes.py` · `YamlRecipeRepository` | `base → recipe → settings` merge and language resolution. |
| `ProjectRepository` | `adapters/files/projects.py` · `YamlProjectRepository` | project.yaml, plan.json, version folders, outputs, music lookup. |
| `CacheStore` | `adapters/files/cache.py` · `JsonCacheStore` | Per-project JSON cache (media info, analyses, words, music analyses). |
| `MusicDownloader` | `adapters/ytdlp/downloader.py` · `YtDlpDownloader` | `sve music`. |
| `VisualDetector` | — (`None` in `container.py`) | Reserved for a visual (CLIP-style) model. |

Supporting adapter modules: `adapters/files/legacy_es.py` translates Spanish files written by
earlier versions **when they are loaded** (the domain only sees English keys; tables in
[LEGACY_KEYS.md](LEGACY_KEYS.md)); `adapters/files/workspace.py` resolves the workspace
(`$SVE_WORKSPACE` or the current folder); `adapters/system/` holds FFmpeg paths, font lookup and
VideoToolbox detection with software fallback.

## The video as a Composite (`domain/pieces.py`)

```
Sequence (whole video)                     composite
  ├── VideoShot        cut of a source     leaf
  ├── Group "star"                         composite (consecutive shots with the same "group")
  │     ├── VideoShot (fast)
  │     ├── VideoShot (slow, "continuous")
  │     └── PolaroidShot (from_shot = k)   leaf drawn from another shot's last frame
  └── CollageShot (from_shots = […])       leaf drawn from several shots' frames
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
  visit_collage` on a `PieceVisitor` (the renderer is `application/render.py:ShotRenderer`).

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
                    check  (provided by `sve check`) ──▶ vN/check.json + listen/ excerpts
                      ▼
                    report ──▶ one screen / JSON from the files above (reads only)
```

`sve products` (provided by that command) adds `cache/products.json`, which a plan can use and
`sve report` summarizes.

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
