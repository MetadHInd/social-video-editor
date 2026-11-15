# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Landscape renders: subtitles are raised into the safe zone `sve check` measures (a real 43 s explainer at
  1920×1080: 13 safe-zone warnings → 0).
- A full-frame clip held under the next one while that one fades in now stays on screen with FFmpeg 7 too
  (the overlay repeats its last frame — `eof_action=repeat` — instead of `tpad`, which FFmpeg 7 ignored there:
  one frame of the presenter showed through).
- A script sentence no longer begins or ends inside a stretch of speech: words said right against its edges
  that the transcription split or misheard are kept, so no word is cut in half.
- Illustrations and scene actions are read from whole words only: a label with no word that names an
  illustration has none (no more picture from a piece of a word), punctuation around a word no longer hides it
  (`celular.`), and `tus`/`your`-style words never pick the person. A scene's verb is read by its forms:
  negated verbs (`no tienes que limpiarlo`, `you don't have to clean it`, `sin cerrarla`) and look-alikes
  (`abiertas`, `abril`, `cuesta`, `tarde`, `trabajar` read as `freeze`, `quite`) no longer trigger an action;
  `cargar` without battery words is loading (`wait`) and `save` without them is `store`.

### Added
- **Shorts from the same project** (stage 7): `[short name]` … `[/short]` (Spanish `[corto nombre]` … `[/corto]`)
  in an explainer script; `sve render <project> --short name` / `--shorts` (`--corto`, `--cortos`) renders each as
  a vertical video of its own (its sentences, the cutaways inside, framing, subtitles and sound; a variant folder
  such as `v3CortoName` that never overwrites). The long video's plan is the same (plus `shorts`).
  `script.shorts: auto` (off) proposes self-contained stretches of 20–60 s by a simple, explained score (starts
  with a question / hook, holds a graphic or a figure, ends on a statement, ≈40 s), recorded with `_auto: shorts`,
  `score` and `why` (`domain/shorts.py`).
- **Subtitles in the narration box** (`subtitles.style: caption`, Spanish `estilo: caja`; default unchanged): the
  pack's paper box (`kit.caption`) with the word being said marked, same groups and timing as the default
  subtitles, kept inside the safe zone, one transparent graphic track over the video, both orientations.
- **A voice memo as the recording**: a source with sound and no picture gets a plain proxy for the analysis and
  its shots are drawn on the theme's moving backdrop (never black), with the same voice chain.
- Graphics: a file a graphic names (`image` of `screen` / `product_card`) is found in the project folder, like a
  photo, in `sve render`, cutaways, `sve plan` and `sve graphic <project>`; a theme font role may be a list of
  fonts tried in order, and `mono` falls back to a monospace font (DejaVu Sans Mono, Consolas, Courier New…),
  never to a proportional one.
- `summary.json`: `short`, `breaths` (what `audio.breaths` lowered, per recording) and `music_levels` (what
  `music.relative_to_voice` measured).

- **Voice and music levels** (stage 6, `domain/voice_levels.py`): `audio.breaths` (Spanish `respiraciones`; on in
  `explainer`) lowers the pauses between sentences — breaths, mouth noise — by 12 dB in the same whole-recording
  pass as `clean_voice`, with short fades inside the pause and margins around the speech and the transcribed
  words (word starts and soft endings are never touched; measured on a real 47 s selfie: pause energy −8 dB,
  speech and word onsets unchanged). `audio.voice_leveler` (off: measured as a trade-off, not a clear gain) adds
  a slow leveler to that pass. `music.relative_to_voice` (Spanish `relativa_a_voz`; on in `explainer`): the music
  level is measured against the voice of the rendered segments — `under_voice_db` (10) under it while it speaks,
  `under_pause_db` (4) in pauses of 0.6 s or more, `intro_db` (4) louder before the first word, slew-limited and
  dipping just before each word — and mixed as a prepared bed; the measured numbers go to
  `vN/music_levels.json`. Without it the fixed music chain is unchanged. `sve check` reports how far the music
  sits under the voice (stems) and warns under `check.music_min_under_voice_lu` (6).
