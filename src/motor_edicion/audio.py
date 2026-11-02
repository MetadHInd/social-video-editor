"""Audio: limpieza por toma, música con ducking, efectos de sonido y masterizado a −14 LUFS."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import ejecutar, espacio, ffmpeg_stderr, log

EXT_MUSICA = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")


def cadena_toma(info: dict, cfg: dict, toma: dict, voz: bool, dur_out: float) -> str:
    """Filtros de audio de una toma (antes de unir)."""
    a = cfg["audio"]
    f = ["aresample=48000", "aformat=channel_layouts=stereo"]
    if not a.get("usar_original", True) or toma.get("silenciar"):
        return "volume=0," + ",".join(f) + f",apad,atrim=0:{dur_out:.4f}"
    if a.get("paso_alto"):
        f.append(f"highpass=f={int(a['paso_alto'])}")
    if a.get("ruido"):
        f.append(f"afftdn=nr={float(a['ruido']):.1f}:nf=-42:tn=1")
    if voz and a.get("eq_voz", True):
        f += ["equalizer=f=250:t=q:w=1.2:g=-2.5", "equalizer=f=3200:t=q:w=1.0:g=3", "deesser=i=0.3"]
    asmr = a.get("asmr") and not voz
    if asmr:   # ASMR: más brillo y detalle del crujido, y los sonidos suaves suben (compresión fuerte)
        f += ["equalizer=f=200:t=q:w=1.0:g=-2", "equalizer=f=4500:t=q:w=1.2:g=3",
              "equalizer=f=9000:t=q:w=1.0:g=4",
              "acompressor=threshold=-34dB:ratio=4:attack=3:release=90:makeup=6",
              "alimiter=limit=0.9:attack=1:release=40:level=0"]
    elif a.get("compresor", True):
        f.append("acompressor=threshold=-22dB:ratio=3:attack=8:release=160:makeup=2")
    gan = float(toma.get("volumen_db", 0)) + (0 if voz else float(a.get("ganancia_sin_voz_db", 0)))
    if gan:
        f.append(f"volume={gan:.1f}dB")
    v = float(toma.get("velocidad", 1.0))
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


def elegir_musica(cfg: dict, proyecto_dir: Path) -> Path | None:
    m = cfg["musica"]
    if m.get("archivo") in (False, "ninguna", "no"):
        return None
    if m.get("archivo"):
        p = Path(m["archivo"]).expanduser()
        if not p.is_absolute():
            p = (proyecto_dir / p) if (proyecto_dir / p).exists() else espacio() / p
        if not p.exists():
            raise SystemExit(f"No encuentro la música: {p}")
        return p
    carpeta = espacio() / m.get("carpeta", "musica")
    pistas = sorted(p for p in carpeta.glob("*") if p.suffix.lower() in EXT_MUSICA) if carpeta.exists() else []
    return pistas[0] if pistas else None


def beats(musica: Path, desde: float, hasta: float) -> list[float]:
    """Golpes de la canción (en tiempo del video) usando librosa."""
    import librosa

    y, sr = librosa.load(str(musica), sr=22050, mono=True, offset=desde, duration=hasta + 2)
    _, bt = librosa.beat.beat_track(y=y, sr=sr, units="time")
    return [float(b) for b in bt if b <= hasta]


def analizar_musica(musica: Path, cache: Path) -> dict:
    """Tempo, rejilla regular de golpes y 'drop' (donde entra la batería / sube la energía)."""
    import json

    import librosa
    import numpy as np

    ruta = cache / f"musica_{musica.stem.replace(' ', '_')}.json"
    if ruta.exists() and ruta.stat().st_mtime > musica.stat().st_mtime:
        return json.loads(ruta.read_text())
    y, sr = librosa.load(str(musica), sr=22050, mono=True)
    dur = len(y) / sr
    tempo, bt = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bt = np.asarray(bt, float)
    if len(bt) < 4:
        return {"duracion": dur, "tempo": 0, "golpes": [], "drop": 0.0}
    intervalo = float(np.median(np.diff(bt)))
    k = np.round((bt - bt[0]) / intervalo)                      # ajuste por mínimos cuadrados a una rejilla fija
    T, b0 = np.polyfit(k, bt, 1)
    rejilla = [float(b0 + j * T) for j in range(int(np.floor(-b0 / T)), int((dur - b0) / T) + 1)]
    rejilla = [g for g in rejilla if 0 <= g <= dur]
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    tr = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)
    def nivel(a, b):
        m = (tr >= a) & (tr < b)
        return 20 * np.log10(rms[m].mean() + 1e-9) if m.any() else -90
    # drop: primer golpe detectado tras una zona sin golpes, o el mayor salto de energía (8 s antes/después)
    drop = float(bt[0]) if bt[0] > 4 * T else None
    if drop is None:
        saltos = [(nivel(g, g + 8) - nivel(g - 8, g), g) for g in rejilla if 8 <= g <= dur - 8]
        drop = max(saltos)[1] if saltos else 0.0
    datos = {"duracion": dur, "tempo": round(60 / T, 2), "intervalo": T, "golpes": rejilla, "drop": drop}
    ruta.write_text(json.dumps(datos))
    return datos


def golpes_en_video(am: dict, entrada: float, total: float) -> list[float]:
    """Golpes de la canción pasados a tiempo del video (la canción empieza en 'entrada')."""
    return [g - entrada for g in am["golpes"] if 0 <= g - entrada <= total + 1]


def entrada_auto(am: dict, drop_en: float) -> float:
    """Desde qué segundo arrancar la canción para que su 'drop' caiga en drop_en (segundo del video)."""
    if not am.get("golpes"):
        return 0.0
    return max(am["drop"] - drop_en, 0.0)   # si el drop está antes de drop_en, la canción empieza en 0


def pista_sfx(eventos: list[tuple[str, float, float]], total: float, ruta: Path) -> Path:
    """Mezcla todos los efectos de sonido en UNA pista (nombre, segundo, ganancia dB)."""
    import numpy as np
    import soundfile as sf

    from .efectos import sfx

    sr = 48000
    pista = np.zeros((int((total + 1) * sr), 2), dtype="float32")
    cache = {}
    for nombre, t, gdb in eventos:
        if nombre not in cache:
            x, r = sf.read(sfx(nombre), dtype="float32", always_2d=True)
            if r != sr:
                import librosa
                x = librosa.resample(x.T, orig_sr=r, target_sr=sr).T
            cache[nombre] = x if x.shape[1] == 2 else np.repeat(x[:, :1], 2, 1)
        x = cache[nombre] * (10 ** (gdb / 20))
        i = int(max(t, 0) * sr)
        j = min(i + len(x), len(pista))
        pista[i:j] += x[: j - i]
    sf.write(ruta, pista[: int(total * sr)], sr)
    return ruta


def mezcla_final(entrada_voz: str, total: float, cfg: dict, musica_idx: int | None,
                 sfx_idx: int | None, hay_voz: bool) -> tuple[str, str]:
    """Filtro de la mezcla: audio de las tomas + música (con ducking) + pista de efectos de sonido."""
    m, partes, mezclar = cfg["musica"], [], [entrada_voz]
    if musica_idx is not None:
        vol = m["volumen_sin_voz_db"] if (m.get("ducking", True) or not hay_voz) else m["volumen_con_voz_db"]
        cad = (f"[{musica_idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
               f"atrim=start={float(m.get('_entrada', 0)):.3f},asetpts=PTS-STARTPTS,"
               f"aloop=loop=-1:size=2147483647,atrim=0:{total:.4f},volume={float(vol):.1f}dB,"
               f"afade=t=in:d={float(m.get('fundido_entrada', 0.25)):.2f},"
               f"afade=t=out:st={max(total - float(m['fundido_salida']), 0):.3f}:d={float(m['fundido_salida']):.3f}")
        if m.get("ducking", True) and (hay_voz or m.get("ducking_asmr")):
            # la música baja sola cuando suena la voz (o los sonidos ASMR del unboxing)
            ratio, umbral = (8, 0.04) if hay_voz else (float(m.get("ducking_ratio", 4)), 0.03)
            partes.append(f"{entrada_voz}asplit=2[vz][sc]")
            partes.append(cad + "[mus0]")
            partes.append(f"[mus0][sc]sidechaincompress=threshold={umbral}:ratio={ratio}:attack=20:release=300[mus]")
            mezclar = ["[vz]", "[mus]"]
        else:
            partes.append(cad + "[mus]")
            mezclar.append("[mus]")
    if sfx_idx is not None:
        partes.append(f"[{sfx_idx}:a]aresample=48000,aformat=channel_layouts=stereo[sfx]")
        mezclar.append("[sfx]")
    if len(mezclar) == 1:
        partes.append(f"{entrada_voz}anull[amix]")
    else:
        partes.append(f"{''.join(mezclar)}amix=inputs={len(mezclar)}:normalize=0:duration=first[amix]")
    return ";".join(partes), "[amix]"


def medir_loudness(ruta: Path, cfg: dict) -> dict:
    a = cfg["audio"]
    err = ffmpeg_stderr(["-i", str(ruta), "-vn", "-af",
                         f"loudnorm=I={a['loudness']}:TP={a['pico']}:LRA=11:print_format=json", "-f", "null", "-"])
    bloque = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    return json.loads(bloque[-1]) if bloque else {}


def masterizar(entrada: Path, salida: Path, cfg: dict) -> dict:
    """Lleva el audio a −14 LUFS con pico máximo −1 dBTP. El video se copia sin recodificar.

    Ganancia + limitador en hasta 3 pasadas: los sonidos ASMR (rasgar, abrir) tienen picos muy altos
    y un normalizador lineal no puede subir el volumen sin pasarse del pico.
    """
    a = cfg["audio"]
    objetivo, pico = float(a["loudness"]), float(a["pico"])
    m0 = medir_loudness(entrada, cfg)
    if not m0:
        log("Aviso: no pude medir el volumen; se copia sin masterizar.")
        ejecutar(["ffmpeg", "-i", entrada, "-c", "copy", salida])
        return {}
    limite = 10 ** ((pico - 1.2) / 20)          # margen para el pico real tras codificar en AAC
    original = entrada.with_name(entrada.stem + "_audio.wav")
    trabajo = entrada.with_name(entrada.stem + "_master.wav")
    ejecutar(["ffmpeg", "-i", entrada, "-map", "0:a", "-c:a", "pcm_s24le", original])
    ganancia, medido = 0.0, float(m0["input_i"])
    for _ in range(5):   # el limitador "se come" volumen: se corrige la ganancia total sobre el original
        if abs(medido - objetivo) < 0.3:
            break
        ganancia += objetivo - medido
        ejecutar(["ffmpeg", "-i", original, "-af",
                  f"volume={ganancia:.2f}dB,aresample=192000,"   # sobremuestreo 4x: controla el pico real
                  f"alimiter=limit={limite:.4f}:attack=1:release=60:level=0,aresample=48000",
                  "-c:a", "pcm_s24le", trabajo])
        medido = float(medir_loudness(trabajo, cfg).get("input_i", objetivo))
    ejecutar(["ffmpeg", "-i", entrada, "-i", trabajo if trabajo.exists() else original,
              "-map", "0:v", "-map", "1:a", "-c:v", "copy",
              "-c:a", "aac", "-b:a", f"{int(cfg['exportar']['audio_kbps'])}k", "-ar", "48000",
              "-movflags", "+faststart", salida])
    original.unlink(missing_ok=True)
    trabajo.unlink(missing_ok=True)
    final = medir_loudness(salida, cfg)
    return {"antes_lufs": m0.get("input_i"), "despues_lufs": final.get("input_i"), "pico_dbtp": final.get("input_tp")}
