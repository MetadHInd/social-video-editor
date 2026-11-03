"""Use case: check a rendered version without anyone listening or looking (`sve check`).

Reads vN/<video>.mp4 + vN/stems/*.wav + summary / plan_used / config_used (legacy Spanish files too)
and writes check.json:
  {"version": "v3", "ok": true, "errors": [...], "warnings": [...], "metrics": {...},
   "listen": ["listen/01_loud_jump.wav", ...]}
plus ≤ 3 s snippets of the worst moments in listen/ (escuchar/ with `language: es`). Everything is
written to the version folder, or to --out DIR (the version folder is then never touched).
Renders made before stems / text boxes were recorded are checked too: the checks that need them
are skipped (or estimated) and say so in metrics.notes.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..domain import check as ck
from ..domain.config import labels
from ..domain.models import UserError
from ..domain.music import beats_in_video
from ..domain.timeline import absolute_texts, compute_timeline
from .music import effective_analysis, music_analysis
from .services import Services

SR = 48000
BLOCK_AUDIO_SEC = 600       # longer renders: the audio is analysed block by block (ck.AudioScan)
BLOCK_SEC = 30.0
_MMSS = re.compile(r"(\d+):(\d+(?:\.\d+)?)")


def _seconds(mmss: str) -> float:
    m = _MMSS.match(mmss.strip())
    return int(m.group(1)) * 60 + float(m.group(2)) if m else 0.0


def shot_spans(summary: dict) -> list[dict]:
    """[{"n", "type", "start", "end", "cut"}] from the summary's shot details."""
    out = []
    for d in summary.get("shot_details", []):
        a, _, b = str(d.get("in_video", "")).partition("–")
        out.append({"n": d.get("n"), "type": d.get("type", "video"), "start": _seconds(a), "end": _seconds(b),
                    "cut": d.get("cut_sec"), "transition": d.get("transition")})
    return out


def _project_check(svc: Services, folder: Path) -> dict:
    """Current check thresholds of the project (tuned after the render); {} if it cannot be loaded."""
    try:
        return svc.projects.load(folder).cfg.get("check") or {}
    except (UserError, OSError, ValueError):
        return {}