- **Local decider** (`decider:` section, Spanish `decisor:`; on in `explainer` with `auto`): the engine's language
  and picture judgments become closed questions (`domain/decide.py`). The ones the rules are unsure of are
  answered by local models, nothing paid per use: a zero-shot classifier (mDeBERTa XNLI, MIT, `[decider]` extra),
  then a language model through Ollama (`qwen3.5:9b`, Apache-2.0, which also sees a frame of borderline b-roll).
  A model only replaces an answer with a valid, surer one; answers are cached per project, one model is in
  memory at a time, and with no models the plan is byte-identical. Covers notes (verbs are not notes), card
  titles, bar labels, scene actions, card illustrations, the drawing chosen, shortened cards and borderline
  clips. Entries a model chose carry `_decided`. `sve doctor` reports both models. Routes measured on 215
  labeled real questions: the language model answers notes, titles, shortened cards and borderline clips;
  the rest stays with the rules (they were right more often). A failed answer never empties a choice, a
  several-answer question has a "none" option, and a shorter version never drops a negation. Notes: the model
  marks which words are conjugated verbs (every other word stays a note, so the presenter keeps as many notes);
  b-roll: only a clip the model is sure is bad is turned down, and the next candidate is fetched instead of a card.
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
- **34 more illustrations** (48 in all), each chosen by its Spanish and English words: `signal`, `building`,
  `shower`, `tap`, `clock`, `moon`, `download`, `gauge`, `crowd`, `video`, `cable`, `satellite`, `keyboard`,
  `shield`, `key`, `bug`, `folder`, `file`, `photo`, `camera`, `mic`, `speaker`, `message`, `bell`, `charger`,
  `trash`, `search`, `gear`, `money`, `calendar`, `pin`, `rocket`, `idea`, `warning` (two- and three-word names
  too: `hora pico`, `disco duro`). **Seven more scene actions**: `compare` (a balance), `search` (a magnifier
  that finds a spot), `connect` (a cable that plugs in), `delete` (into the trash), `download` (pieces fall in,
  a progress bar fills), `share` (one stream split among many), `queue` (a line waits at a gate). `freeze` now
  has frost ferns, an icy glaze, icicles that drip and real snowflakes; `store` ends with the thing kept beside
  what was stored before and the place it left marked.
- `sve broll <project> --unused` (`--sin-usar`) lists the clips no plan uses any more and the search cache, with
  their size; `--yes` (`--si`) deletes them (clip, record, cached frame). A clip a plan names is never deleted.
- `sve report` starts with `TO DO`: what the engine could not settle on its own (decisions left by `sve make`, a
  failed or missing check, b-roll to fetch, unused clips, sentences not recorded, sources not analyzed…), each with
  the command that resolves it; long sections collapse to their first items and `… N more (--full)`. New
  `--full` (`--completo`); `--json` keeps everything and adds `todo` and `footage`. The latest version is a draft
  when only drafts were rendered since the last final one (it said "none yet" for projects made with `sve make`).
  Measured on synthetic projects of each recipe: unboxing 24 → 22 lines, haul 26 → 21, talk 19 → 15,
  slideshow 9 → 12, a busy explainer 62 → 50.
- Slideshows: `photo.skip_duplicates` (`omitir_duplicadas`) shows only the sharpest of the photos that show the same
  scene in a row, and `photo.skip_blurred` (`omitir_borrosas`) leaves out clearly blurred ones (sharpness and lost
  fine detail, measured once per photo in `cache/photo_quality.json`). Both off by default; what was left out and
  why is in `plan.json` (`skipped_photos`), the log and `sve report`.
- Tests: the b-roll and check tests that render tiny synthetic videos use software x264 (`tests/software_encoder.py`;
  the hardware path keeps its own tests), about a quarter faster and steady when the GPU is busy.
- **Four graphic templates and diagram flows** (`docs/GRAPHICS.md`): `line_chart` (values over time; the line
  draws itself, points pop, the highlighted value is stamped; one or two series), `versus` (two things face to face,
  row by row, the better side checked and the winner crowned), `code` (a terminal or an editor where the lines are
  typed, with output and a marked line; theme font `mono`) and `screen` (a screenshot in a browser / phone frame
  that zooms to a region with a ring, or shows it in a loupe in the band the frame leaves empty). A `diagram`
  whose thing has two arrows out is laid out as a flow (branches and loops around; `layout: chain|flow`); chains
  draw exactly as before. Spanish names: `lineas`, `frente_a_frente`, `codigo`, `pantalla` and their keys.
  `domain/graphics/suggest.py` turns a sentence or data into these specs so the engine can choose them; `kit.caption`
  draws a subtitle line anywhere with the word being said marked. All are in the style proof.
