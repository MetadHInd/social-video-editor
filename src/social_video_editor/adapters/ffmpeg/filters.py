"""FFmpeg filter strings built from domain data (shots, configuration, color parameters).

Shots passed here are timeline dicts: the plan shot plus "index", "dur", "overlap", "out_start".
"""
from __future__ import annotations

from ...domain import color_math
from ...domain.effects import effects_of
from ...domain.transitions import get_transition
from ...log import log
from .escape import filter_path
from .luts import hlg_lut_path

# transition name → xfade transition (None = hard cut). https://trac.ffmpeg.org/wiki/Xfade
XFADE = {
    "cut": None, "fade": "fade", "fade_black": "fadeblack", "flash": "fadewhite", "zoom": "zoomin",
    "whip": "smoothleft", "slide_up": "slideup", "slide_left": "slideleft", "circle": "circleopen",
    "pixel": "pixelize", "blur": "hblur", "radial": "radial", "curtain": "vertopen",
    "soft_slide": "smoothleft", "soft_zoom": "zoomin",
}


def xfade_name(transition: str) -> str | None:
    get_transition(transition)                      # validates the name
    return XFADE[transition]


# ── color ────────────────────────────────────────────────────────────────────

def sdr_chain(info: dict, white: float = 0.62) -> str:
    """Brings any source to SDR BT.709 yuv420p (used after scaling).

    white: brightness of HDR white once in SDR (lower = more contrast and color; 0.62 tested on iPhone).
    """
    if info.get("hlg"):
        lut = hlg_lut_path(white=white)
        return (
            "format=yuv420p10le,"
            "scale=in_color_matrix=bt2020:in_range=tv,format=gbrp16le,"
            f"lut3d=file={filter_path(lut)}:interp=tetrahedral,"
            "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
        )
    if info.get("hdr"):
        log(f"Warning: {info['path']} is HDR PQ; treated as approximate HLG.")
        return sdr_chain({**info, "hlg": True}, white)
    return "format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def creative_chain(p: dict) -> str:
    f = []
    if "vibrance" in p:
        f.append(f"vibrance=intensity={p['vibrance']:.2f}")
    if "temperature" in p:
        f.append(f"colortemperature=temperature={p['temperature']}:mix=0.35")
    if "lut" in p:
        f.append(f"lut3d=file={filter_path(p['lut'])}")          # ~ expanded, any folder name
    if "sharpen" in p:
        f.append(f"cas=strength={p['sharpen']:.2f}")
    if p.get("vignette"):
        f.append("vignette=angle=PI/5")
    return ",".join(f)


def correction_chain(stats: dict, color_cfg: dict) -> str:
    """Automatic correction filters from the shot statistics (+ the creative look)."""
    creative = creative_chain(color_math.creative(color_cfg))
    p = color_math.correction(stats, color_cfg)
    if p is None:
        return creative
    filters = []
    if p["levels"]:
        lo, hi = p["levels"]
        filters.append(f"colorlevels=rimin={lo:.3f}:gimin={lo:.3f}:bimin={lo:.3f}:"
                       f"rimax={hi:.3f}:gimax={hi:.3f}:bimax={hi:.3f}")
    if p["gains"]:
        r, g, b = p["gains"]
        filters.append(f"colorchannelmixer=rr={r:.3f}:gg={g:.3f}:bb={b:.3f}")
    filters.append(f"eq=gamma={p['gamma']:.3f}:saturation={p['saturation']:.3f}:contrast={p['contrast']:.3f}")
    return ",".join(filters + ([creative] if creative else []))


# ── framing and camera moves ─────────────────────────────────────────────────

def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def work_size(W: int, H: int, cfg: dict) -> tuple[int, int]:
    """Sources are first scaled larger than the output (zoom_margin) so zooms crop real pixels."""
    m = float(cfg["framing"].get("zoom_margin", 1.25))
    return _even(W * m), _even(H * m)


def framing_mode(info: dict, W: int, H: int, cfg: dict) -> str:
    m = cfg["framing"].get("mode", "auto")
    if m != "auto":
        return m
    r_src, r_out = info["width"] / info["height"], W / H
    return "blur" if max(r_src, r_out) / min(r_src, r_out) > 1.3 else "crop"


