# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- A script sentence no longer begins or ends inside a stretch of speech: words said right against its edges
  that the transcription split or misheard are kept, so no word is cut in half.

### Added
- **Clean voice** (`audio.clean_voice`, on in `explainer` and `talk`): the background noise of each recording is
  taken out once, over the whole recording, with RNNoise (FFmpeg `arnndn`; a ≈300 KB speech model downloaded
  once to the user cache, FFT denoiser without it or with `SVE_OFFLINE=1`); every shot reads its piece of the
  clean track, so there are no seams at the cuts. Measured on a real selfie recording: noise −14.6 dB, voice −1.5 dB.
- **More notes around the presenter**: up to four per sentence, 0.6 s apart, six-letter key words, notes on the
  camera part of a sentence that ends in footage, and proposed again after `sve make` changes the cutaways;
  `callouts` / `bullets` end when a full-frame cutaway comes in.
- **`sve make` reviews and fixes itself** (`domain/review.py`, `review:` section). Before rendering, the plan is
  placed on the video and checked for cutaways too short to be read, text too long to read in time
  (`review.read_cps`; words the voice says count less), full-frame graphics held too long
  (`review.graphic_max`), the presenter alone too long and sentences missing from the recording; the
  engine applies a fixed remedy (start a word earlier, shorten an engine card, end a graphic earlier,
  ask the director for footage, remove what it proposed) and fetches footage again. After the render,
  what `sve check` finds on screen (a clip that stops moving) is fixed and rendered again, at most
  `review.rounds` renders. What it could not settle is a short list of **decisions** with options,
  printed, written to `<project>/review.json` and shown by `sve report`. `check.json` now lists the text
  boxes outside the safe zone. Spanish: `revision: {vueltas, lectura_cps, grafico_max, holgura_camara}`.
- **The director draws data said aloud**: figures compared in a sentence become `bars` (times put in one
  unit, bars named by "before / today" or the words next to them) and a quoted source ("according to X")
  becomes a `headline`, shown when it is said instead of searched for.
- **Soft joins in the explainer.** Camera shots are joined with a 0.2 s `dissolve` (new transition;
  `script.transition`), centered on the cut so no word is dimmed; full-frame graphics and clips fade in and out
  (`script.cutaway_fade`), and two in a row dissolve one over the other.
- **Natural look for the explainer** and `color.per_source`: one color correction measured over the whole
  recording, so the light does not jump at the cuts.
- **Third-party footage with a record: `sve broll`** (alias `tomas`; `--dry-run`, `--force`). Every
  `[broll query="…"]` cue / `broll` cutaway gets a clip from the sources of `broll.sources`: Pexels and
  Pixabay (free API keys in `PEXELS_API_KEY` / `PIXABAY_API_KEY`), Wikimedia Commons (only CC0, public
  domain, CC BY, CC BY-SA) and the official channels of the brands listed in `broll.brands` or a pack's
  `brands.yaml` (searched inside the channel, filtered by channel id, only the piece used is
  downloaded). Candidates are ranked for the frame; the vision model, when available, keeps the clip
  and the piece that show the query best. Writes `footage/<clip>`, `footage/credits.json` (source, page,
  author, license, SHA-256, date, query), the cutaway's `file` / `clip_start`, a credit caption the first
  time each author or brand appears, and `vN/credits.txt` at render time. A `broll` cutaway is now
  rendered (also with your own clip: `broll file="…"`). Spanish: `tomas_externas`, `[toma consulta="…"]`.
- B-roll avoids **printed text** (titles, captions, slide labels found with OpenCV, no model; `broll.text`)
  and crops a clip of another shape **where the query shows best** (`focus_x` / `focus_y`); only the piece
  that is used is stored, checked again for text in full quality.
- `lower_third` is smaller and sits in the bottom-left corner of the safe zone.
- **The director** (`script.auto_broll`, on in the `explainer` recipe): where the presenter would stay
  alone on camera longer than `script.opening` / `script.max_camera`, `sve plan` proposes b-roll that
  starts at a word of the sentence (`from_word`), with a search built from what is said. When no clip
  passes, `sve broll` puts an illustrated title card with what is said instead (`broll.fallback_card`).
