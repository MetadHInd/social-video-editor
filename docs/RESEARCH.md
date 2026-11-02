# The research behind the defaults

The defaults in `recipes/base.yaml` come from a review of what is known about short-form vertical
video. **Read the numbers as orientation, not as laws.** Platforms do not publish how their ranking
works; most TikTok figures come from agency and tool vendors who often cite each other. Each claim
below is tagged:

- *study* — peer-reviewed or academic research;
- *industry consensus* — repeated by most practitioner guides;
- *estimate* — a vendor or third-party figure, often second-hand.

## 1. What the ranking seems to reward

| Signal | What is known | Strength |
|---|---|---|
| Watch time and completion rate | The most important signals according to almost every 2026 guide. | industry consensus |
| Rewatches | One person replaying the video appears to count more than several single views. | estimate |
| Shares and saves | Weigh much more than likes. | industry consensus |
| Comments, follows from the video | Strong, below shares. | estimate |
| Test audience | A video is first shown to a small group and distributed further depending on their response; this can take 24 h or more. | industry consensus |
| "70 % completion to go viral" | Repeated across 2026 guides that cite each other; not official. | weak estimate |
| Length | Under ~15 s competes on completion rate; 30 s and longer on total watch time. | estimate |

**Practical goal:** people should watch to the end, replay, save and share. Effects, music and text
are means to that end. → `duration.target: 30`, loop-friendly endings in the recipes.