def prescale(info: dict, W: int, H: int, cfg: dict, shot: dict) -> str:
    """First filter (on the original source): brings it to the working size."""
    Wm, Hm = work_size(W, H, cfg)
    if framing_mode(info, W, H, cfg) == "blur":       # fill the borders with the same image blurred
        fit = min(Wm / info["width"], Hm / info["height"])
        fw, fh = _even(info["width"] * fit), _even(info["height"] * fit)
        cov = max(Wm / info["width"], Hm / info["height"])
        cw, ch = _even(info["width"] * cov), _even(info["height"] * cov)
        return (f"split=2[bg][fg];"
                f"[bg]scale={_even(cw / 4)}:{_even(ch / 4)},boxblur=20:2,scale={cw}:{ch},crop={Wm}:{Hm},"
                f"eq=brightness=-0.08[bg2];"
                f"[fg]scale={fw}:{fh}:flags=lanczos[fg2];[bg2][fg2]overlay=(W-w)/2:(H-h)/2")
    cov = max(Wm / info["width"], Hm / info["height"])
    cw, ch = _even(info["width"] * cov), _even(info["height"] * cov)
    fx = float(shot.get("focus_x", cfg["framing"].get("focus_x", 0.5)))
    fy = float(shot.get("focus_y", cfg["framing"].get("focus_y", 0.5)))
    crop = f",crop={Wm}:{Hm}:{int((cw - Wm) * fx)}:{int((ch - Hm) * fy)}" if (cw, ch) != (Wm, Hm) else ""
    return f"scale={cw}:{ch}:flags=lanczos{crop}"


def zoom_expr(shot: dict, cfg: dict, dur: float, index: int) -> str:
    """Zoom factor as a function of t (seconds inside the already sped-up shot)."""
    margin = float(cfg["framing"].get("zoom_margin", 1.25))
    base = float(shot.get("zoom", 1.0))
    parts = [f"{base:.4f}"]
    explicit = effects_of(shot, "push_in") + effects_of(shot, "pull_out")
    kb = float(cfg["framing"].get("slow_push", 0.0))
    if explicit:
        for e in explicit:
            c = float(e.get("amount", 0.06))
            parts.append(f"{c}*t/{dur:.4f}" if e["type"] == "push_in" else f"{c}*(1-t/{dur:.4f})")
    elif kb > 0 and dur > 0:  # automatic slow push, alternating direction
        parts.append(f"{kb}*t/{dur:.4f}" if index % 2 == 0 else f"{kb}*(1-t/{dur:.4f})")
    for e in effects_of(shot, "zoom_punch"):
        sc = float(e.get("scale", cfg["effects"].get("zoom_scale", 1.12))) - 1
        t0 = float(e.get("t", 0.0))
        if t0 <= 0.01:   # at the start: begins zoomed in and settles (entry punch)
            parts.append(f"{sc:.3f}*max(0,1-t/0.35)*max(0,1-t/0.35)")
        else:
            parts.append(f"{sc:.3f}*min(max((t-{t0:.3f})/0.08,0),1)")
    for e in effects_of(shot, "beat_pulse"):    # music beats (relative to the shot)
        c = float(e.get("amount", cfg["effects"].get("pulse_amount", 0.04)))
        for tb in shot.get("_beats", [])[:12]:
            parts.append(f"{c:.3f}*gte(t,{tb:.3f})*exp(-(t-{tb:.3f})*9)")
    expr = "+".join(parts)
    if expr in ("1.0000",):
        return "1"
    return f"min({expr},{margin:.3f})"


def crop_expr(shot: dict, fx: float, fy: float, span_w: str = "iw-ow", span_h: str = "ih-oh") -> tuple[str, str]:
    """Crop position: the focus inside the free span (zoomed size − output size), plus shake, clamped.

    span_w / span_h: the free span as an expression. The zoom passes it computed from the zoom factor:
    the crop filter's iw / ih stay those of the FIRST frame when the frame size changes per frame."""
    x, y = f"({span_w})*{fx:.3f}", f"({span_h})*{fy:.3f}"
    for e in effects_of(shot, "shake"):
        t0, d, a = float(e.get("t", 0)), float(e.get("dur", 0.3)), float(e.get("amplitude", 14))
        env = f"between(t,{t0:.3f},{t0 + d:.3f})*(1-(t-{t0:.3f})/{d:.3f})"
        x += f"+{a}*sin(t*73)*{env}"
        y += f"+{a * 0.7}*cos(t*61)*{env}"
    return f"'max(0,min({span_w},{x}))'", f"'max(0,min({span_h},{y}))'"