- **`sve make`** (alias `hacer`): plan if missing, footage, render, review and summary with one command.
- B-roll is judged by **what is said** (the sentence, not only the search) and by **how alive it looks**
  (`broll.min_interest`, `broll.lively` / `broll.dull`); the libraries' stills are judged before any video
  is downloaded (`broll.thumbnails`); director searches go out in English and in the video's language.
- New `scene` template: an animated illustration doing what the sentence says (swipe, open, sleep, drain,
  charge, send, store, freeze, clean, work, wait, faster, slower). When no clip is good enough the director
  draws a `number`, a `list`, a `scene` or a `title`, by what the sentence says, never the same kind twice.
- B-roll never uses the same clip in two places, and its credit caption starts with the clip (`from_word`).
- B-roll: clips are framed by **where their subject is** (cropped around it, or shown whole and large
  over a blurred copy: `fit`), and a clip that does not look like the query is not used
  (`broll.min_match`).
- Fixed: shots framed with blurred borders (`framing.mode: blur`) showed green bands when the source was
  decoded by the hardware (VideoToolbox); segment caches are rebuilt once (`SEGMENT_VERSION` 7).
- `sve check`: `third_party_too_long`, `third_party_share` and `footage_without_record` warnings for
  third-party footage (`broll.max_seconds`, `broll.max_share`); `sve doctor` lists the b-roll sources and
  what each lacks; `sve report` counts the b-roll cues still without a clip.
- `sve render --profile …` with another frame shape frames the explainer plan's shots on the face again
  for that shape (a focus edited by hand is kept).
- `diagram` in vertical: larger illustrations, a light behind the thing the marker reaches, and the steps
  counted off (1, 2, 3…) in the corner the zigzag used to leave empty.
- **From a script to the video: the `explainer` recipe.** `script.md` holds what the presenter says,
  sentence by sentence, with `[cues]` for what is on screen (`[bars data="…"]`, `[photo file="…"]`,
  `[lower_third …]`, `[camera]`; `seconds=N`). `sve plan` finds every sentence in the word-level
  transcription of the recording (tolerant to misheard words), uses the last good take of each
  (`script.take`), keeps them in script order, joins sentences said in a row, shortens long pauses and
  alternates a punch-in at every visible jump cut (`script.punch_in`). Missing sentences are reported,
  never invented. Settings `script.*`; Spanish: `explicativo`, `guion` (`guion.md`, `[barras …]`,
  `[camara]`, `[foto …]`).
- **Cutaways** (`plan.cutaways`): graphics and photos shown over the main track while its sound keeps
  playing, anchored to script sentences (`at_sentence`, `until_sentence`, `seconds`) or to seconds of
  the video (`start`, `end`), in any plan. They stay long enough to be read, never overlap, and
  `lower_third` is laid over the picture with transparency. Rendered once and cached; their sound
  effects are mixed in; the audio of the main track is untouched.
- Subtitles of an explainer plan use the script's own words, timed by the voice
  (`script.subtitles: script`), so misheard words are written right.
- **Picture-in-picture** (`pip.*`, off by default): over a full-frame graphic or photo the presenter stays
  in a corner window with a paper frame (the `pip_frame` graphic), cropped around the face, and the
  graphic is laid out in the room the window leaves. Per cutaway: `pip=yes` / `pip=no`.
- **Overlays for a presenter on screen**: `callouts` (illustrated notes that pop up around the presenter,
  optionally linked into a small diagram) and `bullets` (short lines, one by one), each item at its own
  time (`times`). With one item per sentence under the cue, items follow the sentences.
- **Notes proposed by the engine** (`script.support`): where the presenter is alone on screen, `sve plan`
  adds `callouts` for figures, things that have an illustration and nouns, each timed to its word
  (entries marked `_auto`, shown with `~` in `sve report`).
- **One camera, several framings**: long shots are split between sentences without losing a frame
  (`script.reframe_every`, `continues`), visible cuts rotate wide → punch-in → wide → close-up
  (`script.close_up`), and every camera shot
  drifts slowly (`script.drift`). `sve check` does not count those picture-only cuts as cuts of the sound.
- `framing.face`: finds the presenter's face (OpenCV 4 Haar cascade, or YuNet on OpenCV 5 with a
  ≈230 KB model fetched once into the user cache) and frames the explainer plan's shots on it.