class Checker:
    def __init__(self, svc: Services, version: dict, th: dict):
        self.svc, self.v, self.th = svc, version, th
        self.cfg, self.summary, self.plan = version["config"], version["summary"], version["plan"]
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.metrics: dict = {}
        self.moments: list[dict] = []
        res = str(self.summary.get("resolution", ""))
        if "x" in res:
            self.W, self.H = (int(x) for x in res.split("x"))
        else:
            self.W, self.H = int(self.cfg["output"]["width"]), int(self.cfg["output"]["height"])
        self.spans = shot_spans(self.summary)
        self.cuts = [s["cut"] for s in self.spans[:-1] if s["cut"] is not None]

    def moment(self, kind: str, t: float, severity: float, why: str) -> None:
        self.moments.append({"kind": kind, "t": round(float(t), 2), "severity": round(float(severity), 2), "why": why})

    # ── audio of the final video ─────────────────────────────────────────────
    def audio(self, video: Path) -> tuple[dict, np.ndarray]:
        th, a = self.th, self.cfg["audio"]
        L = self.svc.inspector.loudness(video)
        target, tp_max = float(a["loudness"]), float(a["true_peak"])
        I, tp = ck.finite(L["integrated"]), ck.finite(L["true_peak"])
        self.metrics["audio"] = {"integrated_lufs": I, "target_lufs": target, "true_peak_dbtp": tp,
                                 "true_peak_max": tp_max, "lra": ck.finite(L["lra"]),
                                 # what the mastering measured: the same ebur128 measurement (renders made
                                 # before it used loudnorm, which differs on very short videos)
                                 "engine_lufs": ck.finite(self.summary.get("audio", {}).get("after_lufs"))}
        if I is None or abs(I - target) > th["loudness_tolerance_lu"]:
            self.errors.append(f"integrated loudness {I} LUFS, target {target} ± {th['loudness_tolerance_lu']} LU")
        if tp is not None and tp > tp_max + 0.05:
            self.errors.append(f"true peak {tp} dBTP above {tp_max} dBTP")
        length = max([float(self.summary.get("duration_sec") or 0)] + [float(x) for x in L.get("t") or [0]][-1:])
        if length > BLOCK_AUDIO_SEC and hasattr(self.svc.inspector, "audio_blocks"):
            scan = ck.AudioScan(SR, self._splice_ranges())
            for block in self.svc.inspector.audio_blocks(video, SR, BLOCK_SEC):
                scan.feed(block)
            clips, x = scan.clipping(), None
            self.splices(scan.excerpt, scan.seconds, L)
            sil = scan.silences(th["silence_db"], th["silence_sec"])
            harsh = scan.harshness
        else:
            stereo = self.svc.inspector.audio(video, SR)
            clips = ck.clipping(np.abs(stereo).max(axis=1), SR)      # per channel, never a downmix
            self.splices(lambda a, b: stereo[a:b], len(stereo) / SR, L)
            x = stereo.mean(axis=1)
            sil = ck.silences(x, SR, th["silence_db"], th["silence_sec"])
            harsh = lambda: ck.harshness(x, SR)                     # noqa: E731
        self.metrics["audio"]["clipped_runs"] = len(clips)
        if clips:
            self.errors.append(f"clipping: {len(clips)} runs of full-scale samples (first at {clips[0][0]} s)")
            for t, n in sorted(clips, key=lambda c: -c[1])[:2]:
                self.moment("clipping", t, 10 + n, f"{n} clipped samples")
        self.metrics["audio"]["silences"] = sil
        for a_, b_ in sil:
            self.warnings.append(f"silence {a_:.2f}–{b_:.2f} s ({b_ - a_:.1f} s below {th['silence_db']} dBFS)")
            self.moment("silence", a_, 1 + (b_ - a_) / th["silence_sec"], f"{b_ - a_:.1f} s of silence")
        jumps = ck.cut_jumps(L["t"], L["momentary"], self.cuts)
        big = [j for j in jumps if abs(j["jump_lu"]) > th["cut_jump_lu"]]
        self.metrics["audio"]["cut_jump_max_lu"] = max((abs(j["jump_lu"]) for j in jumps), default=None)
        self.metrics["audio"]["cut_jumps"] = big
        for j in big:
            self.warnings.append(f"loudness jump {j['jump_lu']:+.1f} LU at the cut {j['t']:.2f} s "
                                 f"({j['before']} → {j['after']} LUFS)")
            self.moment("loud_jump", j["t"], abs(j["jump_lu"]) / th["cut_jump_lu"], f"{j['jump_lu']:+.1f} LU at a cut")
        h = harsh()
        self.metrics["audio"].update(harsh_ratio=h["ratio"], harsh_worst_t=h["worst_t"],
                                     harsh_worst_ratio=h["worst_ratio"])
        if h["ratio"] > th["harsh_ratio"]:
            self.warnings.append(f"harsh mix: {h['ratio']:.0%} of the energy in 2–5 kHz (max {th['harsh_ratio']:.0%})")
        if h["worst_ratio"] is not None and h["worst_ratio"] > th["harsh_window_ratio"]:
            self.warnings.append(f"harsh moment at {h['worst_t']:.1f} s: {h['worst_ratio']:.0%} in 2–5 kHz")
            self.moment("harsh", h["worst_t"], h["worst_ratio"] / th["harsh_window_ratio"], "2–5 kHz peak")
        return L, x

    # ── AutoMix splices (playlist renders) ───────────────────────────────────
    def _splice_ranges(self) -> list[tuple[int, int]]:
        """Sample ranges splices() reads (kept in full by the block-wise scan)."""
        mix = (self.cfg.get("music") or {}).get("_automix") or {}
        return [(int(max(float(sp["start"]) - 0.5, 0) * SR), int((max(float(sp["end"]), 0.0) + 0.5) * SR) + 1)
                for sp in mix.get("splices") or []]

    def splices(self, excerpt, total: float, L: dict) -> None:
        """Around every song change of an AutoMix: no silence > splice_silence_sec, no momentary
        loudness dip > splice_dip_db below the neighbouring music, no true peak over audio.true_peak.
        excerpt(first, last) → stereo samples; total: seconds of audio."""
        mix = (self.cfg.get("music") or {}).get("_automix") or {}
        if not mix.get("splices"):
            return
        th = self.th
        sil_sec = float(th.get("splice_silence_sec", 0.3))
        dip_max = float(th.get("splice_dip_db", 2.0))
        tp_max = float(self.cfg["audio"]["true_peak"])
        t, mom = np.asarray(L["t"], float), np.asarray(L["momentary"], float)
        out = []
        for sp in mix["splices"]:
            a, b = max(float(sp["start"]), 0.0), min(float(sp["end"]), total)
            if b <= a:
                continue
            i0, i1 = int(max(a - 0.5, 0) * SR), int(min(b + 0.5, total) * SR)
            seg = excerpt(i0, i1)
            gaps = [(round(x0 + i0 / SR, 2), round(x1 + i0 / SR, 2))
                    for x0, x1 in ck.silences(seg.mean(axis=1), SR, th["silence_db"], sil_sec)]
            tp = _true_peak_db(seg)
            dip = _splice_dip(t, mom, a, b)
            res = {"t": sp["t"], "start": round(a, 2), "end": round(b, 2), "silences": gaps,
                   "true_peak_dbtp": tp, "dip_db": dip}
            out.append(res)
            for g0, g1 in gaps:
                self.warnings.append(f"AutoMix splice at {sp['t']:.1f} s: silence {g0:.2f}–{g1:.2f} s")
                self.moment("splice_gap", g0, 3, f"{g1 - g0:.1f} s gap at a song change")
            if dip is not None and dip > dip_max:
                self.warnings.append(f"AutoMix splice at {sp['t']:.1f} s: loudness dips {dip:.1f} dB "
                                     f"below the neighbouring music (max {dip_max:g})")
                self.moment("splice_dip", (a + b) / 2, 1 + dip / dip_max, f"{dip:.1f} dB dip at a song change")
            if tp is not None and tp > tp_max + 0.05:
                self.errors.append(f"AutoMix splice at {sp['t']:.1f} s: true peak {tp:+.1f} dBTP above {tp_max:+g}")
                self.moment("splice_peak", sp["t"], 5, f"true peak {tp:+.1f} dBTP at a song change")
        self.metrics["automix"] = {"splices": out}

    # ── stems: balance and typing level ──────────────────────────────────────
    def stems(self, mix: dict) -> None:
        stems = self.v["stems"]
        if not stems:
            why = ("not written: long render or no original sound, export.stems: true writes them"
                   if "stems" in self.summary else "rendered before stems existed")
            self.notes.append(f"no stems ({why}): stem balance and typing level skipped")
            return
        th, audio = self.th, self.summary.get("audio", {})
        gain = ck.finite(audio.get("master_gain_db"))
        if gain is None:
            before, after = ck.finite(audio.get("before_lufs")), ck.finite(audio.get("after_lufs"))
            gain = after - before if before is not None and after is not None else 0.0
        I_mix = ck.finite(mix["integrated"], ck.SILENT_LUFS)
        L = {k: self.svc.inspector.loudness(p) for k, p in stems.items()}
        eff = {k: round(ck.finite(v["integrated"], ck.SILENT_LUFS) + gain, 1) for k, v in L.items()}
        self.metrics["stems"] = {**eff, "master_gain_db": round(gain, 2), "mix": I_mix}

        def worst(a: dict, b: dict, offset: float) -> tuple[float, float]:
            """Moment where stem a (+gain) is furthest above b (both audible) → (diff, t)."""
            best = (-99.0, 0.0)
            for t, ma, mb in zip(a["t"], a["momentary"], b["momentary"]):
                if ma > -60 and mb > -60 and ma + offset - mb > best[0]:
                    best = (ma + offset - mb, t - 0.2)
            return best

        if "sfx" in eff and eff["sfx"] > I_mix + th["sfx_max_vs_mix_lu"]:
            self.warnings.append(f"sound effects too loud: {eff['sfx']} LUFS vs mix {I_mix} "
                                 f"(max mix {th['sfx_max_vs_mix_lu']:+g} LU)")
            d, t = worst(L["sfx"], mix, gain)
            self.moment("sfx_loud", t, 1 + d / 6, f"sfx {d:+.1f} LU over the mix")
        if self.cfg["audio"].get("asmr") and "music" in eff and "original" in eff:
            if eff["music"] > eff["original"] + th["music_max_vs_original_lu"]:
                self.warnings.append(f"ASMR: music ({eff['music']} LUFS) masks the original sound "
                                     f"({eff['original']} LUFS)")
                d, t = worst(L["music"], L["original"], 0.0)
                self.moment("music_masks", t, 1 + d / 6, f"music {d:+.1f} LU over the original")
        bursts = [b for b in self.summary.get("typing") or [] if b[2] >= 3]
        if "sfx" in L and bursts:
            levels = [(ck.window_level(L["sfx"]["t"], L["sfx"]["momentary"], a, b) + gain
                       - ck.window_level(mix["t"], mix["momentary"], a, b), (a + b) / 2) for a, b, _ in bursts]
            d, t = max(levels)
            self.metrics["typing_max_vs_mix_lu"] = round(d, 1)
            if d > th["typing_max_vs_mix_lu"]:
                self.warnings.append(f"typing clicks too loud: {d:+.1f} LU vs the mix at {t:.1f} s "
                                     f"(max {th['typing_max_vs_mix_lu']:+g})")
                self.moment("typing_loud", t, 1 + (d - th["typing_max_vs_mix_lu"]) / 6, f"typing {d:+.1f} LU")

    def typing_density(self) -> None:
        bursts = self.summary.get("typing")
        if bursts is None:
            self.notes.append("typing bursts not recorded (old render): typing density skipped")
            return
        rates = [(ck.burst_rate(b), b) for b in bursts]
        rate, burst = max(rates, default=(0.0, None), key=lambda r: r[0])
        self.metrics["typing"] = {"bursts": len(bursts), "clicks": sum(b[2] for b in bursts),
                                  "max_per_sec": round(rate, 1)}
        if burst and rate > self.th["typing_max_per_sec"]:
            self.warnings.append(f"typing too dense: {rate:.0f} clicks/s at {burst[0]:.1f} s "
                                 f"(max {self.th['typing_max_per_sec']:g})")
            self.moment("typing_dense", (burst[0] + burst[1]) / 2, rate / self.th["typing_max_per_sec"],
                        f"{rate:.0f} clicks/s")

    # ── picture ──────────────────────────────────────────────────────────────
    def video(self, video: Path) -> None:
        th = self.th
        ev = self.svc.inspector.video_events(video, th["black_min_sec"], th["freeze_min_sec"])
        fades = [(s["start"] - 0.3, s["start"] + 0.5) for s in self.spans if s["transition"] == "fade_black"]
        black = [b for b in ev["black"] if not ck.overlaps((b[0], b[1]), fades)]
        black = ck.outside_output_fades(black, self.cfg.get("output") or {}, self._total(), th["black_min_sec"])
        drawn = [(s["start"], s["end"]) for s in self.spans if s["type"] in ("polaroid", "collage", "photo")]
        shots = self.plan.get("shots", [])
        frozen = [(s["start"], s["end"]) for s in self.spans
                  if s["n"] and s["n"] - 1 < len(shots)
                  and any(e.get("type") == "freeze" for e in shots[s["n"] - 1].get("effects", []))]
        freeze = [f for f in ev["freeze"] if f[1] is not None and not ck.overlaps((f[0], f[1]), drawn + frozen, -0.05)]
        self.metrics["video"] = {"black": black, "freeze": freeze, "ignored_freezes": len(ev["freeze"]) - len(freeze)}
        for a, b in black:
            self.errors.append(f"black frames {a:.2f}–{b:.2f} s")
        for a, b in freeze:
            self.warnings.append(f"frozen picture {a:.2f}–{b:.2f} s ({b - a:.1f} s)")

    def _total(self) -> float | None:
        return float(self.summary.get("duration_sec") or 0) or None

    def texts(self) -> None:
        boxes = self.summary.get("text_boxes")
        if boxes is None:
            try:
                tl = compute_timeline(self.plan, self.cfg)
                texts = absolute_texts(self.plan, tl)
            except Exception as e:          # a very old plan the current engine cannot place
                self.notes.append(f"text boxes not recorded and not estimable ({e}): safe-zone check skipped")
                return
            boxes = ck.estimated_boxes(texts, self.cfg["texts"]["styles"], self.W, self.H)
            self.notes.append("text boxes not recorded (old render): estimated from the styles (no right-edge check)")
        issues = ck.box_issues(boxes, self.W, self.H, self.th)
        self.metrics["text_boxes"] = {"count": len(boxes), "outside_safe_zone": len(issues)}
        for i in issues:
            self.warnings.append(f"text \"{i['text'][:30]}\" ({i['start']:.1f}–{i['end']:.1f} s) outside the safe "
                                 f"zone: {', '.join(i['issues'])}" + (" (estimated)" if i["estimated"] else ""))

    # ── music, duration ──────────────────────────────────────────────────────
    def beats(self, folder: Path, total: float) -> None:
        mus = self.summary.get("music")
        if not mus or not self.cuts:
            self.notes.append("no music: cut-to-beat check skipped")
            return
        if (self.cfg.get("music") or {}).get("_automix"):           # playlist: the mix's own beat grid
            tl = self.svc.projects.read_json(Path(self.v["dir"]) / "automix.json")
            if not isinstance(tl, dict) or not tl.get("beats"):
                self.notes.append("AutoMix timeline (automix.json) missing: cut-to-beat check skipped")
                return
            tempos = [s.get("tempo") for s in tl.get("songs", []) if s.get("tempo")]
            self._beat_deviation([b for b in tl["beats"] if 0 <= b <= total + 1],
                                 round(float(np.median(tempos)), 2) if tempos else None)
            return
        try:
            path = self.svc.projects.find_music(self.cfg["music"], folder)
        except UserError:
            path = None
        if not path:
            self.notes.append(f"music file {mus.get('file')} not found: cut-to-beat check skipped")
            return
        am = effective_analysis(music_analysis(self.svc, path, folder / "cache", save=False), self.cfg["music"])
        beats = beats_in_video(am, float(mus.get("start_sec") or 0), total)
        self._beat_deviation(beats, am.get("tempo"))

    def _beat_deviation(self, beats: list[float], tempo) -> None:
        dev = ck.beat_deviation(self.cuts, beats, bool(self.cfg["music"].get("half_beats", True)))
        if not dev:
            return
        med = float(np.median(dev))
        self.metrics["beats"] = {"tempo": tempo, "cuts": len(dev), "median_ms": round(med * 1000),
                                 "max_ms": round(max(dev) * 1000)}
        tol = self.th["beat_tolerance_sec"]
        if med > tol:
            self.warnings.append(f"cuts off the beat: median {med * 1000:.0f} ms (max {tol * 1000:.0f} ms)")
        for c, d in zip(self.cuts, dev):
            if d > 2 * tol:
                self.moment("off_beat", c, d / tol / 2, f"cut {d * 1000:.0f} ms off the beat")

    def duration(self, total: float) -> None:
        """duration.max / duration.target null or 0 → not checked. A slideshow fitted to its music
        (photo.fit_to_music) lasts as long as the music: only reported."""
        d = self.cfg.get("duration") or {}
        target, mx = _limit(d.get("target", 30)), _limit(d.get("max", 60))
        self.metrics["duration"] = {"sec": round(total, 2), "target": target, "max": mx}
        fitted = bool((self.cfg.get("photo") or {}).get("fit_to_music")) and \
            any(s.get("type") == "photo" for s in self.plan.get("shots", []))
        if fitted:
            self.notes.append("photos fitted to the music: the duration follows the music (not checked)")
            return
        if mx is not None and total > mx:
            self.errors.append(f"duration {total:.1f} s over the maximum {mx:g} s")
        elif target is not None and abs(total - target) > self.th["duration_tolerance"] * target:
            self.warnings.append(f"duration {total:.1f} s vs target {target:g} s "
                                 f"(± {self.th['duration_tolerance']:.0%})")