- **The director reads sentences more carefully** (no setting; old recipes unchanged): a list needs the shape of
  one ("A, B y C", one join before the last item, no item opening like a clause), so a run of clauses is no
  longer drawn as a list; a bar is named by the thing measured ("Por semana", "La app", "Al mes") — a pronoun
  ("It", "Lo medí"), another figure ("Twenty a day") or a bare verb is not a name, and then the figure card is
  drawn instead of wrong bars; figures said in words of any size ("ciento sesenta y uno", "fifty-four"),
  thousands and millions ("17 mil apps" = 17000) and "un segundo" (a quantity with its unit); quotes with
  "dice / afirma / reportó / encontró que", "de acuerdo con", "says / reported / found that", "a study by X
  found that" — only from a named source. New scene actions take their subject and target from what is named
  ("borra las fotos" → the photos into the trash; "descargas … en el celular" → the cloud down to the phone).
  The icon question offers a shortlist (a question can no longer have more options than answer letters); a
  title that names nothing shows the topic's picture, the phone instead of the app (which read as a photo).
- `script.support_spread` (off by default): the notes of words said under a full-frame cutaway come in
  earlier, spread over the part said on camera, instead of being left out (+20 % notes on a real test plan).
  A word that says again what a sure note shows (its plural, the same illustration) is not asked about.
- `domain/flow.py`: a sentence describing a flow ("A manda B a C", "pasa de X a Y y luego a Z", "goes from X
  to Y") gives the ordered stops of a diagram (not wired to the director yet).
- **Sources analyzed side by side** (`analysis.jobs`, Spanish `trabajos`; `auto` = `render.jobs` / `$SVE_JOBS`,
  `1` = one after another): every source's 16 kHz wav is extracted first and Whisper transcribes them one after
  another on its own thread while the proxies and the picture / scene / sound analysis run. Results and cache
  files are the same as one at a time (a file listed twice is worked on once). 4 synthetic 30 s talking clips
  (`scripts/bench.py --scenario analyze`, M1 Pro, 3 runs each, alternated): 26.2 s → 18.4 s (−30 %).
- `transcription.engine` (Spanish `motor`): `faster-whisper` (default, unchanged), `mlx` (Whisper on the Apple
  GPU through mlx-whisper, `[apple]` extra) or `auto`. Same words contract; an engine that cannot run or fails
  falls back to faster-whisper; the analysis records which engine, model and device transcribed it
  (`transcription` in `<id>_analysis.json`) and `sve doctor` says which engine the settings pick. Transcripts
  stay cached per source: changing the engine never re-transcribes a finished project, and with `auto` a source
  analyzed again (`sve analyze --force`) keeps the engine its words came from. mlx stays opt-in: on a real 47 s
  Spanish recording (model `small`) its greedy decoding changed 9 % of the words and the script alignment found
  one sentence fewer, and it was 0.7–1.8× as fast as faster-whisper, not the 2× needed to adopt it.
- `scripts/bench.py --scenario analyze` (analysis and transcription time, engine / model / device recorded,
  `--engine`, `--analysis-jobs`) and `--scenario explainer75` (final render of a ~75 s explainer: a presenter-like
  recording, 6 full-frame graphics, subtitles). Both run only when named.
- `vision.naming` (default `clip`: names exactly as before): `frames` names each product from its frames
  with the highest product score, against the packaged vocabulary embedded with the image model's own text
  encoder; `decider` then lets the decider's local language model look at the best frame and pick among
  CLIP's `name_candidates` best names (new decider topic `product_name`; asking for it is enough, without
  the language model the CLIP names stand). On a labeled 8-minute unboxing (16 products): exact names
  3 → 8 (`frames`) → 10 (`decider`; 12 counting the right category), ≈4–5 s per product the first time,
  cached after. `products.json` keeps its format. Recommended for the unboxing and haul recipes when Ollama
  is installed.
- The decider's language model receives pictures larger than 960 pixels scaled down (a full-HD b-roll frame
  took ≈16 s a question).

### Fixed
- One or two frames of the presenter showed between two full-frame cutaways in a row: clips are now
  laid on the frame grid and hold their last frame to the end of their window.
- `sve check <project>` without `--version` checks the most recently rendered version, draft or variant included
  (it failed with "Version folder not found: …/v1" when only drafts existed), skips failed renders and says
  which version it checked.
- `credits.txt` (the video's description) is written in the project's language (Spanish by default) and the
  author is shortened (a commons' "Name derivative work of / …" keeps the name); `credits.json` keeps it whole.

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