- `sve report` shows `SCRIPT` (sentences found / missing, takes used) and `CUTAWAYS` (what, when);
  `sve view` lists the cutaways; `summary.json` records `cutaways` and `cutaway_warnings`.
- **Animated graphics drawn from data** (`graphic` shots, optional `[graphics]` extra with skia-python):
  templates `title`, `bars`, `number`, `product_card`, `headline`, `list`, `diagram` and `lower_third`,
  each written as a few key/value pairs (or a one-line cue), laid out for vertical or landscape at the
  largest size that fits the safe zone, with their sound effects. Diagram nodes with a known name are
  drawn as illustrations (phone, router, globe, cloud, server, laptop, chip, ram, disk, battery, tower,
  app, user, lock; matched in Spanish and English). Every graphic sits on scenery (grid, circuit traces
  with pulses, chips, twinkling bits) and keeps moving after it has come in. See `docs/GRAPHICS.md`.
- Graphics themes and **packs** (`<workspace>/packs/<name>/`): a channel's colors, inks with a fixed
  meaning, fonts (variable-font instances), "printed" look (ink outlines, halftone shadows, registration
  offsets, text that prints, objects on twos, stroke wobble, paper grain) and its own elements,
  templates and illustrations in Python. Settings: `graphics.pack`, `graphics.theme`.
- `sve graphic` (`--proof`, `--shot N`, `--cue '…'`, `--pack`, `--video`): previews graphics in seconds
  without rendering the video.
- `sve doctor`: checks FFmpeg (version, filters, encoders), fonts, optional parts and the workspace.
- `sve report` shows graphic shots by template.
- `sve plan` shortens long pauses inside every shot with voice, in every recipe (`pauses.*`: on by
  default, 0.9 s pauses kept at 0.45 s; `storytime` tighter), joined with the new `soft_cut`
  transition (0.12 s crossfade of sound and picture). Pieces carry `pause_cut` and are never moved
  to the beat. Existing plans are unchanged.
- `talk` recipe (meetings, classes, screen recordings): the whole recording in order with only the
  long pauses shortened; neutral per-shot processing so nothing jumps at a cut.
  Spanish keys: `charla`, `pausas` (`pausa_minima`, `pausa_dejada`, `silencio_db`, `tramo_minimo`,
  `activo`, `transicion`).
- `haul` recipe: a calm "aesthetic" unboxing (real speed, plain cuts, every product, one title and a
  closing call to action, steady music over the ASMR sounds).
- Product plan options: `vision.cards`, `vision.counters`, `vision.collage`, `vision.product_effect`,
  `vision.opening_max`; `hook.text_duration`; a closing `cta` text (`cta.text`, `cta.duration`).
- Text styles without a background take `stroke` and `stroke_color` (outlined text in any color).
- `vision.unpacked` / `vision.packaging`: the product plan shows each product out of its box or wrapper
  instead of the packaging (on in `haul`).
- `color.reference`: copy the color look of a reference video (brightness, black/white points,
  saturation, color cast), with `color.reference_strength`.
- Color look: `color.fade` (lifted blacks, softer whites) and `color.tint` (green ↔ magenta).

### Fixed
- One or two frames of the presenter showed between two full-frame cutaways in a row: clips are now
  laid on the frame grid and hold their last frame to the end of their window.

## [0.5.1] - 2026-11-04

Measured with `scripts/bench.py` on a 10-CPU Apple M1 Pro (16 GB), FFmpeg 9.0.2, Python 3.13; median
of 3 runs after a warm-up. Changes that did not pass their criteria were left out (see "Not adopted").

| scenario | 0.5.0 (s) | 0.5.1 (s) | change | shot stage | finish stage | max RSS (MB) |
|---|---:|---:|---:|---:|---:|---:|
| `vertical60` (60 s, 4K HEVC 30 / 60 fps) | 128.8 | 101.2 | −21.4 % | −25.6 % | −13.8 % | 2625 → 3066 |
| `slideshow600` (10 min of photos) | 493.5 | 472.0 | −4.3 % | −3.9 % | −4.2 % | 2862 → 2896 |

