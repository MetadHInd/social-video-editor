"""Command line (sve / social-video-editor / python -m social_video_editor).

  sve new <name> --recipe unboxing --sources a.mov b.mov
  sve analyze <project> [--force]
  sve thumbs  <project> [--source 0 --start 60 --end 90 --every 1]
  sve plan    <project> [--force]
  sve render  <project> [--draft] [--profile youtube|landscape|horizontal] [--no-music]
  sve build   <project> [--draft]          (analyze + thumbs + plan if missing + render)
  sve view    <project>                    (plan and timeline summary)
  sve report  <project> [--json]           (one-screen project report; reads only files on disk)
  sve recipes
  sve music "artist song"                  (downloads to music/ with yt-dlp and analyzes the beat)
  sve check   <project> [--version v3] [--out DIR]   (automatic review → vN/check.json + listen/)
  sve products <project>                   (products recognized by the vision model → cache/products.json)
  sve enhance <project> [--force]          (photo sources: upscale small ones / reduce big ones → cache/photos/)

Spanish aliases are accepted: nuevo, analizar, vistazo, planear, montar, ver, informe, recetas, musica, verificar,
productos, mejorar and --borrador, --perfil, --sin-musica, --forzar, --receta, --fuentes, --fuente, --desde, --hasta, --cada.
"""
from __future__ import annotations

import argparse
import json

from .adapters.files.legacy_es import profile_name
from .adapters.system.console import configure_console
from .adapters.system.tools import configure_numba_cache
from .application.analyze import prepare_sources
from .application.music import download_music
from .application.plan import make_plan
from .application.products import find_products, product_lines
from .application.project import new_project, recipe_lines
from .application.render import render
from .application.report import build_report, report_json, report_lines
from .application.thumbs import contact_sheets
from .application.view import load_plan, view_lines
from .container import build_services
from .domain.config import EXPORT_PROFILES, PROFILE_ALIASES, canonical_profile
from .domain.music import tempo_lines
from .log import log

PROFILE_CHOICES = [*EXPORT_PROFILES, *PROFILE_ALIASES, "cuadrado"]


def _project(svc, name: str):
    return svc.projects.load(svc.projects.resolve(name))


def cmd_new(svc, a):
    log(f"Project created: {new_project(svc, a.name, a.recipe, a.sources)}")


def cmd_analyze(svc, a):
    prepare_sources(svc, _project(svc, a.project), force=a.force)


def cmd_thumbs(svc, a):
    for path in contact_sheets(svc, _project(svc, a.project), a.source, a.start, a.end, a.every):
        print(path)


def cmd_plan(svc, a):
    make_plan(svc, _project(svc, a.project), force=a.force)


def cmd_render(svc, a):
    p = _project(svc, a.project)
    render(svc, p, load_plan(svc, p), draft=a.draft, profile=canonical_profile(profile_name(a.profile)), no_music=a.no_music)


def cmd_build(svc, a):
    a.force = False
    a.source = a.start = a.end = a.every = None
    cmd_thumbs(svc, a)
    cmd_plan(svc, a)
    cmd_render(svc, a)


def cmd_view(svc, a):
    print("\n".join(view_lines(svc, _project(svc, a.project))))


def cmd_report(svc, a):
    r = build_report(svc, _project(svc, a.project))
    print(json.dumps(report_json(r), ensure_ascii=False) if a.json else "\n".join(report_lines(r)))


def cmd_recipes(svc, a):
    print("\n".join(recipe_lines(svc)))


def cmd_music(svc, a):
    path, am = download_music(svc, a.query)
    print(f"Downloaded: {path.name} — {am['duration']:.0f} s, {am['tempo']} BPM, drop at {am['drop']:.1f} s")
    print("\n".join(tempo_lines(am)))
    print(f"To use it: in project.yaml → settings → music → file: \"{path.parent.name}/{path.name}\"")


def cmd_check(svc, a):
    from .application.check import check_version
    from .domain.check import report_lines

    result, notes = check_version(svc, a.project, a.version, a.out)
    print("\n".join(report_lines(result, notes)))


def cmd_products(svc, a):
    print("\n".join(product_lines(find_products(svc, _project(svc, a.project)))))


def cmd_enhance(svc, a):
    from .application.enhance import enhance_photos

    enhance_photos(svc, _project(svc, a.project), force=a.force)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sve", description="Vertical video editing engine for social media")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("new", aliases=["nuevo"], help="create a project")
    s.add_argument("name")
    s.add_argument("--recipe", "--receta", default="base")
    s.add_argument("--sources", "--fuentes", nargs="+", required=True)
    s.set_defaults(f=cmd_new)
    for name, alias, fn, help_ in (("analyze", "analizar", cmd_analyze, "analyze the sources"),
                                   ("plan", "planear", cmd_plan, "propose plan.json")):
        s = sub.add_parser(name, aliases=[alias], help=help_)
        s.add_argument("project")
        s.add_argument("--force", "--forzar", action="store_true")
        s.set_defaults(f=fn)
    s = sub.add_parser("thumbs", aliases=["vistazo"], help="contact sheets + transcript")
    s.add_argument("project")
    s.add_argument("--source", "--fuente", type=int)
    s.add_argument("--start", "--desde", type=float)
    s.add_argument("--end", "--hasta", type=float)
    s.add_argument("--every", "--cada", type=float)
    s.set_defaults(f=cmd_thumbs)
    for name, alias, fn, help_ in (("render", None, cmd_render, "render the video"),
                                   ("build", "montar", cmd_build, "thumbs + plan if missing + render")):
        s = sub.add_parser(name, aliases=[alias] if alias else [], help=help_)
        s.add_argument("project")
        s.add_argument("--draft", "--borrador", action="store_true")
        s.add_argument("--profile", "--perfil", choices=PROFILE_CHOICES)
        s.add_argument("--no-music", "--sin-musica", action="store_true", dest="no_music")
        s.set_defaults(f=fn)
    s = sub.add_parser("view", aliases=["ver"], help="plan and timeline summary")
    s.add_argument("project")
    s.set_defaults(f=cmd_view)
    s = sub.add_parser("report", aliases=["informe"], help="one-screen project report (for people and AI agents)")
    s.add_argument("project")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(f=cmd_report)
    sub.add_parser("recipes", aliases=["recetas"], help="list recipes").set_defaults(f=cmd_recipes)
    s = sub.add_parser("music", aliases=["musica"], help="download a song with yt-dlp")
    s.add_argument("query")
    s.set_defaults(f=cmd_music)
    s = sub.add_parser("check", aliases=["verificar"], help="automatic review of a render (check.json + listen/)")
    s.add_argument("project")
    s.add_argument("--version", dest="version", help="version folder (default: latest vN)")
    s.add_argument("--out", "--salida", dest="out", help="write check.json and snippets here instead")
    s.set_defaults(f=cmd_check)
    s = sub.add_parser("products", aliases=["productos"], help="products recognized by the vision model")
    s.add_argument("project")
    s.set_defaults(f=cmd_products)
    s = sub.add_parser("enhance", aliases=["mejorar"], help="upscale small photos / reduce big ones (cache/photos/)")
    s.add_argument("project")
    s.add_argument("--force", "--forzar", action="store_true", help="redo photos already enhanced")
    s.set_defaults(f=cmd_enhance)
    return ap


def main(argv=None):
    # before anything prints or librosa/numba load: safe console encoding (Windows code pages) and a
    # writable numba cache
    configure_console()
    configure_numba_cache()
    a = parser().parse_args(argv)
    a.f(build_services(), a)
