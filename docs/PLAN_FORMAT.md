# Plan, project and recipe reference

Three files describe a video:

| File | Who writes it | What it holds |
|---|---|---|
| `projects/<name>/project.yaml` | `sve new`, then you | Name, recipe, sources (story order), settings that override the recipe. |
| `projects/<name>/plan.json` | `sve plan`, then you / an agent | The edit: shots and texts. |
| `recipes/<recipe>.yaml` | packaged, or your own in `<workspace>/recipes/` | Defaults per video type. |

All keys below are the **English canonical names**. Files written by earlier versions with
Spanish keys (`proyecto.yaml`, `tomas`, `textos`, `ajustes`, `ritmo`…) are still read: every alias
is listed in [LEGACY_KEYS.md](LEGACY_KEYS.md).

---

## project.yaml

```yaml
name: my_video                 # default: the folder name
recipe: unboxing               # default: base
sources:                       # story order; a string or {path: …}; ~ is expanded; must exist
  - ~/clips/opening.mov
  - path: ~/clips/reveal.mov
settings:                      # optional: any recipe key (see below), deep-merged on top of the recipe
  language: en
  hook: {text: "Was it worth it? 👀"}
  music: {file: "music/My Track.mp3", start_at: auto}
```

Sources are referred to by their **index** (0, 1, …) in `plan.json`.

---

## plan.json

```json
{
  "note": "free text",
  "shots": [ … ],
  "texts": [ … ],
  "extra_candidates": [ … ]
}
```

- **Shot times** (`start`, `end`) are seconds **inside the source**.
- **Text times** are seconds of the **final video**, unless the text is anchored to a shot.
- Shot indices (`from_shot`, `from_shots`, `shot`, `until_shot`) are **0-based** positions in
  `shots`.
- `extra_candidates` (written by `sve plan`) are good moments that were not used — handy to swap a
  shot. They are not rendered.
- `sve plan --force` keeps the previous plan as `plan_previous.json`. Each render copies the plan
  it actually used (search windows resolved, beat-aligned times) to `vN/plan_used.json`.

### Shots

Every shot has a `type` (default `video`). Common keys:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `type` | `video` \| `polaroid` \| `collage` | `video` | Kind of shot. |
| `transition` | name | `cut` | How the shot **enters** (see catalog). Ignored on the first shot. |
| `role` | text | — | Free label shown by `view` / `report` and in `summary.json`. The planner uses `hook`, `story`, `reveal`; others in use: `opening`, `product`, `star`, `outro`. |
| `beats` | int | — | Make the shot last exactly N music beats. If **any** shot has `beats`, every cut is placed exactly on a beat (shots without it get the nearest whole number of beats). |
| `group` | text | — | Consecutive shots with the same label form a `Group` (shown in `view` / `report`). |
| `note` | text | — | Free text (the planner stores what is said in the shot). |
| `score`, `peak`, `voice` | — | — | Informational, written by the planner. |

**Video shots** (`type: video`):

| Key | Type | Default | Meaning |
|---|---|---|---|
| `source` | int | required | Index of the source clip. |
| `start`, `end` | seconds | required unless `search` | Window inside the source. |
| `search` | object | — | Let the engine pick the window at render time: `{"range": [a, b], "dur": 1.8, "criterion": "product"}`. `range` defaults to the whole source, `dur` to 1.8 s. `criterion`: `product` (sharp, colorful, still image) or `action` (motion + loudness); with `vision.product_search: true` the `product` criterion is scored by the CLIP model. `text` (e.g. `{"text": "phone case", "range": [40, 60]}`) picks the window that best matches a description using the vision model whenever it is available (otherwise the `product` heuristic, with a warning). Recomputed on every render; the resolved window is kept as `_searched` in `plan_used.json`. |
| `speed` | float | `1.0` | Playback speed (audio is time-stretched). |
| `continuous` | bool | `false` | With beat alignment: start exactly where the previous shot (same source) ended — for fast→slow ramps. |
| `effects` | list | `[]` | Camera effects (see below). |
| `freeze` | seconds | `0` | Hold the last frame this long (adds to the duration). |
| `zoom` | float | `1.0` | Base zoom factor. |
| `focus_x`, `focus_y` | 0–1 | `framing.focus_x/y` | Where to center the crop (0 = left/top). |
| `color` | object | — | Per-shot overrides of the `color` settings (e.g. `{"saturation": 1.2}`). |
| `volume_db` | dB | `0` | Gain of the shot's own audio. |
| `mute` | bool | `false` | Silence the shot's own audio. |

