# social-video-editor (`sve`)

A Python + FFmpeg engine that turns raw phone clips into finished **vertical social videos**
(TikTok, Reels, Shorts). It analyzes your footage, proposes an editable plan, and renders it:
a hook in the first second, cuts that land on the music's beat, typed text cards with color emoji,
polaroid and collage pieces, ASMR-friendly audio, −14 LUFS mastering and correct handling of
iPhone HDR footage.

Everything the engine decides is written to a plain `plan.json` that you (or an AI agent) can edit
and render again. Originals are never modified.

## Features

- **Analysis** — brightness, sharpness, motion and color per frame (OpenCV), scene changes
  (PySceneDetect), loudness and sound peaks (librosa), word-level transcription (faster-whisper).
- **Automatic first plan** — scores every instant (motion, loudness, peaks, voice, keywords,
  sharpness, color), picks the best windows without cutting phrases or crossing scene changes,
  covers the whole footage, and starts with a *teaser* hook (the best moment first).
- **Beat-synced editing** — music beat grid and "drop" detection; the song can start so its drop
  lands at a chosen second; cuts snap to the nearest beat, or every cut lands exactly on a beat
  (`"beats": N` per shot); `beat_pulse` zooms on every beat.
- **Pieces** — video shots (speed ramps, freeze frames, zoom punch, push-in/pull-out, shake, flash),
  **polaroid** shots (last frame of a shot dropped in as an instant photo with a shutter sound),
  **collage** shots (frames of several shots popping in on the beat), and groups of shots.
- **13 transitions** — cut, fade, fade_black, flash, zoom, whip, slide_up, slide_left, circle, pixel,
  blur, radial, curtain (each with its own sound effect where it makes sense).
- **Text** — word-by-word auto subtitles with the active word highlighted; styled text cards
  (hook, label, card, counter, price, cta, simple) that pop in or are typed letter by letter with a
  soft keystroke sound; a card can have a hand-written-style description typed below its title;
  color emoji inside any text.
