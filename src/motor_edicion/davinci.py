"""Paquete para DaVinci Resolve (versión gratuita): línea de tiempo FCPXML + subtítulos SRT.

La versión gratuita de Resolve no se puede controlar con scripts desde fuera, pero sí importa
FCPXML: así abres la misma edición (con los ORIGINALES en 4K/HDR) para retocar color en la
página Color, audio en Fairlight o textos a mano.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from .analisis import analizar
from .audio import elegir_musica
from .momentos import resolver_rangos
from .plan import duracion_total, linea_de_tiempo, textos_absolutos
from .proxy import preparar


def _r(seg: float, fps: int) -> str:
    return f"{int(round(seg * fps))}/{fps}s"


def _entrada(cfg: dict) -> float:
    m = cfg["musica"]
    v = m.get("_entrada", m.get("entrada_seg", 0))
    return float(v) if isinstance(v, (int, float)) else 0.0


def exportar(proy, plan: dict, carpeta: Path) -> Path:
    cfg = proy.cfg
    fps, W, H = int(cfg["salida"]["fps"]), int(cfg["salida"]["ancho"]), int(cfg["salida"]["alto"])
    infos = [preparar(f, proy.cache, cfg) for f in proy.fuentes]
    resolver_rangos(plan, [analizar(i, proy.cache, cfg) for i in infos], cfg)
    tl = linea_de_tiempo(plan, cfg)
    musica = elegir_musica(cfg, proy.dir)
    rec = [f'<format id="r0" name="Vertical{W}x{H}p{fps}" frameDuration="1/{fps}s" width="{W}" height="{H}"/>']
    for k, i in enumerate(infos, start=1):
        rec.append(f'<format id="f{k}" frameDuration="1/{fps}s" width="{i["ancho"]}" height="{i["alto"]}"/>')
        rec.append(f'<asset id="a{k}" name="{escape(Path(i["ruta"]).stem)}" start="0s" '
                   f'duration="{_r(i["duracion"], fps)}" hasVideo="1" hasAudio="{1 if i["audio"] else 0}" '
                   f'format="f{k}" audioSources="1" audioChannels="2" audioRate="48000">'
                   f'<media-rep kind="original-media" src="{Path(i["ruta"]).as_uri()}"/></asset>')
    if musica:
        rec.append(f'<asset id="m1" name="{escape(musica.stem)}" start="0s" duration="3600/1s" hasAudio="1" '
                   f'audioSources="1" audioChannels="2" audioRate="48000">'
                   f'<media-rep kind="original-media" src="{musica.as_uri()}"/></asset>')
    clips, off = [], 0.0   # en FCPXML se usan cortes secos (las transiciones se ven en el MP4 del motor)
    for n, tm in enumerate(tl):
        if tm.get("tipo"):          # polaroid/collage: hueco (están en el MP4 del motor)
            off += float(tm["dur"])
            continue
        v = float(tm.get("velocidad", 1.0))
        dur = tm["fin"] - tm["ini"]
        dur_out = dur / v
        tmap = ""
        if abs(v - 1) > 1e-3:
            tmap = (f'<timeMap><timept time="{_r(tm["ini"], fps)}" value="{_r(tm["ini"], fps)}" interp="linear"/>'
                    f'<timept time="{_r(tm["ini"] + dur_out, fps)}" value="{_r(tm["fin"], fps)}" interp="linear"/>'
                    f'</timeMap>')
        extra = ""
        if n == 0 and musica:
            extra = (f'<asset-clip ref="m1" lane="-1" offset="{_r(tm["ini"], fps)}" '
                     f'start="{_r(_entrada(cfg), fps)}" '
                     f'duration="{_r(duracion_total(tl), fps)}" name="musica"/>')
        clips.append(f'<asset-clip ref="a{tm["fuente"] + 1}" offset="{_r(off, fps)}" name="toma {n + 1} ({tm.get("rol", "")})" '
                     f'start="{_r(tm["ini"], fps)}" duration="{_r(dur_out, fps)}" format="f{tm["fuente"] + 1}">'
                     f'{tmap}{extra}</asset-clip>')
        off += dur_out
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">\n'
           f'<resources>\n' + "\n".join(rec) + '\n</resources>\n'
           f'<library><event name="motor_edicion"><project name="{escape(proy.nombre)}">'
           f'<sequence format="r0" duration="{_r(off, fps)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" '
           f'audioRate="48k"><spine>\n' + "\n".join(clips) + '\n</spine></sequence></project></event></library>\n'
           f'</fcpxml>\n')
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"{proy.nombre}_davinci.fcpxml"
    ruta.write_text(xml, encoding="utf-8")

    from .exportar import srt
    srt([], textos_absolutos(plan, tl), carpeta / f"{proy.nombre}_davinci_textos.srt")
    (carpeta / "LEEME_DAVINCI.txt").write_text(
        "CÓMO ABRIR ESTA EDICIÓN EN DAVINCI RESOLVE (gratis)\n"
        "1. Abre Resolve → crea un proyecto nuevo.\n"
        f"2. File → Import → Timeline… → elige {ruta.name}.\n"
        "   (Si pregunta, desmarca 'Automatically set project settings' y deja la línea de tiempo en "
        f"{W}×{H}, {fps} fps.)\n"
        "3. Los clips enlazan a los ORIGINALES (4K, algunos HDR). Para que el HDR del iPhone se vea bien:\n"
        "   Project Settings → Color Management → Color science: DaVinci YRGB Color Managed →\n"
        "   Output color space: Rec.709 Gamma 2.4.\n"
        "4. Subtítulos/textos: File → Import → Subtitle… → elige el .srt y arrástralo a la línea de tiempo.\n"
        "5. Las transiciones, zooms, textos animados y la mezcla final están en el MP4 del motor; aquí llegan\n"
        "   los cortes y la música para que retoques lo que quieras. Exporta en Deliver con 1080×1920, H.264.\n",
        encoding="utf-8")
    return ruta