**Polaroid** (`type: polaroid`) — the last frame of another shot drops in as an instant photo
with a flash and a shutter sound:

| Key | Default | Meaning |
|---|---|---|
| `from_shot` | required | Shot whose last frame is used. |
| `text` | `""` | Hand-written caption under the photo (`polaroid.font`). |
| `tilt` | `-4` | Final rotation in degrees. |
| `dur` | `0.6 × beats` (beats default 2) | Duration in seconds (set automatically with beat alignment). |

**Collage** (`type: collage`) — frames of several shots pop in on the beat over a blurred background:

| Key | Default | Meaning |
|---|---|---|
| `from_shots` | required | Shots to take a frame from (the frame at 55 % of each). |
| `background_from` | last of `from_shots` | Shot whose last frame, softened, is the background. |
| `columns` | `collage.columns` (4) | Grid columns. |
| `subdivision` | `collage.subdivision` (2) | Photos appear every 1/N beat. |
| `dur` | `0.6 × beats` | Duration in seconds. |

### Transitions

| Name | Duration | Sound |
|---|---|---|
| `cut` | 0 | — |
| `fade` | 0.35 s (fixed) | — |
| `fade_black` | 0.4 s (fixed) | — |
| `flash` | `transitions.duration` | flash |
| `zoom`, `whip`, `slide_up`, `slide_left` | `transitions.duration` | whoosh |
| `circle` | 0.35 s (fixed) | whoosh |
| `pixel`, `blur` | 0.3 s (fixed) | — |
| `radial`, `curtain` | 0.3 s (fixed) | whoosh |

"Fixed" transitions ignore `transitions.duration`. A transition is never longer than half of
either shot it joins.

### Effects (`effects` list of a video shot)

| Effect | Parameters (default) | What it does |
|---|---|---|
| `zoom_punch` | `t` (0), `scale` (`effects.zoom_scale`, 1.12) | Sudden zoom-in. At `t` = 0 the shot starts zoomed in and settles. |
| `push_in` | `amount` (0.06) | Slow zoom-in over the whole shot (replaces the automatic `framing.slow_push`). |
| `pull_out` | `amount` (0.06) | Slow zoom-out. |
| `shake` | `t` (0), `dur` (0.3), `amplitude` (14 px) | Short shake. |
| `flash` | `t` (0) | Brief white flash. |
| `freeze` | `seconds` (0.6) | Freeze the last frame (like the shot key `freeze`). |
| `beat_pulse` | `amount` (`effects.pulse_amount`) | Small zoom on each music beat inside the shot (needs beat alignment). |

### Texts

```json
{"text": "Was it worth it? 👀", "start": 0, "end": 2.4, "style": "hook"}
{"text": "Earrings", "description": "pastel colors ✨", "shot": 3, "style": "card"}
{"text": "1/12", "shot": 3, "until_shot": 5, "style": "counter"}
```