### Changed
- Shot segments apply speed and the output frame rate before any scaling: the frames a 60 fps source
  drops are no longer scaled from 4K, tone-mapped, corrected and zoomed. 38–44 % faster per 60 fps 4K
  shot, no change at 30 fps; the segment is identical for 30 / 60 fps sources at speed 1 (a fraction
  of a frame apart otherwise, because the zoom is evaluated at the output frame's time).
  `SEGMENT_VERSION` 6: cached segments are rebuilt once.
- `render.jobs: auto` is now half the CPUs between 1 and 3 (was 1–4). On `vertical60`:

  | jobs | wall (s) | vs 1 job | max RSS (GB) |
  |---|---|---|---|
  | 1 | 148.2 | — | 2.70 |
  | 2 | 124.3 | −16.1 % | 2.66 |
  | 3 | 122.6 | −17.3 % | 2.28 |
  | 4 (0.5.0's auto here) | 128.8 | −13.1 % | 2.63 |

- Audio mastering encodes only the AAC in each loudness pass and muxes the video once at the end
  (`-c copy -movflags +faststart`); `render.long_master_sec` is gone (it is ignored if set).
- Draft renders encode photo and drawn shots (polaroid, collage, text cards) with the draft settings
  (ultrafast, CRF 22) like video shots; `draft` is part of their cache signature.
- Temporary file names and atomic publishing are shared (`adapters/files/atomic.py`); the Real-ESRGAN
  JPEG and the CLIP embedding cache use them too.
- `scripts/bench.py` decodes its subprocess output as UTF-8.
- Scene times are read through PySceneDetect 0.7's `seconds` property (`get_seconds()` is deprecated
  there; 0.6 still works).

### Fixed
- Zooms grew from the top-left corner: the subject drifted down and right, and `focus_x` / `focus_y`
  and the shake did nothing while a zoom was running (since before 0.4.0). The zoomed frame is now
  padded onto a fixed canvas and cropped about the focus point. Same speed and sharpness (Laplacian
  variance within −2 %); shots with a zoom look different (better framed) than in 0.5.0 (SSIM(Y)
  0.87–0.89 against its off-centre output), and a shake now moves the frame during a zoom too.
- OpenCV readers and scene detection open videos in folders with non-ASCII names when OpenCV cannot
  open the path itself (some Windows builds): the file is handed to OpenCV as a stream.

### Documentation
- ARCHITECTURE: hardware backends (NVENC, VAAPI, Quick Sync, VideoToolbox), the `auto` order,
  `SVE_HWACCEL`, `SVE_VAAPI_DEVICE`.
- PLAN_FORMAT: `vision.device` accepts `auto`; `enhance.device: auto` = CUDA → MPS → CPU; where the
  Real-ESRGAN model is looked up.

### Not adopted (measured)
Per 4K shot, relative to 0.5.0's filters (A/C/D 60 fps sources, B/E 30 fps):

| change | time | sharpness (Laplacian var.) | SSIM(Y) | why not |
|---|---|---|---|---|
| `cas` after the zoom (M4) | −17 to −23 % | +25 to +34 % | 0.988–0.998 | below the 25 % bar; sharpness off by far more than ±5 % |
| `perspective` zoom (M2) | +1 to +18 % | −30 to −44 % | 0.875–0.893 | slower and blurrier |
| `perspective` zoom, input counter (M3) | +12 to +38 % | −19 to −35 % | 0.872–0.893 | slower and blurrier |
| `zoompan` | — | — | — | whole-pixel steps (visible stair-stepping) and blur |
| fps-first + M3 + M4 | −30 to +23 % | −15 to +4 % | 0.865–0.891 | slower at 30 fps, SSIM |

Intermediate preset `fast` → `veryfast` (CRF 12, shot segments, photo / drawn shots and long-render
chunks): final export −9.6 % (`vertical60`, 101.2 → 91.5 s) and −9.7 % (`slideshow600`, 472.0 →
426.4 s), below the 10 % bar, and the final export moved by more than 0.001 SSIM (SSIM(Y) between the two
exports 0.9930 and 0.9979). Kept `fast`.

### Not tested
- FFmpeg 7.x and 8.1 locally (only 9.0.2 installed here; CI covers 8.1 on Windows).
- NVENC, VAAPI and Quick Sync on real GPUs.
- Windows other than through CI.

## [0.5.0] - 2026-11-04

### Added
- Windows 10 / 11 support: FFmpeg and Rubber Band are found on the `PATH` (`ffmpeg.exe`), fonts in
  `%WINDIR%\Fonts` and `%LOCALAPPDATA%\Microsoft\Windows\Fonts` (Arial / Segoe UI fallbacks, Segoe UI
  Emoji), caches in `%LOCALAPPDATA%\social_video_editor` (`XDG_CACHE_HOME` still wins). A missing FFmpeg
  is a clear error with install hints.
- CI job on `windows-latest` (Python 3.12, FFmpeg 8.1): ruff, the full test suite and the CLI with
  redirected (cp1252) output.
- Parallel rendering: video and photo shots of the same dependency wave are encoded at the same time
  (`render.jobs`, default `auto` = half the CPUs, 1–4; `SVE_JOBS` overrides it). Polaroid and collage
  shots stay serial. Output is identical for any number of jobs (decoded frames compared in tests);
  the speed-up has not been benchmarked yet.
- Hardware acceleration for analysis proxies and drafts: NVENC, VAAPI and Quick Sync besides
  VideoToolbox, chosen after a one-frame test encode, with fallback to software (`SVE_HWACCEL`,
  `SVE_VAAPI_DEVICE`). The final export stays libx264. Untested on real GPUs.
- `scripts/bench.py`: render benchmark on synthetic 4K HEVC footage (60 s vertical, 10 min slideshow,
  `--quick`), with `--repeat` and `--compare`; helpers shared with `make_demo.py` in `scripts/_synth.py`.
- numba's compiled code is cached in the user cache folder (`NUMBA_CACHE_DIR`): the first song analysis
  of a process took 13 s without it and ~3 s with it.
- Torch models (Real-ESRGAN, and CLIP with `device: auto`) pick CUDA, then MPS, then CPU.

### Changed
- Every path inside an FFmpeg filter (LUTs) or concat list is escaped in one place, so drive letters,
  quotes, colons and spaces work.
- FFmpeg / ffprobe / yt-dlp / Whisper output is decoded as UTF-8; log lines and the console never crash
  on a legacy code page.
- Temporary files have unique names and are published with an atomic replace; failed render folders
  get a free name and survive Windows file locks.

### Fixed
- FFmpeg 7.x could abort a chunked (long) render with "Assertion best_input >= 0 failed": each chunk's
  graph now ends at the chunk border by itself instead of being cut by the output (~2% of runs on 7.1.1
  under load before; the test that hit it: 0/60 runs after); the runner also retries that abort up
  to twice.
- FFmpeg 8.1 could hang forever in `encode_frames` when the encoder failed while frames were piped in;
  a watchdog now stops it and reports FFmpeg's error.
- A path with `'` silently broke a LUT filter on every OS; a `C:\…` path broke it on Windows.
- Missing fonts no longer fall back to Pillow's tiny bitmap font.

## [0.4.0] - 2026-11-03

### Added
- Linux support: fonts are found in the Linux font folders (`~/.local/share/fonts`, `~/.fonts`,
  `/usr/local/share/fonts`, `/usr/share/fonts`, sub-folders included, case-insensitive names); a
  missing font falls back to an installed bold sans-serif; color emoji use Noto Color Emoji when
  Apple Color Emoji is not there, and are left out (no crash) when no emoji font is installed.
- Docker image: Python 3.12, FFmpeg 7, Rubber Band, OpenCV's system libraries and the default fonts;
  entry point `python -m social_video_editor`, runs as an unprivileged user, `/workspace` mount.
- CI workflow: ruff, the full test suite on Python 3.11 and 3.12 with FFmpeg 7.1 (Debian 13), and a
  Docker build with a smoke test.
- ruff configuration in `pyproject.toml`; `ruff` added to the `dev` extra.
- `SVE_DECODER_THREADS` environment variable: decoder threads for shot sources (a number, or
  `0` / `auto` for FFmpeg's default).
- AutoMix settings `short_loud_db` and `short_search` for short crossfades.
- `scripts/make_demo.py`: renders the README demo GIFs from synthetic media only.
- README: CI badge, Demo, Design decisions, Roadmap and Docker sections; this changelog.

### Changed
- AutoMix short (not beat-matched) crossfades pick their points on the songs' level envelopes: the
  outgoing song leaves at its last loud phrase end, the incoming one enters on its first loud
  downbeat (quiet intros are skipped). Their make-up gain follows the BS.1770 momentary loudness of
  the rendered overlap, is re-measured after the limiter and is slew-limited. Cached AutoMix plans
  and mixes are rebuilt once (new plan and render versions).
- `sve recipes` shows each recipe's first meaningful header line instead of a decorative rule.
- The package `__version__` matches `pyproject.toml` again (it still said 0.2.0).

### Fixed
- FFmpeg 7.1: segment joins reset timestamps before `fps` (7.1's `xfade` rejects inputs without a
  frame rate), and audio mastering allows up to 6 passes (7.1's AAC encoder overshoots the true peak
  more).
- Shot rendering could hang forever on FFmpeg 7.0 – 8.0 when the source decoder used 5 or more
  frame threads (FFmpeg's default on 4+ cores) while the audio chain denoised and padded. On those
  versions, and when the version is unknown, shot sources decode with 2 threads; FFmpeg 8.1 and
  newer keep FFmpeg's default.
- Test collection no longer imports torch: the Real-ESRGAN availability check looks for spandrel and
  torch without importing them, and a broken install falls back to Lanczos when it is used.
- The CLIP readiness test no longer needs the `vision` extra.

## [0.3.0] - 2026-11-02

### Added
- `sve report` (alias `informe`): one-screen project summary, `--json` for agents.
- `sve check` (alias `verificar`): loudness, true peak, clipping, silences, cut jumps, stem
  balance, typing density, harshness, black / frozen frames, beat deviation and safe-zone text
  boxes; writes `check.json` and short `listen/` snippets (`--out` to write elsewhere). Renders
  write music / original / sfx stems and text box positions for it.
- Tempo disambiguation (T/2, T, 2T scored by pulse clarity) with `music.bpm` and
  `music.tempo_factor` overrides.
- Varied typing sound: seeded per-keystroke pitch, level and timing.
- Local CLIP product recognition (`vision` extra): `sve products` (alias `productos`),
  `search.text` to find a shot by description, product-based unboxing plans
  (`vision.product_plan`) and an opt-in product criterion for search (`vision.product_search`).
- Landscape output (`landscape` / `horizontal`, 1920×1080) with orientation-aware safe zones;
  text and margins scale with the short side, so vertical output is unchanged.
- Photo slideshows: image files or folders as sources in EXIF capture order (orientation applied),
  a `photo` shot with sub-pixel Ken Burns motion, blurred-background framing, optional warmth /
  vignette and `photo.auto_tone`; a `slideshow` recipe.
- `sve enhance` (alias `mejorar`): Real-ESRGAN x4 upscaling (`enhance` extra) with a Lanczos
  fallback; cached copies, originals never modified.
- `music.playlist` with AutoMix: loudness matching, silence / tail trimming, phrase-aligned out and
  in points, beat-matched tail stretch when tempos are close, equal-power crossfade with bass swap.
  `photo.fit_to_music` puts photo changes on beats and song boundaries.
- Long renders: chunked compose with bounded memory, block-wise audio checks, optional stems,
  `output.fade_in` / `output.fade_out`, exact audio / video length.
- Documentation: README, ARCHITECTURE, PLAN_FORMAT, RESEARCH, CONTRIBUTING; MIT license.

### Changed
- Mastering shares one ebur128 (BS.1770) measurement with `sve check`, with a gain cap, an
  always-on true-peak limiter, post-encode verification and a non-convergence warning.
- Polaroid and collage shots play the continuing source audio at −6 dB.
- Typing clicks are thinned to `sfx.typing_max_per_sec` (16).
- The original beat grid is kept when the detected tempo is confirmed, so existing projects keep
  their cut times.

### Fixed
- Short renders landing louder than the loudness target.
- Source cache ids include a hash of path, size and mtime (no collisions between same-named files).
- Any render failure marks the version folder as failed.
- Probe falls back to `r_frame_rate`, then 30 fps.
- Group / Sequence durations account for transition overlaps.
- `encode_frames` cleans up FFmpeg and surfaces its errors.
