"""Línea de comandos del motor.

  motor nuevo <nombre> --receta unboxing --fuentes a.mov b.mov
  motor analizar <proyecto> [--forzar]
  motor vistazo  <proyecto> [--fuente 0 --desde 60 --hasta 90 --cada 1]
  motor planear  <proyecto> [--forzar]
  motor render   <proyecto> [--borrador] [--perfil youtube]
  motor montar   <proyecto> [--borrador]       (analizar + vistazo + planear si falta + render)
  motor davinci  <proyecto> [--version v3SinMusica]
  motor ver      <proyecto>                    (resumen del plan y la línea de tiempo)
  motor recetas
  motor musica "artista canción"               (descarga a musica/ con yt-dlp y analiza el ritmo)
  motor render <proyecto> --sin-musica         (mismos cortes al ritmo, sin mezclar la canción)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import yaml

from .config import Proyecto, recetas_disponibles
from .util import espacio, guardar_json, leer_json, log, mmss


def _resolver(nombre: str) -> Path:
    p = Path(nombre)
    if not p.exists() and (espacio() / "proyectos" / nombre).exists():
        p = espacio() / "proyectos" / nombre
    return p


def cmd_nuevo(a):
    d = espacio() / "proyectos" / a.nombre
    d.mkdir(parents=True, exist_ok=True)
    datos = {"nombre": a.nombre, "receta": a.receta,
             "fuentes": [{"ruta": str(Path(f).expanduser().resolve())} for f in a.fuentes], "ajustes": {}}
    (d / "proyecto.yaml").write_text(yaml.safe_dump(datos, allow_unicode=True, sort_keys=False), encoding="utf-8")
    log(f"Proyecto creado: {d}/proyecto.yaml")


def _analizar(p: Proyecto, forzar=False):
    from .analisis import analizar
    from .proxy import preparar
    infos = [preparar(f, p.cache, p.cfg) for f in p.fuentes]
    return infos, [analizar(i, p.cache, p.cfg, forzar=forzar) for i in infos]


def cmd_analizar(a):
    p = Proyecto(_resolver(a.proyecto))
    _analizar(p, a.forzar)


def cmd_vistazo(a):
    from . import vistazo
    p = Proyecto(_resolver(a.proyecto))
    infos, ans = _analizar(p)
    sel = range(len(infos)) if a.fuente is None else [a.fuente]
    for k in sel:
        for r in vistazo.hojas(infos[k], ans[k], p.cache / "vistazo", a.desde or 0, a.hasta, a.cada):
            print(r)
        print(vistazo.texto(infos[k], ans[k], p.cache / "vistazo"))


def cmd_planear(a):
    from .momentos import armar_plan
    p = Proyecto(_resolver(a.proyecto))
    if p.plan_path.exists() and not a.forzar:
        log(f"Ya existe {p.plan_path} (usa --forzar para rehacerlo; se guarda copia).")
        return
    if p.plan_path.exists():
        shutil.copy(p.plan_path, p.dir / "plan_anterior.json")
    infos, ans = _analizar(p)
    guardar_json(p.plan_path, armar_plan(p.fuentes, ans, p.cfg))
    log(f"Plan guardado en {p.plan_path}")


def cmd_render(a):
    from .render import renderizar
    p = Proyecto(_resolver(a.proyecto))
    if not p.plan_path.exists():
        raise SystemExit("No hay plan.json; ejecuta primero: motor planear <proyecto>")
    renderizar(p, leer_json(p.plan_path), borrador=a.borrador, perfil=a.perfil,
               sin_musica=getattr(a, "sin_musica", False))


def cmd_montar(a):
    a.forzar = False
    a.fuente, a.desde, a.hasta, a.cada = None, None, None, None
    cmd_vistazo(a)
    cmd_planear(a)
    cmd_render(a)


def cmd_davinci(a):
    from .davinci import exportar
    p = Proyecto(_resolver(a.proyecto))
    destino = (p.dir / a.version) if getattr(a, "version", None) else (p.ultima_version(incluir_variantes=True) or p.dir)
    if not destino.exists():
        raise SystemExit(f"No existe la carpeta {destino}")
    plan = leer_json(p.plan_path)
    if (destino / "plan_usado.json").exists():        # la edición tal como se renderizó (cortes al ritmo)
        plan = leer_json(destino / "plan_usado.json")
        p.cfg = leer_json(destino / "config_usada.json")
    print(exportar(p, plan, destino / "davinci"))


def cmd_ver(a):
    from .plan import duracion_total, linea_de_tiempo, textos_absolutos
    p = Proyecto(_resolver(a.proyecto))
    import copy

    from .momentos import resolver_rangos
    from .render import _preparar_musica
    plan = leer_json(p.plan_path)
    infos, ans = _analizar(p)
    resolver_rangos(plan, ans, p.cfg)
    _preparar_musica(p, plan, copy.deepcopy(p.cfg), infos, False)   # misma alineación al ritmo que el render
    tl = linea_de_tiempo(plan, p.cfg)
    print(f"{p.nombre} — receta '{p.receta}' — {len(tl)} tomas — {duracion_total(tl):.1f} s")
    for tm in tl:
        fx = ",".join(e["tipo"] for e in tm.get("efectos", []))
        if tm.get("tipo"):
            print(f" {tm['i']:>2} [{mmss(tm['out_ini'])}–{mmss(tm['out_fin'])}] {tm['tipo']:<28} "
                  f"{tm.get('transicion', ''):<12} {tm.get('rol', '')}")
            continue
        print(f" {tm['i']:>2} [{mmss(tm['out_ini'])}–{mmss(tm['out_fin'])}] fuente {tm['fuente']} "
              f"{mmss(tm['ini'])}→{mmss(tm['fin'])} x{tm.get('velocidad', 1)} {tm.get('transicion', ''):<12} "
              f"{tm.get('rol', ''):<10} {fx}")
    for tx in textos_absolutos(plan, tl):
        print(f"   texto [{mmss(tx['ini'])}–{mmss(tx['fin'])}] ({tx.get('estilo', 'simple')}) {tx['texto']}")


def cmd_musica(a):
    """Descarga una canción con yt-dlp (primer resultado de YouTube) y muestra su ritmo."""
    import subprocess

    from .audio import analizar_musica
    destino = espacio() / "musica"
    destino.mkdir(exist_ok=True)
    busqueda = a.busqueda if a.busqueda.startswith("http") else f"ytsearch1:{a.busqueda}"
    r = subprocess.run(["yt-dlp", busqueda, "-x", "--audio-format", "mp3", "--audio-quality", "0",
                        "--no-playlist", "--print", "after_move:filepath", "-o", str(destino / "%(title)s.%(ext)s")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(r.stderr[-800:])
    ruta = Path(r.stdout.strip().splitlines()[-1])
    am = analizar_musica(ruta, destino)
    print(f"Descargada: {ruta.name} — {am['duracion']:.0f} s, {am['tempo']} BPM, drop en {am['drop']:.1f} s")
    print(f"Para usarla: en proyecto.yaml → ajustes → musica → archivo: \"musica/{ruta.name}\"")


def cmd_recetas(a):
    for nombre, r in recetas_disponibles().items():
        primera = r.read_text(encoding="utf-8").splitlines()[0]
        print(f"{nombre:<15} {primera}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="motor", description="Motor de edición de video vertical para redes sociales")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("nuevo"); s.add_argument("nombre"); s.add_argument("--receta", default="base")
    s.add_argument("--fuentes", nargs="+", required=True); s.set_defaults(f=cmd_nuevo)
    for nombre, fn in (("analizar", cmd_analizar), ("planear", cmd_planear)):
        s = sub.add_parser(nombre); s.add_argument("proyecto"); s.add_argument("--forzar", action="store_true")
        s.set_defaults(f=fn)
    s = sub.add_parser("vistazo"); s.add_argument("proyecto"); s.add_argument("--fuente", type=int)
    s.add_argument("--desde", type=float); s.add_argument("--hasta", type=float); s.add_argument("--cada", type=float)
    s.set_defaults(f=cmd_vistazo)
    for nombre, fn in (("render", cmd_render), ("montar", cmd_montar)):
        s = sub.add_parser(nombre); s.add_argument("proyecto"); s.add_argument("--borrador", action="store_true")
        s.add_argument("--perfil", choices=["tiktok", "reels", "shorts", "youtube", "cuadrado"])
        s.add_argument("--sin-musica", action="store_true", dest="sin_musica")
        s.set_defaults(f=fn)
    s = sub.add_parser("davinci"); s.add_argument("proyecto")
    s.add_argument("--version", help="carpeta a exportar, ej. v3 o v3SinMusica (por defecto la más reciente)")
    s.set_defaults(f=cmd_davinci)
    s = sub.add_parser("ver"); s.add_argument("proyecto"); s.set_defaults(f=cmd_ver)
    sub.add_parser("recetas").set_defaults(f=cmd_recetas)
    s = sub.add_parser("musica"); s.add_argument("busqueda"); s.set_defaults(f=cmd_musica)
    a = ap.parse_args(argv)
    a.f(a)