- **Audio** — denoise, high-pass, voice EQ, compression, extra gain for non-speech (ASMR) moments,
  an optional ASMR mode, music ducking under the voice (or under the ASMR sounds), sound effects
  (whoosh, pop, flash, keystroke, shutter — synthesized if you don't provide your own) and
  mastering to −14 LUFS / −1 dBTP (measured gain + oversampled limiter, repeated until on target).
- **Color** — iPhone HLG HDR → SDR (BT.709) via a generated 3D LUT, automatic levels, white
  balance and brightness matching across shots, optional creative `.cube` LUT and vignette.
- **Export** — 1080×1920 H.264 High / yuv420p / BT.709 + AAC 48 kHz, CRF 17 with a 20 Mbps cap,
  `+faststart`; platform profiles; `.srt` subtitles; a cover image; `plan_used.json`,
  `config_used.json` and `summary.json` next to every version. Unchanged shots are cached and not
  re-encoded.
- **Versions** — every render goes to a new folder (`v1`, `v2`…); variants keep the number of the
  latest version (`v3SinMusica` / `v3NoMusic`, `v3Borrador` / `v3Draft`, `v3Youtube`…).
- **Recipes** — layered YAML defaults per video type (unboxing, tutorial, review, vlog,
  before/after, storytime) that a project can override.
- **Cheap project overview** — `sve report` prints one screen (or JSON) with everything an editor
  or an AI agent needs to know about a project.
- **Product recognition (optional)** — a local CLIP model finds the products shown in the footage
  (`sve products`), picks windows from a text description (`search.text`) and proposes one shot
  per product in unboxing plans.

## Requirements

- **Python ≥ 3.11**
- **FFmpeg 7 or newer** (with `ffprobe`) on the `PATH` — the engine passes filter graphs with the
  `-/filter_complex <file>` syntax introduced in FFmpeg 7. On macOS: `brew install ffmpeg`.
- **macOS notes** (the engine is developed and tested on macOS):
  - Fonts are looked up by file name in `~/Library/Fonts`, `/Library/Fonts`,
    `/System/Library/Fonts/Supplemental` and `/System/Library/Fonts`. The default styles use
    [Inter / Inter Display](https://rsms.me/inter/) (install the `.otf`/`.ttf` files) and
    *Bradley Hand Bold* (ships with macOS); missing fonts fall back to Arial Black.
  - Color emoji are drawn with *Apple Color Emoji*.
  - **VideoToolbox** (hardware decoding, and encoding of the small analysis proxies) is used
    automatically when FFmpeg supports it, with automatic fallback to software;
    `SVE_HWACCEL=0` forces software. The final export always uses libx264.
- Other platforms are untested: fonts and emoji paths are macOS paths.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .              # the engine and the `sve` command
pip install -e ".[music]"     # + yt-dlp, for `sve music`
pip install -e ".[vision]"    # + sentence-transformers: local CLIP product recognition (optional)
pip install -e ".[dev]"       # + pytest
```

The same CLI is available as `sve`, `social-video-editor` and `python -m social_video_editor`.

## Quick start

The **workspace** is the folder given by `$SVE_WORKSPACE` (or the current folder):

```
workspace/
├── projects/            one folder per video (created by `sve new`)
│   └── my_video/
│       ├── project.yaml     name, recipe, sources (in story order), settings
│       ├── plan.json        the editable edit plan (`sve plan`)
│       ├── cache/           probes, analyses, proxies, rendered shots, contact sheets
│       ├── v1/  v2/ …       one folder per render: video, .srt, cover, plan_used.json, summary.json…
│       └── v2SinMusica/ …   variants of the latest version
├── music/               tracks (the first one is used unless a project picks another)
├── recipes/             your own recipes (override the packaged ones with the same name)
└── sfx/                 optional sound effects (whoosh.wav, pop.wav…); generated when missing
```

Spanish names written by earlier versions are accepted too: `proyectos/`, `musica/`, `recetas/`,
`proyecto.yaml` (and Spanish keys inside the files — see [docs/LEGACY_KEYS.md](docs/LEGACY_KEYS.md)).

```bash
export SVE_WORKSPACE=~/videos
sve new my_video --recipe unboxing --sources ~/clips/a.mov ~/clips/b.mov
sve build my_video            # analyze + contact sheets + plan (if missing) + render v1
sve report my_video           # one-screen overview
# edit projects/my_video/plan.json (or project.yaml settings), then:
sve render my_video           # → v2
sve render my_video --no-music   # → v2SinMusica (same beat-aligned cuts, no song)
```

## Commands

| Command | Spanish alias | What it does |
|---|---|---|
| `sve new <name> --recipe R --sources F [F…]` | `nuevo` (`--receta`, `--fuentes`) | Create `projects/<name>/project.yaml` (recipe defaults to `base`). |
| `sve analyze <project> [--force]` | `analizar` (`--forzar`) | Probe every source, make proxies/16 kHz wav, analyze image, scenes, audio and speech (cached). |
| `sve thumbs <project> [--source N --start S --end S --every S]` | `vistazo` (`--fuente --desde --hasta --cada`) | Contact sheets (frames with time, motion and loudness bars) + transcript in `cache/thumbs/`. |
| `sve plan <project> [--force]` | `planear` | Propose `plan.json` (an existing one is kept as `plan_previous.json` with `--force`). |
| `sve render <project> [--draft] [--profile P] [--no-music]` | `--borrador`, `--perfil`, `--sin-musica` | Render a new version folder (or a variant). Profiles: `tiktok`, `reels`, `shorts`, `youtube`, `square` (`cuadrado`). `--draft` = half resolution, fast encode. |
| `sve build <project> [render flags]` | `montar` | Analysis + contact sheets + plan if missing + render. |
| `sve view <project>` | `ver` | Plan and timeline, shot by shot, with the same beat alignment as the render (runs the analysis if it is missing). |
| `sve report <project> [--json]` | `informe` | One compact screen (≲50 lines) or JSON: sources, plan/timeline, texts, music, versions and their files, products, check results. Reads only files on disk — instant. |
| `sve recipes` | `recetas` | List the available recipes. |
| `sve music "<artist song or URL>"` | `musica` | Download a track with yt-dlp into `music/` and analyze its tempo, beats and drop. |
| `sve products <project>` | `productos` | Recognize the products shown in the sources with the local CLIP model (optional `[vision]` extra; downloaded on first use) and print them; writes `cache/products.json`. See [Product recognition](#product-recognition-optional). |
| `sve check <project> [--version vN] [--out DIR]` | `verificar` | Checks a rendered version and writes `vN/check.json` plus short audio excerpts in `listen/` (`escuchar/`). |

`sve report` is the cheapest way to understand a project. Example (synthetic project):

```
PROJECT demo · recipe unboxing · language es
SOURCES 2 · 08:14.50 total
  0 clip_a.mov 08:02.00 HDR | 1 clip_b.mov 00:12.50 mute
PLAN 5 shots · ~8.5 s before beat alignment · 1 group · 3 texts (hook 1, card 1, cta 1)
  0 hook s1 4.3-6.5 2.2s +zoom_punch | 1 opening s0 9.9-12.3 2.5s /flash | 2 s0 ?100-130 x2 0.7s
  3 polaroid<2 1.2s | 4 collage<0,1,2 2.4s /zoom
  first text: Was it worth it? 👀
MUSIC Some Song.mp3 · start_at auto (rendered from 18.73 s) · 120.0 BPM · tempo_factor 1.5
VERSIONS v1 v2 v2SinMusica v10_failed · latest v2
  v2           demo.mp4 · 21.4 s · 5 shots · -14.05 LUFS · +srt · check.json · listen/ 1 files
  v2SinMusica  demo_sin_musica.mp4 · 21.4 s · 5 shots · -14.05 LUFS · +srt
PRODUCTS 2 segments (test-model, es)
  earrings s0 12.0-15.5 c0.71 | necklace s0 30.0-33.0 c0.31?
  ⚠ 1 low confidence (<0.5, marked ?): look at those images (sve thumbs <project> --source S --start A --end B)
CHECK v2: FAILED · 1 errors · 2 warnings · listen: 1 files
  ...
```

Shot tokens read `index role source start-end [xspeed] duration [/transition] [+effects]`;
`?a-b` is a `search` window resolved at render time; `polaroid<k` / `collage<i,j` name the shots
they are drawn from.

## Recipes

Defaults live in [`recipes/base.yaml`](src/social_video_editor/recipes/base.yaml) (every key is
commented). Each recipe only overrides what it changes; a project overrides the recipe in
`project.yaml → settings` (deep merge: `base → recipe → settings`).

| Recipe | Formula |
|---|---|
| `base` | Generic defaults: 30 s target, teaser hook, 0.8–3.2 s shots. |
| `unboxing` | Hook with the result → opening with ASMR sounds → reveal with zoom → reaction / price → loop ending. |
| `tutorial` | Final result first → short numbered steps (waiting sped up) → result again; progress bar. |
| `review` | Verdict or question → the good → the bad → verdict + question to the audience. |
| `vlog` | Most striking moment → fast chronological sequence on the beat → emotional/funny ending. |
| `before_after` | The "after" in 1 s → "before" → sped-up process → reveal with flash/zoom. |
| `storytime` | Strongest line first → story without silences, big subtitles, almost no effects. |

Put `recipes/<name>.yaml` in your workspace to add a recipe or replace a packaged one. The first
line comment is shown by `sve recipes`.

## The plan (`plan.json`)

Shot times are seconds **inside each source**; text times are seconds of the **final video**
(or relative to a shot). Minimal example:

```json
{
  "shots": [
    {"source": 1, "start": 4.2, "end": 6.4, "role": "hook", "effects": [{"type": "zoom_punch"}]},
    {"source": 0, "start": 9.8, "end": 12.3, "speed": 1.5, "transition": "flash", "beats": 3},
    {"source": 0, "search": {"range": [120, 135], "dur": 1.8, "criterion": "product"}},
    {"type": "polaroid", "from_shot": 2, "beats": 2, "text": "my favorite"},
    {"type": "collage", "from_shots": [0, 1, 2], "beats": 8}
  ],
  "texts": [
    {"text": "Was it worth it? 👀", "shot": 0, "style": "hook"},
    {"text": "Earrings", "description": "pastel colors ✨", "shot": 2, "style": "card"},
    {"text": "Which one is your favorite? 💬", "start": 9.0, "end": 12.0, "style": "cta"}
  ]
}
```

Full reference: [docs/PLAN_FORMAT.md](docs/PLAN_FORMAT.md).

## Product recognition (optional)

A local **CLIP** model (through `sentence-transformers`, CPU by default, nothing leaves your
machine) can recognize the products shown in the footage. It is an **optional extra**:

```bash
pip install -e ".[vision]"
```

The first use downloads the models once (**≈1.2 GB**, to the Hugging Face cache); frame
embeddings are cached per source in `cache/<id>_clip.npy`. Without the extra the engine works as
before. What uses it:

- **`sve products <project>`** — always uses the model (and downloads it if needed): prints a table
  of the product segments (source, start–end, best window, proposed name, confidence, score) and
  writes `cache/products.json`, which `sve report` summarizes. Names come from a packaged list of
  ~80 small products in the project language (`vision.vocabulary` sets your own list).
- **`search.text`** in a shot — `{"source": 0, "search": {"text": "phone case", "range": [40, 60]}}`
  picks the window that best matches the description. It uses the model **whenever it is
  available**; otherwise it falls back to the `product` heuristic with a warning.
- **Unboxing plans** — the `unboxing` recipe sets `vision.product_plan: true`: with the model
  available, `sve plan` proposes one shot per detected product (a card with its proposed name and
  an `n/N` counter; the most confident `vision.stars` products get a fast → slow ramp and a
  polaroid; a collage at the end). Without the model, the regular plan is made.
- **CLIP product criterion (opt-in)** — with `vision.product_search: true`, a `search` with
  `criterion: product` scores frames with the model instead of the sharpness/color heuristic.
  It is off by default because it changes the windows of existing plans.

"Available" follows `vision.enabled`: `auto` (default) = used when installed **and** already
downloaded; `true` = may download it; `false` = never (except `sve products`). All keys:
[docs/PLAN_FORMAT.md](docs/PLAN_FORMAT.md#vision).

## Language

The default **content language is Spanish** (`language: es`); English (`en`) is the other
supported value. The code, logs, CLI and file keys are always English. The language affects:

- **Transcription** — Whisper's language (`transcription.language: null` follows `language`).
- **Localized defaults** — any recipe value written as `{es: …, en: …}` takes the value for the
  configured language: the keyword lists that boost the scoring (`selection.keywords` in
  `unboxing`, `tutorial`, `review`) and the Whisper context prompt (`transcription.context` in
  `unboxing`). On-screen texts are never generated in a language: you write them in `plan.json`
  (the recipes ship with `hook.text: null`).
- **Version folders and file names** — variants and output files are named in that language:

  | | `es` (default) | `en` |
  |---|---|---|
  | `--no-music` | `v3SinMusica/…_sin_musica.mp4` | `v3NoMusic/…_no_music.mp4` |
  | `--draft` | `v3Borrador/…_borrador.mp4` | `v3Draft/…_draft.mp4` |
  | `--profile square` | `v3Cuadrado/…_cuadrado.mp4` | `v3Square/…_square.mp4` |
  | cover image | `…_portada.jpg` | `…_cover.jpg` |
  | failed render | `v4_fallida/` | `v4_failed/` |

**To change it**, set `language` in a recipe or in the project's settings:

```yaml
# projects/my_video/project.yaml
name: my_video
recipe: unboxing
sources:
  - path: /path/to/clip.mov
settings:
  language: en              # es | en
  transcription:
    language: es            # optional: transcribe Spanish speech while naming files in English
```

Legacy Spanish project files work the same way: `ajustes: {idioma: en}` in `proyecto.yaml`.
To make English the default for every project in a workspace, copy a recipe into
`<workspace>/recipes/` and add `language: en` at the top (a workspace recipe replaces the packaged
one with the same name). Any other value is rejected (`Unknown language`).

## Testing

```bash
python -m pytest -q                       # everything (integration tests need FFmpeg)
python -m pytest -q -m "not integration"  # fast unit tests only
```

Integration tests generate synthetic media in a temporary folder; nothing is written outside it.

## Architecture

Hexagonal (ports and adapters): a pure `domain/`, `ports/` (Protocols), `adapters/` (FFmpeg,
OpenCV, librosa, Whisper, Pillow, files, yt-dlp), `application/` use cases, and `container.py`
wiring them together. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The research behind the
defaults is summarized in [docs/RESEARCH.md](docs/RESEARCH.md).

## Music and copyright

Tracks you mix into the video are your responsibility. Platforms may mute or limit videos with
copyrighted music; `--no-music` renders the same edit without the song so you can add a sound
from inside the app instead.

## License

[MIT](LICENSE)
