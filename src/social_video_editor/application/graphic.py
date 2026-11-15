"""Use case: animated graphics — the theme a project draws with, scenes for the renderer, quick
previews of one graphic and the style proof (every template on one sheet).

    sve graphic --proof [--pack NAME]              every template, vertical and landscape
    sve graphic <project> --shot 2                 graphic shot 2 of the plan
    sve graphic [<project>] --cue 'bars data="A:15, B:40" unit="%"'

A preview is a contact sheet of a few instants plus the settled frame at full size: enough to judge
a graphic in seconds, without rendering the video.
"""
from __future__ import annotations

from pathlib import Path

from ..domain.graphics import TEMPLATES, build_theme, make_scene, parse_cue
from ..domain.graphics.scene import Scene
from ..domain.models import UserError
from ..log import log
from .services import Services

VERTICAL, LANDSCAPE = (1080, 1920), (1920, 1080)

SAMPLES = {
    "es": [
        {"template": "title", "text": "¿Cómo viaja un mensaje?", "kicker": "Capítulo 1", "icon": "phone"},
        {"template": "bars", "title": "Tiempo para abrir una app", "data": "Desde cero:100, En memoria:20", "unit": "%",
         "prefix": "≈", "source": "Fuente: dato de ejemplo"},
        {"template": "number", "value": "240", "unit": "ms", "label": "Un mensaje tarda", "sub": "en dar la vuelta al mundo",
         "icon": "phone", "source": "Fuente: dato de ejemplo"},
        {"template": "product_card", "name": "Disco de ejemplo Pro", "image": "@sample",
         "specs": "Capacidad:2 TB; Lectura:14.900 MB/s", "source": "Ficha del fabricante"},
        {"template": "headline", "text": "Los precios de la memoria suben por tercer trimestre seguido",
         "outlet": "Noticias de hardware", "date": "12 de marzo de 2026", "highlight": "tercer trimestre seguido"},
        {"template": "list", "title": "Para cuidar la batería", "items": "Baja el brillo; Activa el modo ahorro; No la dejes llegar a cero",
         "marks": "+,+,-"},
        {"template": "diagram", "title": "Cómo viaja un mensaje", "nodes": "Celular, Router, Internet",
         "edges": "Celular>Router:envía; Router>Internet:reenvía; Internet>Router:responde", "dur": 5},
        {"template": "lower_third", "kicker": "Video", "text": "Canal oficial del fabricante"},
        {"template": "callouts", "items": "Celular; Router; Internet; 240 ms", "times": "0.2, 0.8, 1.4, 2.0"},
        {"template": "bullets", "items": "Sale de tu celular; Cruza el océano; Vuelve en un parpadeo", "times": "0.2, 1.0, 1.8"},
        {"template": "pip_frame", "rect": "0.48, 0.52, 0.36, 0.25"},
        {"template": "scene", "action": "swipe", "subject": "app", "text": "Al cerrarla, empieza de cero"},
        {"template": "scene", "action": "drain", "subject": "app", "text": "Gasta batería sin usarla"},
        {"template": "line_chart", "title": "Teléfonos vendidos", "data": "2015:12, 2018:30, 2021:44, 2024:75", "unit": "M",
         "source": "Fuente: dato de ejemplo", "caption": "Se multiplicó por seis."},
        {"template": "versus", "title": "¿Cuál dura más?", "left": "Celular", "right": "Portátil",
         "rows": "Batería (h):20|9; Peso (kg):0.2*|1.5; Pantalla:6|15", "source": "Fuente: dato de ejemplo",
         "caption": "El celular gana en casi todo."},
        {"template": "code", "title": "Instálalo en una línea", "lines": "brew install ffmpeg; pip install social-video-editor; sve doctor",
         "output": "FFmpeg 7.1 ok; Fuentes ok; Gráficos ok", "highlight": 3,
         "caption": "Y revisa que todo esté listo."},
        {"template": "screen", "title": "Apaga esto", "image": "@screen", "focus_x": 0.5, "focus_y": 0.62,
         "url": "ajustes.ejemplo.com", "label": "Actualizar en segundo plano", "source": "Captura de ejemplo",
         "caption": "Esta opción gasta batería."},
        {"template": "diagram", "title": "Adónde va una foto", "nodes": "Celular, Caché, Servidor, Nube",
         "edges": "Celular>Caché:guarda; Caché>Servidor:si es nueva; Caché>Nube:copia; Servidor>Celular:listo", "dur": 6,
         "caption": "Y el celular se entera al final."},
    ],
    "en": [
        {"template": "title", "text": "How does a message travel?", "kicker": "Chapter 1", "icon": "phone"},
        {"template": "bars", "title": "Time to open an app", "data": "Cold start:100, In memory:20", "unit": "%",
         "prefix": "≈", "source": "Source: sample data"},
        {"template": "number", "value": "240", "unit": "ms", "label": "A message takes", "sub": "to go around the world",
         "icon": "phone", "source": "Source: sample data"},
        {"template": "product_card", "name": "Sample Drive Pro", "image": "@sample",
         "specs": "Capacity:2 TB; Read:14,900 MB/s", "source": "Maker's data sheet"},
        {"template": "headline", "text": "Memory prices rise for the third quarter in a row",
         "outlet": "Hardware news", "date": "March 12, 2026", "highlight": "third quarter in a row"},
        {"template": "list", "title": "To look after the battery", "items": "Lower the brightness; Turn on power saving; Do not let it reach zero",
         "marks": "+,+,-"},
        {"template": "diagram", "title": "How a message travels", "nodes": "Phone, Router, Internet",
         "edges": "Phone>Router:sends; Router>Internet:forwards; Internet>Router:answers", "dur": 5},
        {"template": "lower_third", "kicker": "Video", "text": "The maker's official channel"},
        {"template": "callouts", "items": "Phone; Router; Internet; 240 ms", "times": "0.2, 0.8, 1.4, 2.0"},
        {"template": "bullets", "items": "It leaves your phone; It crosses the ocean; It is back in a blink", "times": "0.2, 1.0, 1.8"},
        {"template": "pip_frame", "rect": "0.48, 0.52, 0.36, 0.25"},
        {"template": "scene", "action": "swipe", "subject": "app", "text": "Close it and it starts from zero"},
        {"template": "scene", "action": "drain", "subject": "app", "text": "It drains the battery doing nothing"},
        {"template": "line_chart", "title": "Phones sold", "data": "2015:12, 2018:30, 2021:44, 2024:75", "unit": "M",
         "source": "Source: sample data", "caption": "It grew six times over."},
        {"template": "versus", "title": "Which lasts longer?", "left": "Phone", "right": "Laptop",
         "rows": "Battery (h):20|9; Weight (kg):0.2*|1.5; Screen:6|15", "source": "Source: sample data",
         "caption": "The phone wins almost everything."},
        {"template": "code", "title": "Install it in one line", "lines": "brew install ffmpeg; pip install social-video-editor; sve doctor",
         "output": "FFmpeg 7.1 ok; Fonts ok; Graphics ok", "highlight": 3,
         "caption": "And check that everything is ready."},
        {"template": "screen", "title": "Turn this off", "image": "@screen", "focus_x": 0.5, "focus_y": 0.62,
         "url": "settings.example.com", "label": "Background refresh", "source": "Sample screenshot",
         "caption": "This one drains the battery."},
        {"template": "diagram", "title": "Where a photo goes", "nodes": "Phone, Cache, Server, Cloud",
         "edges": "Phone>Cache:saves; Cache>Server:if new; Cache>Cloud:backup; Server>Phone:done", "dur": 6,
         "caption": "And the phone hears back at the end."},
    ],
}