Sources: [eClincher](https://www.eclincher.com/articles/how-the-tiktok-algorithm-works-in-2026) ·
[Hootsuite](https://blog.hootsuite.com/tiktok-algorithm/) ·
[Darkroom](https://www.darkroomagency.com/observatory/tiktok-algorithm-guide-2026) ·
[SocialChamp](https://www.socialchamp.com/blog/?p=42771) ·
[Everything PR](https://everything-pr.com/tiktok-algorithm-ranking-signals) ·
[Virlo](https://virlo.ai/blog/how-the-tiktok-algorithm-works) ·
[Opus — length and retention](https://www.opus.pro/blog/tiktok-length-format-retention-data)

## 2. Attention: myth and measurement

- **Myth — "humans have an 8-second attention span, less than a goldfish."** The figure traces back
  to a 2015 Microsoft Canada marketing report that took it from "Statistic Brain"; nobody has found
  a study behind it, researchers point out that an average "attention span" is not a meaningful
  quantity (attention depends on the task), and there is no evidence goldfish have poor memory
  either. ([Univ. of Wisconsin — Goldfish MythBusting, PDF](https://internet.psych.wisc.edu/wp-content/uploads/532-Master/532-UnitPages/Unit-09/Goldfish_MythBusting.pdf) ·
  [Downes](https://www.downes.ca/post/74864/rd) ·
  [Medium](https://medium.com/write-rise/the-attention-span-stat-everyone-keeps-getting-wrong-91c418793ccc))
- **Measured — screen attention (Gloria Mark, UC Irvine).** The time people stay on one screen
  before switching fell from about 2.5 minutes (2004) to 75 s (2012) and **47 s** (2016–2020),
  median 40 s; about half of the switches are self-interruptions. It describes switching behavior,
  not a clinical capacity. *study* ([UC Newsroom](https://www.universityofcalifornia.edu/news/how-sharpen-your-attention-and-meet-your-goals-2024) ·
  [Silicon Canals](https://siliconcanals.com/t-screen-attention-47-seconds-gloria-mark/))
- **Short videos and memory.** In an LMU Munich experiment (Chiossi et al., 2023, 60 participants),
  watching TikTok impaired *prospective memory* (remembering to do something later) more than
  Twitter or YouTube; the authors attribute it to constant context switching. *study, preprint*
  ([arXiv](https://arxiv.org/abs/2302.03714v1))
- **"Variable reward / dopamine"** explanations are widespread but we found no peer-reviewed
  evidence for TikTok specifically; treated as a design idea only.

**Practical goal:** viewers are not incapable of attention — they switch context fast and decide
within seconds whether something deserves their time. Every stretch of the video has to earn it.

## 3. The first seconds (the hook)

- Over 70 % of viewers reportedly decide within ~3 s whether to keep watching; "good" 3-second
  retention is quoted at 70–85 %. *estimate*
- Starting the hook in the first second instead of the second raised 2–3 s retention from 63 % to
  81 % in one vendor analysis. *estimate, second-hand*
- In an analysis of 121,000 hooks, social-proof hooks performed best and authority hooks worst.
  *vendor study*

→ `hook.strategy: teaser` (open with the best moment), `hook.duration: 1.6`, `hook.text` typed in
the first second, cover image taken once the hook text is on screen (`export.cover_at`).

Sources: [Virlo — first seconds](https://virlo.ai/blog/why-the-first-few-seconds-matter) ·
[Hook engineering (dev.to)](https://dev.to/bulkdl/hook-engineering-the-science-of-the-first-3-seconds-in-short-form-video-2026-2jp9) ·
[Quso — 121K hooks](https://quso.ai/blog/hooks-for-tiktok) ·
[CapCut — hooks](https://www.capcut.com/resource/best-tiktok-hooks)

## 4. Psychology applied to retention

| Principle | What it says | How the engine uses it |
|---|---|---|
| **Curiosity gap** (Loewenstein, 1994) | Curiosity arises from a noticed gap in knowledge; it peaks when the gap is small but real, and fades if the answer takes too long. *study* ([summary](https://scrapbox.io/mtane0412/The_Psychology_of_Curiosity:_A_Review_and_Reinterpretation) · [Learning Loop](https://learningloop.io/plays/psychology/curiosity-effect)) | Teaser hook: show the result plus a question, answer it during the video. |
| **Open loops / numbered lists** | A list creates the expectation of reaching the end. The Zeigarnik effect is often cited here but is unproven for short video. *design idea* | `counter` text style ("3/12"). |
| **Pattern interrupts — with limits** | Frequent visual change re-engages, but a study of 2,511 TikTok product videos found an **inverted U**: more shots help up to a point, then hurt. *study* — Xiao, Li & Mou (2024), *Internet Research* ([paper](https://www.emerald.com/insight/content/doi/10.1108/intr-07-2023-0521/full/pdf)) | `pacing.shot_min: 0.8`, `pacing.shot_max: 3.2`; zoom punches on at most 30 % of shots (`effects.zoom_ratio`). |
| **Vertical format** | The same study: vertical video increases **comments and shares**. *study* | 1080×1920 output by default. |
| **Captions and sound-off viewing** | Vendors cite Meta reporting ~12 % more watch time for captioned ads, and 69 % of people watching video with the sound off in public (Verizon Media / Publicis). *vendor-cited figures* ([Vimeo](https://vimeo.com/blog/post/video-caption-benefits) · [Kapwing](https://www.kapwing.com/resources/subtitle-statistics/) · [AI-Media, PDF](https://ai-media.tv/wp-content/uploads/Whitepaper_Engagement_and_Retention-Final.pdf)) | Word-by-word auto subtitles when there is speech; large text cards when there is none. |
| **Music tempo** | Across 26,025 ads, fast music improves results only above a certain tempo and for "gain" messages (offers, purchases), not "avoid a problem" ones; fast music raises arousal and purchase intention. *studies* ([Journal of Advertising Research](https://thearf.org/access-knowledge-2/publications/journal-of-advertising-research/when-fast-music-makes-ads-more-persuasive-and-when-it-doesnt/) · [J. Retailing and Consumer Services, 2021](https://ideas.repec.org/a/eee/joreco/v63y2021ics0969698921002964.html)) | Cuts on the beat (`music.cuts_on_beat`), exact beat alignment with `"beats"`, `beat_pulse`. |
| **Loop ending** | If the end connects to the start, more people rewatch. *industry practice* | Recipes suggest an ending that loops back to the hook. |
| **Concrete call to action** | Comments are a distribution signal. *consensus* | `cta` text style ("Which one is your favorite? Comment the number"). |
| **Typewriter text** | Caption-style guides recommend the typewriter effect for emphasis, not for fast clips or speech subtitles. *vendors* ([VideoCaptions.ai](https://www.videocaptions.ai/caption-styles/typewriter) · [BlitzCut](https://blitzcutai.com/blog/video-caption-styles-ranked)) | Only on hook, cards and CTA; typing speeds up so it always finishes with time to read. |

**Ethical limit:** no invented prices, fake reactions or staged problems. Viewers spot them and
trust is lost.

### A viral video, summarized

1. Hook at second 0: a striking image plus text that opens a question.
2. A clear promise (a list, a transformation, a verdict).
3. Rhythm: something new every 2–3 s, without overdoing effects.
4. The payoff that was promised.
5. An ending that invites a replay (loop) or a comment.
6. Designed to work **without sound** (texts/subtitles) and **with sound** (music, ASMR).
7. Search: on-screen text (OCR), the description, hashtags and speech are indexed — put the keyword
   on screen and in the description early, without stuffing it.
   ([SocialPilot — TikTok SEO](https://www.socialpilot.co/blog/tiktok-seo) ·
   [CapCut — caption keywords](https://www.capcut.com/create/tiktok-caption-keywords-discoverability))
8. Consistency: saves and shares across several videos build an account, not one video.

Editing-rhythm sources: [ClipFlip — 44,000 clips](https://clipflip.io/resources/how-to-make-viral-clips) ·
[Socialync — retention techniques](https://www.socialync.io/blog/retention-techniques-keep-viewers-watching-2026)

## 5. Technical specifications

| Topic | Recommended | Engine default |
|---|---|---|
| Resolution | 1080×1920 (9:16) — consensus | `output: 1080×1920`, 30 fps |
| Codec | H.264 + AAC in MP4 (H.265 is sometimes processed worse) | libx264 High, yuv420p, BT.709, AAC 48 kHz, `+faststart` |
| Upload bitrate | 10–20 Mbps (sources range 5–25) | CRF 17 capped at 20 Mbps (`export.maxrate`) |
| Loudness | −14 LUFS integrated, −1 dBTP — the most cited value (some say −10 to −12) | `audio.loudness: -14`, `audio.true_peak: -1.0` |
| Safe zones | Avoid roughly the bottom ~480 px (caption, username), ~150 px on the right (buttons) and the top ~130–260 px; keep key text between y ≈ 220 and y ≈ 1440. Platforms publish no fixed figure; it changes with caption length. | Subtitles at `y: 0.64`; text styles at y 0.085–0.80; collage zone 0.215–0.72 |
| Upload | Enable "Upload in HD" | — |

Sources: [Wild & Free Tools — safe zones](https://wildandfreetools.com/blog/vertical-video-safe-zones-guide-2026/) ·
[Affroom — TikTok safe zone](https://affroom.com/blog/tiktok-safe-zone/) ·
[Moda — safe zone checker](https://moda.app/tools/story-safe-zone-checker) ·
[ConvertIntoMP4 — export settings](https://convertintomp4.com/da/blog/tiktok-youtube-shorts-export-settings) ·
[The PostFlow — export settings](https://thepostflow.com/post-production/post-production-workflows/export-settings-youtube-instagram-tiktok/)

## 6. Music by format

| Format | Music | Indicative tempo |
|---|---|---|
| Unboxing / haul | Upbeat "cute" pop, or the original ASMR sound + effects | Fast (≥ 120 BPM) |
| Tutorial | Lo-fi or soft instrumental, no lyrics | Moderate (80–110 BPM) |
| Vlog | The current trend | The song's |
| Before / after | Something with a clear drop for the reveal | Fast |
| Storytime | Very low or none | — |

**Copyright:** commercial songs mixed into a video outside the app may get it muted or limited.
Personal accounts can add trending songs from inside the app; business accounts are limited to the
platform's commercial library. Royalty-free options include Pixabay Music, the YouTube Audio
Library (check each track's attribution terms) and the
[TikTok Commercial Music Library](https://ads.tiktok.com/business/en-US/blog/audio-library-royalty-free-music)
([Soundstripe](https://www.soundstripe.com/blogs/tiktok-music-library-explained) ·
[Stack Influence](https://stackinfluence.com/blog/find-royalty-free-commercial-music-for-tiktok)).
This is why `sve render --no-music` exists: the same beat-aligned edit without the song, so a sound
can be added in the app.

## 7. Checklist

**Before uploading**
- The first 2 s have a strong image and a hook text.
- It makes sense without sound.
- No important text in the bottom strip or against the right edge.
- It ends in a loop or with a concrete question.
- A cover is chosen (the engine exports one).

**When publishing**
- Description with natural keywords; 3–5 relevant hashtags; "Upload in HD" on.
- Upload the `.srt` if the app allows it (or enable automatic captions when there is speech).
- Posting time: published advice disagrees
  ([Sprout Social](https://sproutsocial.com/insights/best-time-to-post-on-tiktok/) suggests
  Tuesday–Thursday afternoons; [Buffer](https://buffer.com/resources/best-time-to-post-on-tiktok)
  found weekends better) — your own analytics (when your followers are active) are the most reliable.

**First hours**
- Answer the first comments (requests for a "part 2" are the next video).
- Pin a comment with a question.
- After 24–48 h check 3-second retention, completion, saves and shares.