def motion(W: int, H: int, cfg: dict, shot: dict, dur: float, index: int) -> str:
    """Time-varying zoom about the focus + final crop to the output size.

    scale with eval=frame gives frames whose size changes every frame, but the next filter's input
    keeps the first frame's size: a crop reading iw/ih (0.5.0 and earlier) stayed at x = y = 0, so every
    zoom grew from the top-left corner and focus_x/focus_y and shake did nothing while zooming. The
    scaled frame is therefore padded onto a fixed canvas (as big as the largest zoom) and cropped at a
    position computed from the zoom factor itself."""
    z = zoom_expr(shot, cfg, dur, index)
    if z == "1":                                  # no zoom: no free span, so no focus or shake either
        return f"scale={W}:{H}:flags=bicubic"
    fx = float(shot.get("focus_x", 0.5))
    fy = float(shot.get("focus_y", 0.5))
    sw, sh = f"trunc({W}*({z})/2)*2", f"trunc({H}*({z})/2)*2"
    margin = float(cfg["framing"].get("zoom_margin", 1.25))     # zoom_expr caps the zoom at the margin
    cw, ch = _even(W * margin) + 2, _even(H * margin) + 2
    x, y = crop_expr(shot, fx, fy, f"{sw}-{W}", f"{sh}-{H}")
    return (f"scale=w='{sw}':h='{sh}':eval=frame:flags=bicubic,pad=w={cw}:h={ch}:x=0:y=0:eval=frame,"
            f"crop={W}:{H}:{x}:{y}")


def flash_filters(shot: dict) -> str:
    f = []
    for e in effects_of(shot, "flash"):
        t0 = float(e.get("t", 0))
        f.append(f"eq=brightness='0.45*max(0,1-abs(t-{t0:.3f})/0.09)':eval=frame")
    return ",".join(f)


# ── audio ────────────────────────────────────────────────────────────────────

def shot_audio_chain(cfg: dict, shot: dict, voice: bool, dur_out: float) -> str:
    """Audio filters of one shot (before joining)."""
    a = cfg["audio"]
    f = ["aresample=48000", "aformat=channel_layouts=stereo"]
    if not a.get("use_original", True) or shot.get("mute"):
        return "volume=0," + ",".join(f) + f",apad,atrim=0:{dur_out:.4f}"
    if a.get("highpass"):
        f.append(f"highpass=f={int(a['highpass'])}")
    if a.get("denoise"):
        f.append(f"afftdn=nr={float(a['denoise']):.1f}:nf=-42:tn=1")
    if voice and a.get("voice_eq", True):
        f += ["equalizer=f=250:t=q:w=1.2:g=-2.5", "equalizer=f=3200:t=q:w=1.0:g=3", "deesser=i=0.3"]
    if a.get("asmr") and not voice:   # ASMR: brighter crackles, soft sounds come up (strong compression)
        f += ["equalizer=f=200:t=q:w=1.0:g=-2", "equalizer=f=4500:t=q:w=1.2:g=3",
              "equalizer=f=9000:t=q:w=1.0:g=4",
              "acompressor=threshold=-34dB:ratio=4:attack=3:release=90:makeup=6",
              "alimiter=limit=0.9:attack=1:release=40:level=0"]
    elif a.get("compressor", True):
        f.append("acompressor=threshold=-22dB:ratio=3:attack=8:release=160:makeup=2")
    gain = float(shot.get("volume_db", 0)) + (0 if voice else float(a.get("gain_without_voice_db", 0)))
    if gain:
        f.append(f"volume={gain:.1f}dB")
    v = float(shot.get("speed", 1.0))
    while v > 2.0:
        f.append("atempo=2.0")
        v /= 2
    while v < 0.5:
        f.append("atempo=0.5")
        v /= 0.5
    if abs(v - 1) > 1e-3:
        f.append(f"atempo={v:.4f}")
    f += ["afade=t=in:d=0.015", "apad", f"atrim=0:{dur_out:.4f}",
          f"afade=t=out:st={max(dur_out - 0.03, 0):.4f}:d=0.03"]
    return ",".join(f)


def music_covers(music_cfg: dict, total: float) -> bool:
    """True for a playlist (AutoMix) whose mix, from music._start on, lasts at least `total` seconds.
    Single tracks keep the looping chain unchanged."""
    mix = music_cfg.get("_automix") or {}
    try:
        return bool(mix) and float(mix.get("duration") or 0) >= float(total) - 0.05
    except (TypeError, ValueError):
        return False