# what the narrator would be saying over each sample (the proof shows graphic and subtitle together)
CAPTIONS = {"es": ["Todo empieza en tu celular.", "Abre cinco veces más rápido.", "Menos que un parpadeo.", "Este es el disco.",
                   "Salió en las noticias.", "Tres cosas sencillas.", "Y vuelve por el mismo camino."],
            "en": ["It all starts on your phone.", "It opens five times faster.", "Less than a blink.", "This is the drive.",
                   "It was in the news.", "Three simple things.", "And it comes back the same way."]}


def require_graphics(svc: Services) -> None:
    why = svc.graphics.ready() if svc.graphics is not None else "no graphic renderer"
    if why:
        raise UserError(f"Graphics are not available: {why}")


def graphics_theme(svc: Services, cfg: dict) -> dict:
    """The theme of a project: default ← pack (graphics.pack) ← graphics.theme."""
    g = cfg.get("graphics") or {}
    pack = svc.packs.load(str(g["pack"]))["theme"] if g.get("pack") and svc.packs is not None else None
    return build_theme(pack, g.get("theme"))


def build_scene(svc: Services, cfg: dict, spec: dict, W: int, H: int, theme: dict | None = None) -> Scene:
    require_graphics(svc)
    stage = svc.graphics.stage(W, H, theme or graphics_theme(svc, cfg), cfg.get("language", "es"))
    return make_scene(spec, stage)


