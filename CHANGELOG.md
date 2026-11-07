# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