def output_fades(cfg: dict, total: float) -> str:
    """Video fade from black (output.fade_in s) and to black (output.fade_out s) of the whole video,
    "" when both are 0. The fade-out reaches black on the last frame, which lasts until `total`."""
    o = cfg.get("output") or {}
    fps = int(o.get("fps", 30))
    fi, fo = max(float(o.get("fade_in") or 0), 0.0), max(float(o.get("fade_out") or 0), 0.0)
    f = []
    if fi > 0:
        f.append(f"fade=t=in:st=0:d={min(fi, total):.4f}")
    if fo > 0:
        fo = min(fo, total)
        f.append(f"fade=t=out:st={max(total - fo, 0):.4f}:d={max(fo - 1 / fps, 1 / fps):.4f}")
    return ",".join(f)


def final_mix(voice_in: str, total: float, cfg: dict, music_idx: int | None, sfx_idx: int | None,
              has_voice: bool, stems: dict | None = None) -> tuple[str, str]:
    """Mix filter: shot audio + music (with ducking) + sound-effect track.

    stems: if a dict is given, every part is also split off (asplit, same pass) and the dict is
    filled with {"original" | "music" | "sfx": output label} for the caller to map to stem files.
    "original" = the shots' own audio after processing, "music" = after volume/fades/ducking.
    """
    m, parts = cfg["music"], []
    if stems is not None:
        parts.append(f"{voice_in}asplit=2[orig_mix][st_original]")
        voice_in = "[orig_mix]"
        stems["original"] = "[st_original]"
    mix = [voice_in]
    if music_idx is not None:
        vol = m["volume_without_voice_db"] if (m.get("ducking", True) or not has_voice) else m["volume_with_voice_db"]
        # a playlist mix that already covers the video is never looped (aloop would keep it all in RAM)
        loop = "" if music_covers(m, total) else "aloop=loop=-1:size=2147483647,"
        chain = (f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                 f"atrim=start={float(m.get('_start', 0)):.3f},asetpts=PTS-STARTPTS,"
                 f"{loop}atrim=0:{total:.4f},volume={float(vol):.1f}dB,"
                 f"afade=t=in:d={float(m.get('fade_in', 0.25)):.2f},"
                 f"afade=t=out:st={max(total - float(m['fade_out']), 0):.3f}:d={float(m['fade_out']):.3f}")
        if m.get("ducking", True) and (has_voice or m.get("ducking_asmr")):
            # the music ducks by itself under the voice (or under the ASMR sounds of an unboxing)
            ratio, threshold = (8, 0.04) if has_voice else (float(m.get("ducking_ratio", 4)), 0.03)
            parts.append(f"{voice_in}asplit=2[vz][sc]")
            parts.append(chain + "[mus0]")
            parts.append(f"[mus0][sc]sidechaincompress=threshold={threshold}:ratio={ratio}:attack=20:release=300[mus]")
            mix = ["[vz]", "[mus]"]
        else:
            parts.append(chain + "[mus]")
            mix.append("[mus]")
        if stems is not None:
            parts.append("[mus]asplit=2[mus_mix][st_music]")
            mix[mix.index("[mus]")] = "[mus_mix]"
            stems["music"] = "[st_music]"
    if sfx_idx is not None:
        if stems is not None:
            parts.append(f"[{sfx_idx}:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[sfx][st_sfx]")
            stems["sfx"] = "[st_sfx]"
        else:
            parts.append(f"[{sfx_idx}:a]aresample=48000,aformat=channel_layouts=stereo[sfx]")
        mix.append("[sfx]")
    if len(mix) == 1:
        parts.append(f"{voice_in}anull[amix]")
    else:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first[amix]")
    return ";".join(parts), "[amix]"


# ── joining and export ───────────────────────────────────────────────────────

