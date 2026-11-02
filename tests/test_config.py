"""Configuración en capas, búsqueda de recetas y nombres de carpetas de versión."""
from __future__ import annotations

import pytest
import yaml

from motor_edicion.config import Proyecto, cargar_receta, mezclar, recetas_disponibles, ruta_receta
from motor_edicion.util import RECETAS_PAQUETE


def test_mezclar_profunda_sin_mutar():
    base = {"a": {"b": 1, "c": 2}, "lista": [1, 2], "x": 1}
    encima = {"a": {"b": 3, "d": {"e": 4}}, "lista": [9], "y": None}
    out = mezclar(base, encima)
    assert out == {"a": {"b": 3, "c": 2, "d": {"e": 4}}, "lista": [9], "x": 1, "y": None}
    assert base == {"a": {"b": 1, "c": 2}, "lista": [1, 2], "x": 1}          # no se toca la base
    out["a"]["d"]["e"] = 99
    assert encima["a"]["d"]["e"] == 4                                          # copia profunda
    assert mezclar(base, None) == base


def test_mezclar_dict_reemplaza_escalar_y_viceversa():
    assert mezclar({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}
    assert mezclar({"a": {"b": 2}}, {"a": 5}) == {"a": 5}


def test_receta_en_capas_unboxing():
    base, unb = cargar_receta("base"), cargar_receta("unboxing")
    assert unb["ritmo"]["toma_max"] == 2.8                    # de unboxing.yaml
    assert unb["ritmo"]["toma_min"] == base["ritmo"]["toma_min"] == 0.8   # heredado de base.yaml
    assert unb["seleccion"]["pesos"]["picos"] == 1.1
    assert unb["seleccion"]["pesos"]["movimiento"] == base["seleccion"]["pesos"]["movimiento"]
    assert unb["transiciones"]["duracion"] == 0.22
    assert unb["transiciones"]["rotacion"][-1] == "slide_arriba"   # las listas se reemplazan enteras


def test_todas_las_recetas_del_paquete_cargan():
    nombres = set(recetas_disponibles())
    assert {"base", "unboxing", "tutorial", "resena", "vlog", "antes_despues", "storytime"} <= nombres
    for n in nombres:
        cfg = cargar_receta(n)
        assert cfg["salida"]["fps"] == 30


def test_receta_inexistente():
    with pytest.raises(SystemExit, match="No existe la receta"):
        cargar_receta("no_existe")


def test_receta_del_espacio_tiene_prioridad(espacio):
    assert ruta_receta("unboxing") == RECETAS_PAQUETE / "unboxing.yaml"
    (espacio / "recetas").mkdir()
    (espacio / "recetas" / "unboxing.yaml").write_text("ritmo:\n  toma_max: 9.5\n", encoding="utf-8")
    (espacio / "recetas" / "mia.yaml").write_text("duracion:\n  objetivo: 7\n", encoding="utf-8")
    assert ruta_receta("unboxing") == espacio / "recetas" / "unboxing.yaml"
    cfg = cargar_receta("unboxing")
    assert cfg["ritmo"]["toma_max"] == 9.5
    assert cfg["seleccion"]["pesos"]["picos"] == 0.8          # la del paquete ya no se usa
    assert cargar_receta("mia")["duracion"]["objetivo"] == 7
    assert recetas_disponibles()["mia"] == espacio / "recetas" / "mia.yaml"


def _proyecto(espacio, ajustes=None):
    d = espacio / "proyectos" / "demo"
    d.mkdir(parents=True)
    fuente = espacio / "clip.mov"
    fuente.write_bytes(b"")
    datos = {"nombre": "demo", "receta": "unboxing", "fuentes": [str(fuente)], "ajustes": ajustes or {}}
    (d / "proyecto.yaml").write_text(yaml.safe_dump(datos), encoding="utf-8")
    return Proyecto(d)


def test_proyecto_ajustes_sobre_receta(espacio):
    p = _proyecto(espacio, {"ritmo": {"toma_min": 0.5}, "musica": {"entrada_seg": "auto"}})
    assert p.receta == "unboxing"
    assert p.cfg["ritmo"]["toma_min"] == 0.5
    assert p.cfg["ritmo"]["toma_max"] == 2.8
    assert p.cfg["musica"]["entrada_seg"] == "auto"
    assert p.cfg["musica"]["fundido_salida"] == 1.0
    assert p.fuentes[0]["ruta"] == str(espacio / "clip.mov")
    assert p.cache.is_dir()


def test_proyecto_fuente_faltante(espacio):
    d = espacio / "p2"
    d.mkdir()
    (d / "proyecto.yaml").write_text("fuentes: [/no/existe.mov]\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="No encuentro la fuente"):
        Proyecto(d)


def test_carpeta_variante_y_versiones(espacio):
    p = _proyecto(espacio)
    assert p.ultima_version() is None
    assert p.carpeta_variante("SinMusica").name == "v1SinMusica"      # sin versiones: número 1
    assert p.nueva_version().name == "v1"
    (p.dir / "v2").mkdir()
    assert p.nueva_version().name == "v3"
    assert p.carpeta_variante("SinMusica").name == "v3SinMusica"
    assert p.carpeta_variante("SinMusica").name == "v3SinMusica_2"
    assert p.carpeta_variante("SinMusica").name == "v3SinMusica_3"
    assert p.carpeta_variante("Borrador").name == "v3Borrador"
    assert p.ultima_version().name == "v3"                              # las variantes no cuentan
    assert p.nueva_version().name == "v4"
