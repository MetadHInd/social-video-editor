"""Render: convierte plan.json en el video final.

Pasos:
  1. Cada toma se renderiza sola desde el ORIGINAL (HDR→SDR, encuadre, color, zoom, velocidad, audio limpio).
     Se guarda en cache/segmentos/ con una huella: si la toma no cambia, no se vuelve a renderizar.
  2. Se unen con sus transiciones, se monta la capa de textos/subtítulos, la música y los efectos de sonido.
  3. Se masteriza el audio a −14 LUFS y se exportan video, portada, .srt y resumen en una carpeta vN nueva.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from . import audio, color, encuadre, exportar, subtitulos
from .analisis import analizar
from .efectos import congelar, filtros_extra
from .momentos import resolver_rangos
from .plan import ajustar_a_beats, alinear_a_golpes, duracion_total, linea_de_tiempo, textos_absolutos
from .proxy import preparar
from .transiciones import info as info_trans
from .transiciones import unir
from .util import ejecutar, guardar_json, log, mmss


def _stats_toma(info: dict, ini: float, fin: float) -> dict:
    import cv2

    cap = cv2.VideoCapture(info["proxy"])
    frames = []
    for t in np.linspace(ini + 0.05, max(fin - 0.05, ini + 0.06), 6):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    return color.medir(frames)


def _hay_voz(an: dict, ini: float, fin: float) -> bool:
    hablado = sum(max(0, min(b, fin) - max(a, ini)) for a, b in an["voz"]["tramos"])
    return hablado / max(fin - ini, 1e-3) > 0.3


def _segmento(tm: dict, info: dict, an: dict, cfg: dict, W: int, H: int, cache: Path, borrador: bool) -> Path:
    fps = int(cfg["salida"]["fps"])
    claves_cfg = {k: cfg.get(k) for k in ("color", "encuadre", "audio", "efectos")}
    firma = json.dumps({"t": {k: tm.get(k) for k in ("fuente", "ini", "fin", "velocidad", "efectos", "zoom",
                                                       "enfoque_x", "enfoque_y", "volumen_db", "silenciar",
                                                       "congelar", "color", "_golpes")},
                        "par": tm["i"] % 2, "dur": tm["dur"], "src": info["ruta"], "W": W, "H": H,
                        "cfg": claves_cfg, "b": borrador, "v": 4}, sort_keys=True, default=str)
    ruta = cache / "segmentos" / f"{hashlib.sha1(firma.encode()).hexdigest()[:16]}.mkv"
    if ruta.exists():
        return ruta
    ruta.parent.mkdir(parents=True, exist_ok=True)
    v = float(tm.get("velocidad", 1.0))
    stats = _stats_toma(info, tm["ini"], tm["fin"])
    cfg_toma = cfg if not tm.get("color") else {**cfg, "color": {**cfg["color"], **tm["color"]}}
    corr = color.cadena_correccion(stats, cfg_toma)
    vf = [encuadre.escala_previa(info, W, H, cfg, tm),
          color.cadena_sdr(info, float(cfg["color"].get("hdr_blanco", 0.62))),
          corr,
          f"setpts=(PTS-STARTPTS)/{v:.4f}",
          encuadre.movimiento(W, H, cfg, tm, tm["dur"], tm["i"]),
          filtros_extra(tm)]
    cg = congelar(tm)
    if cg > 0:
        vf.append(f"tpad=stop_mode=clone:stop_duration={cg:.3f}")
    vf.append(f"fps={fps},format=yuv420p,setsar=1")
    vf = ",".join(x for x in vf if x)
    af = audio.cadena_toma(info, cfg, tm, _hay_voz(an, tm["ini"], tm["fin"]), tm["dur"])
    cmd = ["ffmpeg", "-hwaccel", "videotoolbox", "-ss", f"{tm['ini']:.3f}", "-t", f"{tm['fin'] - tm['ini']:.3f}",
           "-i", info["ruta"]]
    if info["audio"]:
        mapa_a = f"0:{info['audio_indice']}"
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        mapa_a = "1:a"
    cmd += ["-map", "0:v:0", "-map", mapa_a, "-vf", vf, "-af", af, "-t", f"{tm['dur']:.4f}",
            "-c:v", "libx264", "-preset", "ultrafast" if borrador else "fast", "-crf", "22" if borrador else "12",
            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", ruta.with_suffix(".tmp.mkv")]
    ejecutar(cmd)
    ruta.with_suffix(".tmp.mkv").rename(ruta)
    return ruta


def _segmento_especial(tm: dict, segs: list, cfg: dict, W: int, H: int, cache: Path) -> Path:
    """Tomas dibujadas (polaroid, collage) a partir de otras tomas ya renderizadas."""
    from . import collage, polaroid

    fps = int(cfg["salida"]["fps"])
    refs = [str(segs[k]) for k in ([tm["de_toma"]] if tm["tipo"] == "polaroid"
                                    else list(tm["de_tomas"]) + [tm.get("fondo_de", tm["de_tomas"][-1])])]
    firma = json.dumps({"tm": {k: v for k, v in tm.items() if k not in ("out_ini", "out_fin", "i", "solape")},
                        "refs": refs, "W": W, "H": H, "cfg": {k: cfg.get(k) for k in ("polaroid", "collage")},
                        "v": 1}, sort_keys=True, default=str)
    ruta = cache / "segmentos" / f"{tm['tipo']}_{hashlib.sha1(firma.encode()).hexdigest()[:14]}.mkv"
    if ruta.exists():
        return ruta
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if tm["tipo"] == "polaroid":
        return polaroid.render(tm, segs[tm["de_toma"]], W, H, fps, cfg, ruta)
    if tm["tipo"] == "collage":
        return collage.render(tm, [segs[k] for k in tm["de_tomas"]], segs[tm.get("fondo_de", tm["de_tomas"][-1])],
                              W, H, fps, cfg, ruta)
    raise SystemExit(f"Tipo de toma desconocido: {tm['tipo']}")


def _preparar_musica(proy, plan: dict, cfg: dict, infos: list, sin_musica: bool):
    """Elige la pista, calcula desde dónde suena y alinea los cortes a sus golpes."""
    musica = audio.elegir_musica(cfg, proy.dir)
    if not musica:
        log("Sin música (pon una pista en musica/ o en ajustes.musica.archivo)")
        return None
    m = cfg["musica"]
    am = audio.analizar_musica(musica, proy.cache)
    T = am.get("intervalo", 0.5)
    if m.get("entrada_seg", 0) == "auto":
        drop_en = m.get("drop_en_seg")
        if drop_en is None:
            t0 = plan["tomas"][0]
            drop_en = int(t0["golpes"]) * T if "golpes" in t0 else linea_de_tiempo(plan, cfg)[0]["dur"]
        entrada = audio.entrada_auto(am, float(drop_en))
    else:
        entrada = float(m.get("entrada_seg", 0))
    m["_entrada"] = round(entrada, 3)
    golpes = audio.golpes_en_video(am, entrada, 600)
    duraciones = [i["duracion"] for i in infos]
    if any("golpes" in tm for tm in plan["tomas"]) or m.get("alinear") == "exacto":
        alinear_a_golpes(plan, golpes, cfg, duraciones)
        modo = "cada corte en un golpe"
    elif m.get("cortes_en_beat", True):
        modo = f"{ajustar_a_beats(plan, golpes, cfg, duraciones)} cortes movidos al ritmo"
    else:
        modo = "sin ajuste de cortes"
    log(f"Música: {musica.name} — {am.get('tempo')} BPM, empieza en {entrada:.2f} s "
        f"(drop de la canción en {am.get('drop', 0):.2f} s) — {modo}"
        + (" — NO se mezcla (--sin-musica)" if sin_musica else ""))
    return None if sin_musica else musica


def renderizar(proy, plan: dict, borrador: bool = False, perfil: str | None = None,
               sin_musica: bool = False) -> Path:
    t0 = time.time()
    cfg = copy.deepcopy(proy.cfg)
    if perfil:
        p = exportar.PERFILES[perfil]
        cfg["salida"].update(ancho=p["ancho"], alto=p["alto"])
        cfg["exportar"].update(crf=p["crf"], maxrate=p["maxrate"])
    W, H, fps = int(cfg["salida"]["ancho"]), int(cfg["salida"]["alto"]), int(cfg["salida"]["fps"])
    if borrador:
        W, H = W // 2 // 2 * 2, H // 2 // 2 * 2
    plan = copy.deepcopy(plan)
    infos = [preparar(f, proy.cache, cfg) for f in proy.fuentes]
    analisis = [analizar(i, proy.cache, cfg) for i in infos]
    resolver_rangos(plan, analisis, cfg)
    for tm in plan["tomas"]:           # ya resuelto: plan_usado.json guarda los tiempos finales
        if "buscar" in tm:
            tm["_buscado"] = tm.pop("buscar")
    musica = _preparar_musica(proy, plan, cfg, infos, sin_musica)

    tl = linea_de_tiempo(plan, cfg)
    total = duracion_total(tl)
    log(f"Línea de tiempo: {len(tl)} tomas, {total:.1f} s, {W}×{H}{' (borrador)' if borrador else ''}")

    segs = [None] * len(tl)
    for tm in tl:
        if tm.get("tipo"):
            continue
        log(f"  toma {tm['i'] + 1}/{len(tl)}: fuente {tm['fuente']} {mmss(tm['ini'])}→{mmss(tm['fin'])} "
            f"x{tm.get('velocidad', 1)} [{tm.get('transicion')}] {tm.get('rol', '')}")
        segs[tm["i"]] = _segmento(tm, infos[tm["fuente"]], analisis[tm["fuente"]], cfg, W, H, proy.cache, borrador)
    for tm in tl:
        if tm.get("tipo"):
            log(f"  toma {tm['i'] + 1}/{len(tl)}: {tm['tipo']} ({tm['dur']:.2f} s) [{tm.get('transicion')}]")
            segs[tm["i"]] = _segmento_especial(tm, segs, cfg, W, H, proy.cache)

    # Versión principal = número nuevo (v4). Variantes = mismo número que la última principal + sufijo
    # (v4SinMusica, v4Borrador, v4Youtube…), para que se vea de qué versión salen.
    sufijo = ("Borrador" if borrador else "") + (perfil.capitalize() if perfil else "") + \
             ("SinMusica" if sin_musica else "")
    vdir = proy.carpeta_variante(sufijo) if sufijo else proy.nueva_version()
    nombre = (proy.nombre + ("_borrador" if borrador else "") + (f"_{perfil}" if perfil else "")
              + ("_sin_musica" if sin_musica else ""))
    textos = textos_absolutos(plan, tl)
    grupos = subtitulos.grupos_subtitulos(analisis, tl, cfg) if cfg["subtitulos"].get("auto", True) else []
    capa, teclas = subtitulos.construir_capa(textos, grupos, cfg, W, H, total, vdir / "_capa")

    # Efectos de sonido → una sola pista
    sx = cfg["sfx"]
    base = float(sx.get("volumen_db", -12))
    eventos = []
    if sx.get("activado", True):
        for tm in tl[1:]:
            snd = info_trans(tm.get("transicion", "corte"))[2]
            if snd and sx.get("whoosh_en_transiciones", True):
                eventos.append((snd, max(tm["out_ini"] - (0.12 if snd == "whoosh" else 0), 0), base))
        for tx in textos:
            anim = tx.get("animacion", cfg["textos"]["estilos"].get(tx.get("estilo", "simple"), {}).get("animacion"))
            if sx.get("pop_en_textos", True) and anim == "pop" and tx.get("sonido", True):
                eventos.append(("pop", tx["ini"], base - 2))
            if tx.get("sfx"):
                eventos.append((tx["sfx"], tx["ini"], base))
        if sx.get("tecleo", True):
            eventos += [("tecla", t, base + float(sx.get("tecleo_db", -9))) for t in teclas]
        for tm in tl:
            if tm.get("tipo") == "polaroid":
                eventos.append(("obturador", tm["out_ini"], base + 3))
            if tm.get("tipo") == "collage":
                from .collage import tiempos_aparicion
                eventos += [("pop", tm["out_ini"] + t, base - 4)
                            for t in tiempos_aparicion(tm, len(tm["de_tomas"]),
                                                       int(cfg.get("collage", {}).get("subdivision", 2)))]

    # Entradas: segmentos, capa, música, sfx
    entradas = []
    for s_ in segs:
        entradas += ["-i", s_]
    idx = len(segs)
    idx_capa = idx_mus = idx_sfx = None
    if capa:
        entradas += ["-f", "concat", "-safe", "0", "-i", capa]
        idx_capa, idx = idx, idx + 1
    if musica:
        entradas += ["-i", musica]
        idx_mus, idx = idx, idx + 1
    if eventos:
        entradas += ["-i", audio.pista_sfx(eventos, total, vdir / "_sfx.wav")]
        idx_sfx, idx = idx, idx + 1

    union, v, a = unir([{"dur": tm["dur"], "solape": tm["solape"], "transicion": tm.get("transicion", "corte")}
                        for tm in tl], fps)
    partes = [union]
    if idx_capa is not None:
        partes.append(f"[{idx_capa}:v]fps={fps},format=rgba,setpts=PTS-STARTPTS[txt];"
                      f"{v}[txt]overlay=0:0:format=auto:eof_action=pass:shortest=0[vt]")
        v = "[vt]"
    if cfg["efectos"].get("barra_progreso"):
        alto_b = max(int(H * 0.006), 4)
        col = cfg["efectos"].get("color_barra", "#FFFFFF").lstrip("#")
        partes.append(f"color=c=0x{col}:s={W}x{alto_b}:r={fps}:d={total:.3f}[barra];"
                      f"{v}[barra]overlay=x='-w+w*t/{total:.3f}':y=0[vb]")
        v = "[vb]"
    partes.append(f"{v}format=yuv420p,setsar=1[vout]")
    hay_voz = any(_hay_voz(analisis[tm["fuente"]], tm["ini"], tm["fin"]) for tm in tl if not tm.get("tipo"))
    mezcla, am = audio.mezcla_final(a, total, cfg, idx_mus, idx_sfx, hay_voz)
    partes.append(mezcla)
    premaster = vdir / f"_{nombre}_premaster.mkv"
    script = vdir / "_filtro.txt"
    script.write_text(";\n".join(partes))
    final = vdir / f"{nombre}.mp4"
    try:
        ejecutar(["ffmpeg", *entradas, "-/filter_complex", script, "-map", "[vout]", "-map", am,
                  "-t", f"{total:.4f}", "-r", str(fps), *exportar.opciones_video(cfg, borrador),
                  "-c:a", "pcm_s16le", "-ar", "48000", premaster])
        lufs = audio.masterizar(premaster, final, cfg)
    except Exception:
        fallida = vdir.with_name(vdir.name + "_fallida")
        vdir.rename(fallida)
        log(f"El render falló; archivos de diagnóstico en {fallida}")
        raise
    premaster.unlink(missing_ok=True)
    (vdir / "_sfx.wav").unlink(missing_ok=True)
    import shutil
    shutil.rmtree(vdir / "_capa", ignore_errors=True)   # imágenes temporales de la capa de textos

    # Extras
    exportar.srt(grupos, textos, vdir / f"{nombre}.srt")
    t_portada = min(float(cfg["exportar"].get("portada_seg", 1.6)), total / 2)
    exportar.portada(final, t_portada, vdir / f"{nombre}_portada.jpg")
    guardar_json(vdir / "plan_usado.json", plan)
    guardar_json(vdir / "config_usada.json", cfg)
    resumen = {
        "video": final.name, "duracion_seg": round(total, 2), "tomas": len(tl), "resolucion": f"{W}x{H}",
        "musica": None if not musica else {"archivo": musica.name, "desde_seg": cfg["musica"].get("_entrada")},
        "audio": lufs, "subtitulos_auto": len(grupos), "textos": len(textos), "efectos_sonido": len(eventos),
        "tiempo_render_seg": round(time.time() - t0, 1),
        "tomas_detalle": [{"n": tm["i"] + 1, "tipo": tm.get("tipo", "video"), "fuente": tm.get("fuente"),
                           "de": None if tm.get("tipo") else mmss(tm["ini"]),
                           "a": None if tm.get("tipo") else mmss(tm["fin"]),
                           "en_video": f"{mmss(tm['out_ini'])}–{mmss(tm['out_fin'])}",
                           "corte_seg": round(tm["out_fin"] - (tl[tm["i"] + 1]["solape"] / 2
                                                               if tm["i"] + 1 < len(tl) else 0), 3),
                           "rol": tm.get("rol"), "transicion": tm.get("transicion")} for tm in tl],
    }
    guardar_json(vdir / "resumen.json", resumen)
    log(f"Listo → {final}  ({total:.1f} s, {lufs.get('despues_lufs', '?')} LUFS, "
        f"{resumen['tiempo_render_seg']} s de render)")
    return final