def join(segments: list[dict], fps: int) -> tuple[str, str, str]:
    """Filter joining inputs [i:v]/[i:a] with their transitions.

    segments: [{"dur": s, "overlap": s, "transition": name}, …] (in order, inputs 0..n-1); an
    optional "skip" (seconds) drops the start of that input (a chunk border, see encoder._chunks);
    "renumber": timestamps rebuilt from the frame / sample count (the chunk list of a long video);
    "exact_audio": the input's audio padded / cut to exactly "dur" (48 kHz) — a segment whose audio
    stopped at the last whole codec frame (up to 21 ms short) would shorten every crossfade after it;
    "end" (seconds, after any skip): the input is cut there, its video after round(end * fps) frames
    and its audio after round(end * 48000) samples, so the graph itself stops at a chunk border
    instead of the output options closing it early (see encoder._chunks).
    Returns (filter, video_label, audio_label).
    """
    # normalized inputs (same time base: xfade requires it). Timestamps start at 0 BEFORE fps: setpts
    # marks its output as variable frame rate and FFmpeg 7.x xfade refuses inputs without a frame rate.
    parts = []
    for i, s in enumerate(segments):
        vs, as_ = "setpts=PTS-STARTPTS,", ""
        if s.get("renumber"):       # chunks read one after another: timestamps from the frame / sample count
            vs, as_ = f"setpts=N/({fps}*TB),", "asetpts=N/SR/TB,"
        if s.get("skip"):           # half a frame early: the container's millisecond timestamps
            vs = f"trim=start={max(float(s['skip']) - 0.5 / fps, 0):.6f},setpts=PTS-STARTPTS,"
            as_ = f"atrim=start={float(s['skip']):.6f},"
        pad = cut = ""
        end = s.get("end")
        if end is not None:         # counted after fps: exact whatever the container's timestamps
            cut = f",trim=end_frame={max(int(round(float(end) * fps)), 1)}"
        if s.get("exact_audio"):
            samples = int(round(float(s["dur"] if end is None else min(float(s["dur"]), float(end))) * 48000))
            pad = f",apad=whole_len={samples},atrim=end_sample={samples}"
        elif end is not None:
            pad = f",atrim=end_sample={max(int(round(float(end) * 48000)), 1)}"
        parts.append(f"[{i}:v]{vs}fps={fps}{cut},settb=AVTB[sv{i}];"
                     f"[{i}:a]{as_}asetpts=PTS-STARTPTS{pad}[sa{i}]")
    v, a = "[sv0]", "[sa0]"
    length = segments[0]["dur"]
    exact = bool(segments[0].get("exact_audio"))     # the joined audio so far is exactly as long as planned
    for i, s in enumerate(segments[1:], start=1):
        xf = xfade_name(s["transition"])
        nv, na = f"[v{i}]", f"[a{i}]"
        exact = exact and bool(s.get("exact_audio"))
        if (xf is None or s["overlap"] <= 0) and exact:
            # exact audio on both sides: picture and sound joined apart. A joint concat pads the audio to
            # the video's microsecond timestamps (a few samples per cut, 6 in 10 cuts of 0.5 s at 30 fps)
            parts.append(f"{v}[sv{i}]concat=n=2:v=1:a=0[cv{i}];{a}[sa{i}]concat=n=2:v=0:a=1{na};"
                         f"[cv{i}]settb=AVTB{nv}")
            length += s["dur"]
        elif xf is None or s["overlap"] <= 0:
            parts.append(f"{v}{a}[sv{i}][sa{i}]concat=n=2:v=1:a=1[cv{i}]{na};[cv{i}]settb=AVTB{nv}")
            length += s["dur"]
        else:
            off = length - s["overlap"]
            parts.append(f"{v}[sv{i}]xfade=transition={xf}:duration={s['overlap']:.4f}:offset={off:.4f}{nv}")
            parts.append(f"{a}[sa{i}]acrossfade=d={s['overlap']:.4f}:c1=tri:c2=tri{na}")
            length += s["dur"] - s["overlap"]
        v, a = nv, na
    return ";".join(parts), v, a


def double_rate(rate) -> str:
    """Buffer size = twice the max rate, keeping the unit: '20M' → '40M', '7.5M' → '15M', '800k' → '1600k'."""
    s = str(rate).strip()
    unit = s[-1] if s[-1:].isalpha() else ""
    try:
        value = float(s[:-1] if unit else s)
    except ValueError:
        return s
    doubled = value * 2
    return f"{int(doubled) if doubled.is_integer() else round(doubled, 3)}{unit}"


def video_output_options(cfg: dict, draft: bool) -> list[str]:
    e = cfg["export"]
    if draft:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"]
    mr = e.get("maxrate", "20M")
    return ["-c:v", "libx264", "-preset", "slow", "-crf", str(e.get("crf", 17)), "-maxrate", str(mr),
            "-bufsize", double_rate(mr), "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-g", str(int(cfg["output"]["fps"]) * 2)]