def _limit(v) -> float | None:
    """A duration limit from the recipe: null / 0 / false → None (no limit)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _true_peak_db(x: np.ndarray) -> float | None:
    """4x-oversampled peak (dBTP) of a short stereo excerpt."""
    if not len(x):
        return None
    from scipy.signal import resample_poly
    peak = float(np.abs(resample_poly(x.astype(np.float64), 4, 1, axis=0)).max())
    return round(20 * np.log10(peak), 2) if peak > 0 else None


def _splice_dip(t: np.ndarray, mom: np.ndarray, a: float, b: float, side: float = 5.0) -> float | None:
    """How far the quietest second inside [a, b] falls below the quieter of the music around it
    (energy means of the momentary loudness, [a − 1 − side, a − 1] and [b + 1, b + 1 + side])."""
    def level(lo: float, hi: float) -> float | None:
        m = (t >= lo) & (t < hi) & np.isfinite(mom) & (mom > -70)
        return float(10 * np.log10(np.mean(10 ** (mom[m] / 10)))) if m.sum() >= 3 else None

    sides = [v for v in (level(a - 1 - side, a - 1), level(b + 1, b + 1 + side)) if v is not None]
    inside = [v for v in (level(x, x + 1.0) for x in np.arange(a, max(b - 1.0, a) + 1e-9, 0.5)) if v is not None]
    if not sides or not inside:
        return None
    return round(min(sides) - min(inside), 2)


def check_version(svc: Services, project: str, version: str | None = None,
                  out: str | Path | None = None) -> tuple[dict, list[str]]:
    """Runs every check → (check.json content, notes). Writes check.json + snippets to `out` or the version."""
    if svc.inspector is None:
        raise UserError("No render inspector configured")
    folder = svc.projects.resolve(project)
    v = svc.projects.load_version(folder, version)
    if v["video"] is None:
        raise UserError(f"No video in {v['dir']}")
    th = ck.thresholds(v["config"].get("check"), _project_check(svc, folder))
    c = Checker(svc, v, th)
    total = float(v["summary"].get("duration_sec") or 0) or None
    mix, _ = c.audio(v["video"])
    total = total or (mix["t"][-1] if mix["t"] else 0.0)
    c.stems(mix)
    c.typing_density()
    c.video(v["video"])
    c.texts()
    c.beats(folder, total)
    c.duration(total)

    out_dir = Path(out).expanduser() if out else v["dir"]
    sub = labels(v["config"].get("language", "es"))["listen"]
    listen_dir = out_dir / sub
    svc.projects.clear_files(listen_dir, "*.wav")     # stale snippets of an earlier check
    listen = []
    picks = ck.pick_moments(c.moments, int(th["listen_max"]))
    for i, m in enumerate(picks, 1):
        start, dur = ck.snippet_window(m["t"], float(th["listen_sec"]), total)
        name = f"{i:02d}_{m['kind']}.wav"
        svc.inspector.snippet(v["video"], start, dur, listen_dir / name)
        listen.append(f"{sub}/{name}")
        m["file"], m["start"] = f"{sub}/{name}", start
    c.metrics["moments"] = picks
    c.metrics["notes"] = c.notes
    c.metrics["thresholds"] = th
    result = {"version": v["name"], "ok": not c.errors, "errors": c.errors, "warnings": c.warnings,
              "metrics": c.metrics, "listen": listen}
    svc.projects.write_json(out_dir / "check.json", result)
    return result, c.notes
