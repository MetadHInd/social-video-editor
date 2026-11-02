"""Translation of legacy Spanish files (adapters/files/legacy_es.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from social_video_editor.adapters.files import legacy_es
from social_video_editor.adapters.files.cache import JsonCacheStore
from social_video_editor.adapters.files.recipes import YamlRecipeRepository
from social_video_editor.domain.timeline import absolute_texts, compute_timeline

# The shape of a real Spanish plan (synthetic content): search windows, beats, ramps, polaroids,
# collage, counters and cards with descriptions, overrides of style position.
SPANISH_PLAN = {
    "nota": "plan de prueba",
    "tomas": [
        {"fuente": 1, "buscar": {"en": [2.5, 6.5], "dur": 2.0, "criterio": "producto"}, "golpes": 3,
         "rol": "gancho", "transicion": "corte", "efectos": [{"tipo": "zoom_golpe", "t": 0}], "nota": "todo"},
        {"fuente": 0, "buscar": {"en": [5, 12], "dur": 2.0, "criterio": "accion"}, "golpes": 2, "velocidad": 1.5,
         "rol": "apertura", "transicion": "flash", "efectos": []},
        {"fuente": 0, "ini": 49.7, "fin": 50.95, "golpes": 1, "velocidad": 1.8, "rol": "producto",
         "transicion": "zoom", "efectos": [{"tipo": "pulso_ritmo", "cantidad": 0.03}]},
        {"fuente": 0, "ini": 50.954, "fin": 51.79, "golpes": 2, "velocidad": 0.6, "rol": "estrella",
         "transicion": "corte", "continua": True, "congelar": 0.2, "enfoque_x": 0.3, "enfoque_y": 0.6,
         "volumen_db": -3, "silenciar": False, "color": {"saturacion": 1.2, "nitidez": 0.1},
         "efectos": [{"tipo": "zoom_golpe", "t": 0.05, "escala": 1.1}, {"tipo": "sacudida", "amplitud": 9},
                     {"tipo": "congelar", "seg": 0.4}, {"tipo": "empuje"}, {"tipo": "alejar"}]},
        {"tipo": "polaroid", "de_toma": 3, "golpes": 1, "texto": "Funda 💛", "giro": -3, "transicion": "corte",
         "rol": "polaroid"},
        {"fuente": 0, "ini": 72.0, "fin": 74.0, "golpes": 3, "rol": "revelacion", "transicion": "slide_arriba",
         "_golpes": [0.5, 1.0], "_buscado": {"en": [70, 80], "criterio": "accion"}, "puntaje": 1.2},
        {"tipo": "collage", "de_tomas": [2, 3, 5], "fondo_de": 5, "golpes": 7, "columnas": 4, "subdivision": 4,
         "transicion": "desliz_izq", "rol": "collage", "_apariciones": [0.1]},
    ],
    "textos": [
        {"texto": "¿Valió la pena? 👀", "toma": 0, "estilo": "gancho"},
        {"texto": "Funda", "descripcion": "lo más tierno 💛", "toma": 2, "hasta_toma": 4, "estilo": "tarjeta"},
        {"texto": "1/3", "toma": 2, "hasta_toma": 4, "estilo": "contador", "animacion": "ninguna"},
        {"texto": "Comenta 💬", "toma": 6, "ini": 0.3, "fin": 2.0, "estilo": "cta", "y": 0.8, "tam": 50,
         "velocidad": 20, "sonido": False, "sfx": "tecla", "fondo": "#000000", "mayusculas": True},
        {"texto": "absoluto", "ini": 0.0, "fin": 1.0, "estilo": "precio"},
    ],
    "candidatos_extra": [{"fuente": 0, "ini": 2.6, "fin": 5.4, "puntaje": 2.5, "pico": True, "voz": False}],
}

ENGLISH_PLAN = {
    "note": "plan de prueba",
    "shots": [
        {"source": 1, "search": {"range": [2.5, 6.5], "dur": 2.0, "criterion": "product"}, "beats": 3,
         "role": "hook", "transition": "cut", "effects": [{"type": "zoom_punch", "t": 0}], "note": "todo"},
        {"source": 0, "search": {"range": [5, 12], "dur": 2.0, "criterion": "action"}, "beats": 2, "speed": 1.5,
         "role": "opening", "transition": "flash", "effects": []},
        {"source": 0, "start": 49.7, "end": 50.95, "beats": 1, "speed": 1.8, "role": "product",
         "transition": "zoom", "effects": [{"type": "beat_pulse", "amount": 0.03}]},
        {"source": 0, "start": 50.954, "end": 51.79, "beats": 2, "speed": 0.6, "role": "star",
         "transition": "cut", "continuous": True, "freeze": 0.2, "focus_x": 0.3, "focus_y": 0.6,
         "volume_db": -3, "mute": False, "color": {"saturation": 1.2, "sharpen": 0.1},
         "effects": [{"type": "zoom_punch", "t": 0.05, "scale": 1.1}, {"type": "shake", "amplitude": 9},
                     {"type": "freeze", "seconds": 0.4}, {"type": "push_in"}, {"type": "pull_out"}]},
        {"type": "polaroid", "from_shot": 3, "beats": 1, "text": "Funda 💛", "tilt": -3, "transition": "cut",
         "role": "polaroid"},
        {"source": 0, "start": 72.0, "end": 74.0, "beats": 3, "role": "reveal", "transition": "slide_up",
         "_beats": [0.5, 1.0], "_searched": {"range": [70, 80], "criterion": "action"}, "score": 1.2},
        {"type": "collage", "from_shots": [2, 3, 5], "background_from": 5, "beats": 7, "columns": 4,
         "subdivision": 4, "transition": "slide_left", "role": "collage"},
    ],
    "texts": [
        {"text": "¿Valió la pena? 👀", "shot": 0, "style": "hook"},
        {"text": "Funda", "description": "lo más tierno 💛", "shot": 2, "until_shot": 4, "style": "card"},
        {"text": "1/3", "shot": 2, "until_shot": 4, "style": "counter", "animation": "none"},
        {"text": "Comenta 💬", "shot": 6, "start": 0.3, "end": 2.0, "style": "cta", "y": 0.8, "size": 50,
         "speed": 20, "sound": False, "sfx": "keystroke", "background": "#000000", "uppercase": True},
        {"text": "absoluto", "start": 0.0, "end": 1.0, "style": "price"},
    ],
    "extra_candidates": [{"source": 0, "start": 2.6, "end": 5.4, "score": 2.5, "peak": True, "voice": False}],
}


def test_spanish_plan_translates_to_english():
    assert legacy_es.plan(SPANISH_PLAN) == ENGLISH_PLAN


def test_translation_is_idempotent_and_english_passes_through():
    assert legacy_es.plan(ENGLISH_PLAN) == ENGLISH_PLAN
    assert legacy_es.plan(legacy_es.plan(SPANISH_PLAN)) == ENGLISH_PLAN
    assert legacy_es.plan(json.loads(json.dumps(SPANISH_PLAN))) == ENGLISH_PLAN   # as read from plan.json


def test_translated_plan_drives_the_domain(cfg):
    plan = legacy_es.plan(SPANISH_PLAN)
    for s in plan["shots"]:
        s.pop("search", None)
        s.setdefault("start", 1.0) if "type" not in s else None
        s.setdefault("end", 2.0) if "type" not in s else None
    tl = compute_timeline(plan, cfg)
    assert [c.piece.kind for c in tl] == ["video"] * 4 + ["polaroid", "video", "collage"]
    assert tl[3].dur == pytest.approx(2.0)            # (51.79 − 50.954) / 0.6 + freeze 0.2 + freeze effect 0.4
    assert tl.sequence.render_order()[-2:] == [4, 6]
    assert [t["style"] for t in absolute_texts(plan, tl)] == ["hook", "card", "counter", "cta", "price"]


def test_config_translation_and_removed_keys():
    es = {"salida": {"ancho": 720, "alto": 1280, "fps": 30, "perfiles": ["tiktok"]},
          "duracion": {"objetivo": 20, "minimo": 5, "maximo": 40}, "cierre": {"bucle": True},
          "gancho": {"estrategia": "inicio", "transicion": "fundido"},
          "seleccion": {"pesos": {"movimiento": 1, "palabras_clave": 2}, "palabras_clave": ["hola"]},
          "encuadre": {"modo": "desenfoque", "enfoque_y": 0.2},
          "transiciones": {"rotacion": ["corte", "negro", "circulo"], "sonido": True, "duracion": 0.3},
          "musica": {"archivo": "ninguna", "entrada_seg": "auto", "alinear": "exacto", "_entrada": 3.0},
          "subtitulos": {"animacion": "pop", "borde": 5, "resalte": "#FF0000"},
          "textos": {"estilos": {"tarjeta": {"tam": 50, "animacion": "escribir", "desc_tam": 40},
                                 "mio": {"fondo": None}}},
          "exportar": {"portada": False, "portada_seg": 2.0},
          "polaroid": {"caida": 0.2, "destello": 0.1, "centro_y": 0.4},
          "collage": {"columnas": 3}, "idioma": "en"}
    en = legacy_es.config(es)
    assert en == {
        "output": {"width": 720, "height": 1280, "fps": 30},
        "duration": {"target": 20, "max": 40},
        "hook": {"strategy": "start", "transition": "fade"},
        "selection": {"weights": {"motion": 1, "keywords": 2}, "keywords": ["hola"]},
        "framing": {"mode": "blur", "focus_y": 0.2},
        "transitions": {"rotation": ["cut", "fade_black", "circle"], "duration": 0.3},
        "music": {"file": "none", "start_at": "auto", "align": "exact", "_start": 3.0},
        "subtitles": {"animation": "pop", "stroke": 5, "highlight": "#FF0000"},
        "texts": {"styles": {"card": {"size": 50, "animation": "typewriter", "desc_size": 40},
                             "mio": {"background": None}}},
        "export": {"cover": False, "cover_at": 2.0},
        "polaroid": {"drop_time": 0.2, "flash": 0.1, "center_y": 0.4},
        "collage": {"columns": 3}, "language": "en"}
    assert legacy_es.config(en) == en


def test_every_english_recipe_key_is_reachable_from_spanish():
    """Every key of the packaged base recipe has a Spanish origin in the tables (or is new/unchanged)."""
    base = YamlRecipeRepository().read("base")
    english_targets = set()

    def collect(ctx):
        english_targets.update(ctx.keys.values())
        for c in ctx.children.values():
            collect(c)
        if ctx.entry:
            collect(ctx.entry)
    collect(legacy_es.CONFIG)
    same_in_both = {"fps", "weights", "audio", "color", "auto", "gamma_min", "gamma_max", "vibrance", "lut",
                    "ducking", "ducking_asmr", "ducking_ratio", "sfx", "uppercase", "y", "styles", "crf",
                    "maxrate", "audio_kbps", "polaroid", "collage", "subdivision", "y0", "y1", "loudness",
                    "asmr", "motion", "peaks", "voice", "keywords", "sharpness", "language"}

    def walk(d, path=""):
        for k, v in d.items():
            if path.startswith("texts.styles."):
                continue
            assert k in english_targets or k in same_in_both, f"{path}{k} has no legacy mapping"
            if isinstance(v, dict) and not set(v) <= {"es", "en"}:
                walk(v, f"{path}{k}.")
    walk(base)


def test_cache_store_reads_legacy_files(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "clip_analisis.json").write_text(json.dumps({
        "id": "clip", "duracion": 10.0,
        "muestras": [{"t": 0, "luz": 0.5, "nitidez": 10.0, "movimiento": 1.0, "color": 3.0}],
        "escenas": [[0, 10]], "audio": {"paso": 0.1, "db": [-20.0], "picos": [1.0], "db_medio": -20.0},
        "palabras": [{"p": "hola", "ini": 1.0, "fin": 1.4, "prob": 0.9}],
        "voz": {"tramos": [[1.0, 1.4]], "proporcion": 0.04}}), encoding="utf-8")
    (cache / "clip_info.json").write_text(json.dumps({"ruta": "/x/clip.mov", "duracion": 10.0, "audio": True,
                                                      "audio_indice": 1, "ancho": 1080, "alto": 1920}))
    store = JsonCacheStore()
    an = store.load(cache, "analysis", "clip")
    assert an["samples"][0] == {"t": 0, "brightness": 0.5, "sharpness": 10.0, "motion": 1.0, "color": 3.0}
    assert an["audio"] == {"step": 0.1, "db": [-20.0], "peaks": [1.0], "median_db": -20.0}
    assert an["words"] == [{"word": "hola", "start": 1.0, "end": 1.4, "prob": 0.9}]
    assert an["voice"] == {"segments": [[1.0, 1.4]], "ratio": 0.04}
    info = store.load(cache, "media", "clip")
    assert info == {"path": "/x/clip.mov", "duration": 10.0, "has_audio": True, "audio_index": 1,
                    "width": 1080, "height": 1920}
    # new files are written in English, next to the untouched legacy ones
    p = store.save(cache, "media", "clip", info)
    assert p.name == "clip_media.json" and (cache / "clip_info.json").exists()
    assert store.load(cache, "media", "clip") == info
    assert store.load(cache, "analysis", "missing") is None


def test_legacy_keys_doc_lists_every_mapping():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "LEGACY_KEYS.md").read_text(encoding="utf-8")

    def check(ctx):
        for es, en in ctx.keys.items():
            assert f"`{es}`" in doc and f"`{en}`" in doc, (es, en)
        for table in ctx.values.values():
            for es, en in table.items():
                assert f"`{es}`" in doc and f"`{en}`" in doc, (es, en)
        for c in ctx.children.values():
            check(c)
        if ctx.entry:
            check(ctx.entry)
    for ctx in (legacy_es.CONFIG, legacy_es.PLAN, legacy_es.PROJECT, legacy_es.ANALYSIS, legacy_es.MEDIA_INFO,
                legacy_es.MUSIC_ANALYSIS, legacy_es.SUMMARY):
        check(ctx)