def preview_times(scene: Scene) -> list[float]:
    """A few instants that show how a graphic comes in, then the settled picture."""
    end = scene.exit_at - 0.05 if scene.exit_at is not None else scene.dur - 1 / 30
    return [0.0, min(0.25, scene.dur), min(scene.settle * 0.6, scene.dur), max(end, 0.0)]


def preview(svc: Services, cfg: dict, spec: dict, out_dir: Path, sizes=(VERTICAL, LANDSCAPE), name: str = "graphic") -> list[Path]:
    """Contact sheet + full-size settled frame of one graphic, per frame size."""
    theme = graphics_theme(svc, cfg)
    out = []
    for W, H in sizes:
        scene = build_scene(svc, cfg, spec, W, H, theme)
        tag = "vertical" if H > W else "landscape"
        pictures = [svc.graphics.still(scene, t) for t in preview_times(scene)]
        out.append(svc.graphics.save_sheet(pictures, Path(out_dir) / f"{name}_{tag}_sheet.png", columns=4,
                                           tile_width=270 if H > W else 480))
        out.append(svc.graphics.save_picture(pictures[-1], Path(out_dir) / f"{name}_{tag}.png"))
    return out


def proof(svc: Services, cfg: dict, out_dir: Path, video: bool = False) -> list[Path]:
    """The style proof: every template with sample content, vertical and landscape. One sheet per
    orientation (three instants per template), each settled frame at full size, and with `video` a
    clip of all of them in a row to judge the motion."""
    require_graphics(svc)
    out_dir = Path(out_dir)
    theme = graphics_theme(svc, cfg)
    sample = svc.graphics.sample_image(out_dir / "_sample_product.png")
    language = cfg.get("language", "es")
    screenshot = sample_screenshot(svc, out_dir / "_sample_screen.png", language)
    said = CAPTIONS.get(language, CAPTIONS["en"])
    files = {"@sample": str(sample), "@screen": str(screenshot)}
    specs = [{**s, **({"image": files[s["image"]]} if s.get("image") in files else {}),
              **({"caption": said[i % len(said)]} if not getattr(TEMPLATES.get(s["template"]), "overlay", False)
                 and "caption" not in s else {})}
             for i, s in enumerate(SAMPLES.get(language, SAMPLES["en"]))]
    fps = int(cfg["output"]["fps"])
    out = []
    for W, H in (VERTICAL, LANDSCAPE):
        tag = "vertical" if H > W else "landscape"
        scenes = [build_scene(svc, cfg, s, W, H, theme) for s in specs]
        pictures = []
        for scene in scenes:
            t0, t1, t2, t3 = preview_times(scene)
            shots = [svc.graphics.still(scene, t) for t in (t1, t2, t3)]
            pictures += shots
            svc.graphics.save_picture(shots[-1], out_dir / "stills" / f"{scene.name}_{tag}.png")
        out.append(svc.graphics.save_sheet(pictures, out_dir / f"proof_{tag}.png", columns=6 if H > W else 3,
                                           tile_width=270 if H > W else 420))
        log(f"Style proof ({tag}): {out[-1]}")
        if video:
            def frames():
                for scene in scenes:
                    yield from svc.graphics.frames(scene, fps, int(round(scene.dur * fps)))
            n = sum(int(round(scene.dur * fps)) for scene in scenes)
            out.append(svc.encoder.encode_frames(frames(), n, W, H, fps, out_dir / f"proof_{tag}.mov", crf=18))
            log(f"Style proof clip ({tag}): {out[-1]}")
    return out


def sample_screenshot(svc: Services, out: Path, language: str = "en") -> Path:
    """A made-up settings page (drawn with the engine's own list graphic, default look) to stand in
    for a screenshot in the style proof."""
    items = {"es": "Ahorro de batería; Modo oscuro; Actualizar en segundo plano",
             "en": "Battery saver; Dark mode; Background refresh"}
    stage = svc.graphics.stage(1600, 1000, build_theme(), language)
    page = make_scene({"template": "list", "title": "Ajustes" if language == "es" else "Settings",
                       "items": items.get(language, items["en"])}, stage)
    return svc.graphics.save_picture(svc.graphics.still(page, page.dur - 0.05), out)


def cue_spec(text: str) -> dict:
    return parse_cue(text)


def plan_graphic(plan: dict | None, number: int) -> dict:
    """Graphic shot `number` (1-based, counting only graphic shots) of a plan."""
    shots = [s for s in (plan or {}).get("shots", []) if s.get("type") == "graphic"]
    if not 1 <= number <= len(shots):
        raise UserError(f"The plan has {len(shots)} graphic shots; --shot {number} is not one of them")
    return {k: v for k, v in shots[number - 1].items() if k not in ("type", "transition", "role") and not k.startswith("_")}
