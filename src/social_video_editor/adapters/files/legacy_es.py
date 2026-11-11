"""Backward compatibility with Spanish files (the only place that knows the old names).

Projects created by earlier versions use Spanish keys and values: proyecto.yaml, plan.json with
"tomas"/"textos", recipes in recetas/ with "ritmo", "seleccion"…, cache files with "muestras",
"palabras"… Everything is translated to English WHEN LOADED, so the domain only sees English.
English input passes through unchanged (keys/values not in the tables are kept as they are).

Each file type is described by a Ctx (context) tree:
  keys      Spanish key → English key (for the dict at this level)
  values    English key → {Spanish value → English value} (scalars or lists of scalars)
  children  English key → Ctx applied to the nested dict (or to every dict of a nested list)
  entry     for "map" dicts whose keys are names (e.g. text styles): Ctx of every entry value;
            entry_names translates those names
  removed   keys (Spanish or English) that are no longer used and are dropped on load

The full table is documented in docs/LEGACY_KEYS.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ctx:
    keys: dict[str, str] = field(default_factory=dict)
    values: dict[str, dict] = field(default_factory=dict)
    children: dict[str, Ctx] = field(default_factory=dict)
    entry: Ctx | None = None
    entry_names: dict[str, str] = field(default_factory=dict)
    removed: frozenset = frozenset()


def translate(data, ctx: Ctx):
    """Translates a loaded YAML/JSON value with the given context (returns a new value)."""
    if isinstance(data, list):
        return [translate(x, ctx) for x in data]
    if not isinstance(data, dict):
        return data
    if ctx.entry is not None:
        return {ctx.entry_names.get(k, k): translate(v, ctx.entry) for k, v in data.items()}
    out = {}
    for k, v in data.items():
        ek = ctx.keys.get(k, k)
        if k in ctx.removed or ek in ctx.removed:
            continue
        if ek in ctx.values:
            v = _value(v, ctx.values[ek])
        if ek in ctx.children:
            v = translate(v, ctx.children[ek])
        out[ek] = v
    return out


def _value(v, table: dict):
    if isinstance(v, list):
        return [_value(x, table) for x in v]
    return table.get(v, v) if isinstance(v, str) else v


# ── enum values ──────────────────────────────────────────────────────────────

TRANSITIONS = {"corte": "cut", "fundido": "fade", "negro": "fade_black", "slide_arriba": "slide_up",
               "desliz_izq": "slide_left", "circulo": "circle", "difuminar": "blur", "cortina": "curtain"}
# unchanged: flash, zoom, whip, pixel, radial
EFFECT_TYPES = {"zoom_golpe": "zoom_punch", "empuje": "push_in", "alejar": "pull_out", "sacudida": "shake",
                "congelar": "freeze", "pulso_ritmo": "beat_pulse"}
# unchanged: flash
SHOT_TYPES: dict[str, str] = {"grafico": "graphic"}  # polaroid, collage: same in both languages
# graphic shots / cues (docs/GRAPHICS.md): template names and their keys
GRAPHIC_TEMPLATES = {"barras": "bars", "cifra": "number", "ficha": "product_card", "cartel": "title",
                     "titular": "headline", "lista": "list", "rotulo": "lower_third", "diagrama": "diagram"}
GRAPHIC_KEYS = {"plantilla": "template", "titulo": "title", "datos": "data", "unidad": "unit", "prefijo": "prefix",
                "resaltar": "highlight", "maximo": "max", "valor": "value", "etiqueta": "label", "icono": "icon",
                "debajo": "sub", "desde": "from", "contar": "count", "nombre": "name", "imagen": "image",
                "caracteristicas": "specs", "precio": "price", "antetitulo": "kicker", "medio": "outlet",
                "fecha": "date", "elementos": "items", "marcas": "marks", "nodos": "nodes", "iconos": "icons",
                "flechas": "edges", "subtitulo": "caption"}
ROLES = {"gancho": "hook", "historia": "story", "revelacion": "reveal", "cierre": "outro",
         "apertura": "opening", "producto": "product", "estrella": "star"}
# unchanged: polaroid, collage; any other role (free text) is kept
CRITERIA = {"producto": "product", "accion": "action"}
TEXT_STYLES = {"gancho": "hook", "etiqueta": "label", "tarjeta": "card", "contador": "counter", "precio": "price"}
# unchanged: cta, simple
ANIMATIONS = {"escribir": "typewriter", "ninguna": "none"}
# unchanged: pop
HOOK_STRATEGIES = {"inicio": "start"}                # unchanged: teaser
FRAMING_MODES = {"recortar": "crop", "desenfoque": "blur"}   # unchanged: auto
MUSIC_ALIGN = {"exacto": "exact"}
MUSIC_FILE = {"ninguna": "none", "no": "none"}       # music.file: none = no music
SFX_NAMES = {"tecla": "keystroke", "obturador": "shutter"}   # unchanged: whoosh, pop, flash
RECIPE_NAMES = {"resena": "review", "antes_despues": "before_after"}
# unchanged: base, unboxing, tutorial, vlog, storytime
PROFILES = {"cuadrado": "square"}                    # unchanged: tiktok, reels, shorts, youtube

# ── configuration (recipes and project "settings") ───────────────────────────

COLOR = Ctx(keys={"hdr_blanco": "hdr_white", "niveles": "levels", "balance_blancos": "white_balance",
                  "luma_objetivo": "target_luma", "contraste": "contrast", "saturacion": "saturation",
                  "temperatura": "temperature", "nitidez": "sharpen", "vineta": "vignette",
                  "calidez": "warmth", "desvanecido": "fade", "tinte": "tint",
                  "referencia": "reference", "fuerza_referencia": "reference_strength"})
# unchanged: auto, gamma_min, gamma_max, vibrance, lut

TEXT_STYLE_KEYS = {"tam": "size", "fuente": "font", "fondo": "background", "mayusculas": "uppercase",
                   "max_ancho": "max_width", "radio": "radius", "contorno": "outline",
                   "animacion": "animation", "velocidad": "speed", "desc_fuente": "desc_font",
                   "desc_tam": "desc_size"}
# unchanged: color, x, y, desc_color
TEXT_STYLE = Ctx(keys=TEXT_STYLE_KEYS, values={"animation": ANIMATIONS})

VISION = Ctx(
    keys={"activado": "enabled", "modelo": "model", "modelo_texto": "text_model", "dispositivo": "device",
          "positivos": "positive", "negativos": "negative", "pesos": "weights", "puntaje_min": "min_score", "puntaje_extender": "extend_score",
          "continuidad": "continuity", "duracion_min": "min_duration", "duracion_max": "max_duration",
          "mismo_producto": "same_product", "ventana": "window", "max_productos": "max_products",
          "estrellas": "stars", "vocabulario": "vocabulary", "plan_productos": "product_plan",
          "busqueda_producto": "product_search", "tarjetas": "cards", "contadores": "counters",
          "efecto_producto": "product_effect", "apertura_max": "opening_max",
          "sin_empaque": "unpacked", "empaques": "packaging"},
    # unchanged: fps
    children={"weights": Ctx(keys={"indicaciones": "prompts", "objeto": "objectness", "novedad": "novelty",
                                   "nitidez": "sharpness"})},
)

PHOTO = Ctx(   # new section: still photos and slideshows
    keys={"duracion": "duration", "transicion": "transition", "cantidad_movimiento": "motion_amount",
          "ajustar_a_musica": "fit_to_music", "duracion_min": "min_duration", "duracion_max": "max_duration",
          "tono_auto": "auto_tone"},
    values={"transition": TRANSITIONS},
)

CONFIG = Ctx(
    keys={"idioma": "language", "salida": "output", "duracion": "duration", "ritmo": "pacing",
          "gancho": "hook", "seleccion": "selection", "analisis": "analysis",
          "transcripcion": "transcription", "encuadre": "framing", "transiciones": "transitions",
          "efectos": "effects", "musica": "music", "subtitulos": "subtitles", "textos": "texts",
          "exportar": "export", "verificacion": "check", "visión": "vision", "foto": "photo",
          "presentacion": "slideshow", "llamada": "cta", "charla": "talk", "pausas": "pauses",
          "graficos": "graphics"},
    removed=frozenset({"cierre"}),
    children={
        "output": Ctx(keys={"ancho": "width", "alto": "height"}, removed=frozenset({"perfiles"})),
        "cta": Ctx(keys={"texto": "text", "duracion": "duration"}),
        "pauses": Ctx(keys={"pausa_minima": "min_pause", "pausa_dejada": "keep_pause",
                            "silencio_db": "silence_db", "tramo_minimo": "min_span",
                            "activo": "enabled", "transicion": "transition"},
                      values={"transition": TRANSITIONS}),
        "duration": Ctx(keys={"objetivo": "target", "maximo": "max"}, removed=frozenset({"minimo"})),
        "pacing": Ctx(keys={"toma_min": "shot_min", "toma_max": "shot_max", "frase_max": "phrase_max",
                            "separacion_min": "min_gap", "acelerar_sin_voz": "speed_without_voice"}),
        "hook": Ctx(keys={"estrategia": "strategy", "duracion": "duration", "texto": "text",
                          "transicion": "transition", "duracion_texto": "text_duration"},
                    values={"strategy": HOOK_STRATEGIES, "transition": TRANSITIONS}),
        "selection": Ctx(
            keys={"pesos": "weights", "palabras_clave": "keywords", "luz_minima": "min_brightness",
                  "borroso_percentil": "blur_percentile", "usar_todas_las_fuentes": "use_all_sources",
                  "cobertura": "coverage"},
            children={"weights": Ctx(keys={"movimiento": "motion", "picos": "peaks", "voz": "voice",
                                           "palabras_clave": "keywords", "nitidez": "sharpness"})}),
        "analysis": Ctx(keys={"muestras_por_seg": "samples_per_sec", "umbral_escena": "scene_threshold"}),
        "transcription": Ctx(keys={"modelo": "model", "idioma": "language", "contexto": "context",
                                   "prob_min": "min_probability"}),
        "color": COLOR,
        "framing": Ctx(keys={"modo": "mode", "enfoque_x": "focus_x", "enfoque_y": "focus_y",
                             "margen_zoom": "zoom_margin", "empuje_lento": "slow_push"},
                       values={"mode": FRAMING_MODES}),
        "transitions": Ctx(keys={"rotacion": "rotation", "duracion": "duration"},
                           values={"rotation": TRANSITIONS}, removed=frozenset({"sonido"})),
        "effects": Ctx(keys={"zoom_en_picos": "zoom_on_peaks", "zoom_escala": "zoom_scale",
                             "pulso_cantidad": "pulse_amount", "zoom_proporcion": "zoom_ratio",
                             "flash_en_reveal": "flash_on_reveal", "barra_progreso": "progress_bar",
                             "color_barra": "progress_bar_color"}),
        "audio": Ctx(keys={"usar_original": "use_original", "ruido": "denoise", "paso_alto": "highpass",
                           "eq_voz": "voice_eq", "compresor": "compressor",
                           "ganancia_sin_voz_db": "gain_without_voice_db", "pico": "true_peak",
                           "ganancia_max_master_db": "master_max_gain_db", "dibujadas_db": "drawn_shot_db"}),
        "music": Ctx(keys={"archivo": "file", "carpeta": "folder", "volumen_con_voz_db": "volume_with_voice_db",
                           "volumen_sin_voz_db": "volume_without_voice_db", "entrada_seg": "start_at",
                           "fundido_salida": "fade_out", "fundido_entrada": "fade_in",
                           "cortes_en_beat": "cuts_on_beat", "medio_tiempo": "half_beats",
                           "drop_en_seg": "drop_at", "alinear": "align", "_entrada": "_start",
                           "bpm": "bpm", "factor_tempo": "tempo_factor", "lista": "playlist"},
                     values={"file": MUSIC_FILE, "align": MUSIC_ALIGN}),
        "sfx": Ctx(keys={"activado": "enabled", "volumen_db": "volume_db", "pop_en_textos": "pop_on_texts",
                         "tecleo": "typing", "tecleo_db": "typing_db", "tecleo_max_por_seg": "typing_max_per_sec",
                         "whoosh_en_transiciones": "whoosh_on_transitions"}),
        "subtitles": Ctx(keys={"max_palabras": "max_words", "max_caracteres": "max_chars",
                               "mayusculas": "uppercase", "fuente": "font", "tam": "size", "resalte": "highlight",
                               "borde": "stroke", "borde_color": "stroke_color", "sombra": "shadow",
                               "animacion": "animation"},
                         values={"animation": ANIMATIONS}),
        "texts": Ctx(keys={"estilos": "styles"},
                     children={"styles": Ctx(entry=TEXT_STYLE, entry_names=TEXT_STYLES)}),
        "export": Ctx(keys={"portada": "cover", "portada_seg": "cover_at"}),
        "polaroid": Ctx(keys={"ancho": "width", "centro_y": "center_y", "fuente": "font", "caida": "drop_time",
                              "destello": "flash"}),
        "collage": Ctx(keys={"columnas": "columns"}),
        "check": Ctx(keys={"tolerancia_volumen_lu": "loudness_tolerance_lu", "silencio_db": "silence_db",
                           "silencio_seg": "silence_sec", "salto_corte_lu": "cut_jump_lu",
                           "sfx_max_vs_mezcla_lu": "sfx_max_vs_mix_lu",
                           "musica_max_vs_original_lu": "music_max_vs_original_lu",
                           "tecleo_max_por_seg": "typing_max_per_sec", "tecleo_max_vs_mezcla_lu": "typing_max_vs_mix_lu",
                           "aspereza": "harsh_ratio", "aspereza_ventana": "harsh_window_ratio",
                           "negro_min_seg": "black_min_sec", "congelado_min_seg": "freeze_min_sec",
                           "tolerancia_beat_seg": "beat_tolerance_sec", "seguro_arriba": "safe_top",
                           "seguro_abajo": "safe_bottom", "seguro_derecha": "safe_right",
                           "columna_botones": "right_rail", "tolerancia_duracion": "duration_tolerance",
                           "escuchar_max": "listen_max", "escuchar_seg": "listen_sec"}),
        "vision": VISION,
        "photo": PHOTO,
        "graphics": Ctx(keys={"paquete": "pack", "tema": "theme"}),      # the theme's own keys are English only
    },
)

# ── plan.json ────────────────────────────────────────────────────────────────

EFFECT = Ctx(keys={"tipo": "type", "escala": "scale", "cantidad": "amount", "amplitud": "amplitude",
                   "seg": "seconds"},
             values={"type": EFFECT_TYPES})
SEARCH = Ctx(keys={"en": "range", "criterio": "criterion", "texto": "text"}, values={"criterion": CRITERIA})
SHOT = Ctx(
    keys={"fuente": "source", "ini": "start", "fin": "end", "velocidad": "speed", "rol": "role",
          "transicion": "transition", "efectos": "effects", "golpes": "beats", "continua": "continuous",
          "buscar": "search", "_buscado": "_searched", "tipo": "type", "de_toma": "from_shot",
          "de_tomas": "from_shots", "fondo_de": "background_from", "texto": "text", "giro": "tilt",
          "columnas": "columns", "enfoque_x": "focus_x", "enfoque_y": "focus_y", "volumen_db": "volume_db",
          "silenciar": "mute", "congelar": "freeze", "nota": "note", "puntaje": "score", "pico": "peak",
          "voz": "voice", "grupo": "group", "_golpes": "_beats", "duracion": "duration",
          "movimiento": "motion", "cantidad_movimiento": "motion_amount", "tono_auto": "auto_tone", **GRAPHIC_KEYS},
    # unchanged: dur, zoom, subdivision, color (its keys use the color table)
    values={"role": ROLES, "transition": TRANSITIONS, "type": SHOT_TYPES, "template": GRAPHIC_TEMPLATES},
    children={"effects": EFFECT, "search": SEARCH, "_searched": SEARCH, "color": COLOR},
    removed=frozenset({"_apariciones"}),
)
TEXT = Ctx(
    keys={**TEXT_STYLE_KEYS, "texto": "text", "descripcion": "description", "ini": "start", "fin": "end",
          "toma": "shot", "hasta_toma": "until_shot", "estilo": "style", "sonido": "sound"},
    # unchanged: pop, sfx (its value uses the sound table), color, x, y, desc_color
    values={"style": TEXT_STYLES, "animation": ANIMATIONS, "sfx": SFX_NAMES},
)
PLAN = Ctx(keys={"nota": "note", "tomas": "shots", "textos": "texts", "candidatos_extra": "extra_candidates"},
           children={"shots": SHOT, "texts": TEXT, "extra_candidates": SHOT})

# ── project file (proyecto.yaml → project.yaml) ─────────────────────────────

PROJECT = Ctx(keys={"nombre": "name", "receta": "recipe", "fuentes": "sources", "ajustes": "settings"},
              values={"recipe": RECIPE_NAMES},
              children={"sources": Ctx(keys={"ruta": "path"}), "settings": CONFIG})

# ── cache files ──────────────────────────────────────────────────────────────

MEDIA_INFO = Ctx(keys={"ruta": "path", "duracion": "duration", "audio": "has_audio",
                       "audio_canales": "audio_channels", "audio_indice": "audio_index", "ancho": "width",
                       "alto": "height", "rotacion": "rotation"})
# unchanged: id, fps, codec, pix_fmt, transfer, primaries, hdr, hlg, proxy, wav
WORD = Ctx(keys={"p": "word", "ini": "start", "fin": "end"})          # unchanged: prob
ANALYSIS = Ctx(
    keys={"duracion": "duration", "muestras": "samples", "escenas": "scenes", "palabras": "words", "voz": "voice"},
    children={"samples": Ctx(keys={"luz": "brightness", "nitidez": "sharpness", "movimiento": "motion"}),
              "audio": Ctx(keys={"paso": "step", "picos": "peaks", "db_medio": "median_db"}),
              "words": WORD,
              "voice": Ctx(keys={"tramos": "segments", "proporcion": "ratio"})},
)
MUSIC_ANALYSIS = Ctx(keys={"duracion": "duration", "intervalo": "interval", "golpes": "beats"})

# ── rendered summary (resumen.json → summary.json), read only by tools ──────

SUMMARY = Ctx(
    keys={"duracion_seg": "duration_sec", "tomas": "shots", "resolucion": "resolution", "musica": "music",
          "subtitulos_auto": "auto_subtitles", "textos": "texts", "efectos_sonido": "sound_effects",
          "tiempo_render_seg": "render_time_sec", "tomas_detalle": "shot_details"},
    children={"music": Ctx(keys={"archivo": "file", "desde_seg": "start_sec"}),
              "audio": Ctx(keys={"antes_lufs": "before_lufs", "despues_lufs": "after_lufs",
                                 "pico_dbtp": "true_peak_dbtp"}),
              "shot_details": Ctx(keys={"tipo": "type", "fuente": "source", "de": "from", "a": "to",
                                        "en_video": "in_video", "corte_seg": "cut_sec", "rol": "role",
                                        "transicion": "transition"},
                                  values={"role": ROLES, "transition": TRANSITIONS})},
)

# ── file and folder names ────────────────────────────────────────────────────

FILES = {"project.yaml": "proyecto.yaml", "plan_used.json": "plan_usado.json",
         "config_used.json": "config_usada.json", "summary.json": "resumen.json"}
FOLDERS = {"projects": "proyectos", "recipes": "recetas", "music": "musica", "sfx": "sfx"}
# cache: <id>_media.json ← <id>_info.json, <id>_analysis.json ← <id>_analisis.json,
#        <wav>_words.json ← <wav>_palabras.json, music_<song>.json ← musica_<song>.json
CACHE_FILES = {"media": ("{key}_media.json", "{key}_info.json", MEDIA_INFO),
               "analysis": ("{key}_analysis.json", "{key}_analisis.json", ANALYSIS),
               "words": ("{key}_words.json", "{key}_palabras.json", WORD),
               "music": ("music_{key}.json", "musica_{key}.json", MUSIC_ANALYSIS)}
ENV_WORKSPACE = ("SVE_WORKSPACE", "MOTOR_ESPACIO")


def config(d: dict) -> dict:
    return translate(d or {}, CONFIG)


def plan(d: dict) -> dict:
    return translate(d or {}, PLAN)


def project(d: dict) -> dict:
    return translate(d or {}, PROJECT)


def summary(d: dict) -> dict:
    return translate(d or {}, SUMMARY)


def recipe_name(name: str) -> str:
    return RECIPE_NAMES.get(name, name)


def legacy_recipe_name(name: str) -> str | None:
    """Spanish file name of a recipe (resena for review…), or None if it has none."""
    return next((es for es, en in RECIPE_NAMES.items() if en == name), None)


def profile_name(name: str | None) -> str | None:
    return PROFILES.get(name, name) if name else name


def legacy_sfx_name(name: str) -> str | None:
    return next((es for es, en in SFX_NAMES.items() if en == name), None)


def graphic(spec: dict) -> dict:
    """A graphic cue / shot with Spanish keys and template name → English ("barras" → "bars")."""
    return translate(spec, SHOT)
