# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