| Key | Default | Meaning |
|---|---|---|
| `text` | required | The text (emoji allowed). |
| `style` | `simple` | A style from `texts.styles`: `hook`, `label`, `card`, `counter`, `price`, `cta`, `simple` or your own. |
| `start`, `end` | — | Final-video seconds; with `shot`, seconds **relative to that shot** (`start` default 0, `end` default the shot's duration). |
| `shot` | — | Anchor the text to a shot. It appears after the shot's entry transition and leaves before the next one. |
| `until_shot` | `shot` | Keep the text on screen until the end of this later shot. |
| `description` | — | Second line typed below the title once the title is done (`desc_font`, `desc_size`, `desc_color`). |
| `animation` | style's | `typewriter`, `pop` or `none`. |
| `speed` | style's (30) | Letters per second when typing (raised automatically so typing ends with ≥ 25 % of the time left to read). |
| `pop` | `true` | `false` = no pop-in scale animation. |
| `sound` | `true` | `false` = no pop sound when it appears. |
| `sfx` | — | Extra sound effect at its start: `whoosh`, `pop`, `flash`, `keystroke`, `shutter`. |
| `size`, `color`, `background`, `x`, `y`, `uppercase`, `max_width`, `font`, `desc_font`, `desc_color` | style's | Per-text style overrides. |

Texts that end up with zero length (e.g. past the end of the video) are dropped. When the video has
no speech, the `.srt` file contains the plan texts.

---

## Recipe and settings keys

Layering: `base.yaml` → `<recipe>.yaml` → `project.yaml settings` (deep merge: only what you write
changes). A value written as `{es: …, en: …}` is resolved by `language`. Defaults below are from
[`base.yaml`](../src/social_video_editor/recipes/base.yaml), which also comments every key.

### Top level

| Key | Default | Meaning |
|---|---|---|
| `language` | `es` | `es` \| `en`: Whisper language, localized defaults, version-folder and file names (see the README). |

### `output`, `duration`, `pacing`

| Key | Default | Meaning |
|---|---|---|
| `output.width` / `height` / `fps` | 1080 / 1920 / 30 | Output size (profiles override width/height). |
| `duration.target` | 30 | Seconds the planner aims for. |
| `duration.max` | 60 | Hard maximum for the planner. |
| `pacing.shot_min` | 0.8 | Shortest shot (s). |
| `pacing.shot_max` | 3.2 | Longest shot without speech (s). |
| `pacing.phrase_max` | 5.0 | With speech, a shot may grow until the phrase ends, up to this. |
| `pacing.min_gap` | 0.6 | Minimum separation between two shots of the same source. |
| `pacing.speed_without_voice` | 1.0 | Speed of shots without speech (e.g. 1.2). |

### `hook`

| Key | Default | Meaning |
|---|---|---|
| `strategy` | `teaser` | `teaser` = open with the best moment, then tell the story; `start` = begin at the beginning. |
| `duration` | 1.6 | Seconds of the teaser. |
| `text` | `null` | If set, the planner adds it as a `hook`-style text at 0 s. |
| `transition` | `flash` | Transition from the hook to the story. |

### `selection`, `analysis`, `transcription`

| Key | Default | Meaning |
|---|---|---|
| `selection.weights.motion` / `audio` / `peaks` / `voice` / `keywords` / `sharpness` / `color` | 1.0 / 0.7 / 0.8 / 1.0 / 2.0 / 0.6 / 0.4 | Weights of the per-instant score. |
| `selection.keywords` | `{es: [], en: []}` | Words that boost a moment when said (recipes fill them). |
| `selection.min_brightness` | 0.12 | Darker samples are penalized. |
| `selection.blur_percentile` | 12 | The blurriest N % are penalized. |
| `selection.use_all_sources` | true | Take the best moment of every source. |
| `selection.coverage` | 8 | Split the footage in N parts and take the best of each (0 = off). |
| `analysis.samples_per_sec` | 4 | Image samples per second. |
| `analysis.scene_threshold` | 27 | PySceneDetect content threshold. |
| `transcription.model` | `small` | faster-whisper model (`tiny` … `large-v3`). |
| `transcription.language` | `null` | `null` = follow `language`. |
| `transcription.context` | `""` | Prompt with names/brands that help Whisper. |
| `transcription.min_probability` | 0.35 | Words below this probability are dropped (voice activity detection is always on). |

### `color`, `framing`

| Key | Default | Meaning |
|---|---|---|
| `color.auto` | true | Automatic correction per shot. |
| `color.hdr_white` | 0.62 | HLG → SDR white level of the generated LUT. |
| `color.levels` | true | Stretch blacks/whites. |
| `color.white_balance` | 0.5 | 0 = untouched, 1 = fully neutral. |
| `color.target_luma` | 0.47 | Mean brightness every shot is brought to. |
| `color.gamma_min` / `gamma_max` | 0.8 / 1.5 | Limits of the brightness correction. |
| `color.contrast` / `saturation` / `vibrance` | 1.05 / 1.10 / 0.12 | Creative look. |
| `color.temperature` | `null` | Kelvin, e.g. 6000 = warmer. |
| `color.sharpen` | 0.35 | CAS sharpening (0 = none). |
| `color.lut` | `null` | Path to a creative `.cube` LUT. |
| `color.vignette` | false | Add a vignette. |
| `framing.mode` | `auto` | `crop`, `blur` (blurred borders) or `auto` (blur when aspect ratios differ a lot). |
| `framing.focus_x` / `focus_y` | 0.5 / 0.5 | Crop center. |
| `framing.zoom_margin` | 1.25 | Extra working resolution so zooms keep quality (also the maximum zoom). |
| `framing.slow_push` | 0.04 | Automatic slow zoom on every shot, alternating in/out (0 = off). |

### `transitions`, `effects`

| Key | Default | Meaning |
|---|---|---|
| `transitions.rotation` | `[cut, cut, zoom, cut, whip, cut]` | Transitions the planner assigns in order. |
| `transitions.duration` | 0.22 | Duration of non-fixed transitions. |
| `effects.zoom_on_peaks` | true | Planner adds `zoom_punch` on shots with a sound peak. |
| `effects.zoom_scale` | 1.12 | Default `zoom_punch` scale. |
| `effects.pulse_amount` | 0.035 | Default `beat_pulse` amount. |
| `effects.zoom_ratio` | 0.3 | At most this share of shots get a zoom punch. |
| `effects.flash_on_reveal` | false | `reveal` shots enter with `flash`. |
| `effects.progress_bar` / `progress_bar_color` | false / `#FFFFFF` | Bar along the top that fills up. |

### `audio`, `music`, `sfx`

| Key | Default | Meaning |
|---|---|---|
| `audio.use_original` | true | Keep the sources' own sound. |
| `audio.denoise` | 10 | Noise reduction in dB (0 = off). |
| `audio.highpass` | 80 | High-pass filter (Hz). |
| `audio.voice_eq` | true | Voice presence EQ + de-esser on shots with speech. |
| `audio.compressor` | true | Gentle compression. |
| `audio.gain_without_voice_db` | 3 | Extra gain on shots without speech (ASMR sounds). |
| `audio.asmr` | false | Without speech: brighter EQ + strong compression so every crackle is heard. |
| `audio.loudness` / `true_peak` | −14 / −1.0 | Mastering target (integrated LUFS / dBTP). |
| `music.file` | `null` | Track path (relative to the project or the workspace); `null` = first track in `music.folder` (default `music/`); `none` = no music. |
| `music.folder` | — | Workspace-relative folder to take the first track from. |
| `music.volume_with_voice_db` / `volume_without_voice_db` | −20 / −11 | Music level. |
| `music.ducking` / `ducking_asmr` / `ducking_ratio` | true / false / 4 | Lower the music under the voice (and optionally under the video's own sounds). |
| `music.start_at` | 0.0 | Second of the song to start from, or `auto`: the song's drop lands at the end of the first shot (or at `drop_at`). |
| `music.drop_at` | — | Video second where the song's drop should land (with `start_at: auto`). |
| `music.align` | — | `exact` = every cut exactly on a beat (automatic when a shot has `beats`). |
| `music.cuts_on_beat` / `half_beats` | true / true | Otherwise, move cuts to the nearest beat (or half beat) within 0.3 s. |
| `music.fade_in` / `fade_out` | 0.25 / 1.0 | Fades (s). |
| `music.bpm` / `tempo_factor` | null / null | Force the tempo (e.g. 120) / multiply the detected one (2 = double, 0.5 = half) when detection picks half or double. `sve music` shows the alternatives; `sve report` shows them when set. |
| `sfx.enabled` | true | Sound effects on/off. |
| `sfx.volume_db` | −12 | Base level. |
| `sfx.pop_on_texts` / `typing` / `typing_db` / `whoosh_on_transitions` | true / true / −9 / true | Which effects play. |

### `subtitles`, `texts`

| Key | Default | Meaning |
|---|---|---|
| `subtitles.auto` | true | Word-by-word subtitles from the transcription when there is speech. |
| `subtitles.max_words` / `max_chars` | 3 / 18 | Words and characters per subtitle. |
| `subtitles.uppercase`, `font`, `size`, `color`, `highlight`, `stroke`, `stroke_color`, `shadow` | true, Inter-Black, 82, white, `#FFE14D`, 9, black, true | Look (active word in `highlight`). |
| `subtitles.y` | 0.64 | Vertical center (0 = top, 1 = bottom). |
| `subtitles.animation` | `pop` | `pop` or `none`. |
| `texts.styles.<name>` | see `base.yaml` | `size`, `font`, `color`, `background` (`#RRGGBBAA`), `x`, `y`, `max_width`, `uppercase`, `radius`, `outline`, `animation`, `speed`, `desc_font`, `desc_size`, `desc_color`. Sizes are for a 1080-px-wide output and scale with it. |

### `export`, `polaroid`, `collage`

| Key | Default | Meaning |
|---|---|---|
| `export.crf` / `maxrate` / `audio_kbps` | 17 / 20M / 192 | H.264 quality, bitrate cap, AAC bitrate. |
| `export.cover` / `cover_at` | true / 1.6 | Export a cover JPEG taken at that second. |
| `polaroid.width` / `center_y` / `font` / `drop_time` / `flash` | 0.70 / 0.5 / Bradley Hand Bold / 0.16 / 0.10 | Photo size, crop center, caption font, landing time, flash duration. |
| `collage.columns` / `subdivision` / `y0` / `y1` | 4 / 2 / 0.215 / 0.72 | Grid columns, appearance rate, vertical zone (kept in the safe zone). |

### `vision`

Local CLIP model (optional extra `pip install -e ".[vision]"`, ≈1.2 GB downloaded on first use).
Used by `sve products`, by `search.text`, by `sve plan` when `product_plan` is on, and by
`criterion: product` searches when `product_search` is on.

| Key | Default | Meaning |
|---|---|---|
| `vision.enabled` | `auto` | `auto` = used when installed and already downloaded; `true` = may download the model; `false` = never (`sve products` always uses it). |
| `vision.model` / `text_model` | `sentence-transformers/clip-ViT-B-32` / `…/clip-ViT-B-32-multilingual-v1` | Image model; text model in the same space (Spanish, English and 50+ languages). |
| `vision.device` | `cpu` | `cpu`, `mps` (Apple GPU, opt-in; falls back to CPU on invalid values) or `cuda`. |
| `vision.fps` | 2 | Frames per second embedded (from the proxy; cached in `cache/<id>_clip.npy`). |
| `vision.positive` / `negative` | see `base.yaml` | Prompts describing a product shot / what is not one (`{es: […], en: […]}`). |
| `vision.weights` | `{prompts: 0.5, objectness: 1.0, novelty: 1.0, sharpness: 0.5}` | Parts of the product score. |
| `vision.min_score` / `extend_score` | 0.35 / −0.25 | Standardized score where a product starts / that neighboring frames need to join it. |
| `vision.continuity` | 0.78 | Similarity between consecutive frames below which a new product starts. |
| `vision.min_duration` / `max_duration` | 1.0 / 25.0 | Seconds a product must be visible / longest segment. |
| `vision.same_product` | 0.82 | Two stretches this similar (and < 20 s apart) are merged as one product. |
| `vision.window` | 2.0 | Seconds of the best moment of each product (`best` in `products.json`). |
| `vision.max_products` | 15 | At most this many products (the best-scoring ones, kept in story order). |
| `vision.stars` | 3 | The most confident products get a fast → slow ramp and a polaroid in the product plan. |
| `vision.vocabulary` | null | null = packaged list of ~80 small products (es/en); or your own list of names. |
| `vision.product_plan` | false (`unboxing`: true) | `sve plan` makes one shot per detected product (card with its name + `n/N` counter, collage at the end). |
| `vision.product_search` | false | Opt-in: `search` with `criterion: product` uses the vision model (changes the windows of existing plans). `search.text` uses it regardless. |

### Export profiles (`sve render --profile`)

| Profile | Size | CRF | Max rate |
|---|---|---|---|
| `tiktok` | 1080×1920 | 17 | 20M |
| `reels` | 1080×1920 | 17 | 15M |
| `shorts` | 1080×1920 | 17 | 20M |
| `youtube` | 1920×1080 | 18 | 16M |
| `square` | 1080×1080 | 17 | 15M |

---

## Outputs

A render writes a version folder `vN/` (or a variant such as `vNSinMusica/`, see the README's
Language section) with:

| File | Content |
|---|---|
| `<name>.mp4` | The video. |
| `<name>.srt` | Subtitles (auto subtitles, or the plan texts when there is no speech). |
| `<name>_portada.jpg` / `<name>_cover.jpg` | Cover image. |
| `plan_used.json` | The plan as rendered (resolved search windows, beat-aligned times, `_beats`). |
| `config_used.json` | The fully merged configuration. |
| `summary.json` | `video`, `duration_sec`, `shots`, `resolution`, `music` {`file`, `start_sec`}, `audio` {`before_lufs`, `after_lufs`, `true_peak_dbtp`}, `auto_subtitles`, `texts`, `sound_effects`, `render_time_sec`, `shot_details` [{`n`, `type`, `source`, `from`, `to`, `in_video`, `cut_sec`, `role`, `transition`}]. |

Written by other commands:

| File | Written by | Shape |
|---|---|---|
| `cache/products.json` | `sve products` (and `sve plan` with `vision.product_plan`) | `{"segments": [{"source", "start", "end", "best": [a, b], "label", "confidence", "score"}], "model", "language"}` |
| `cache/<id>_clip.npy`, `<id>_clip.json` | anything that uses the vision model | Frame embeddings of a source and their timestamps. |
| `vN/check.json` | `sve check` | `{"version", "ok", "errors": [], "warnings": [], "metrics": {}, "listen": ["listen/01_….wav"]}` |

