"""Línea de tiempo, alineación de cortes a los golpes de la música y textos en tiempo absoluto."""
from __future__ import annotations

import copy

import numpy as np
import pytest

from motor_edicion.plan import (alinear_a_golpes, cuadros, duracion_total, fuente_a_salida, linea_de_tiempo,
                                textos_absolutos)

FPS = 30


def _plan():
    return {"tomas": [
        {"fuente": 0, "ini": 0.0, "fin": 2.0, "transicion": "corte"},
        {"fuente": 0, "ini": 5.0, "fin": 6.5, "velocidad": 1.5, "transicion": "negro"},   # negro = 0,4 s fijo
        {"tipo": "polaroid", "de_toma": 1, "dur": 1.2, "transicion": "corte"},
        {"tipo": "collage", "de_tomas": [0, 1], "golpes": 3, "transicion": "zoom"},    # sin dur: 0,6 s por golpe
    ]}


def test_cuadros():
    assert cuadros(1.0) == 1.0
    assert cuadros(0.22) == pytest.approx(7 / 30)
    assert cuadros(0.4) == pytest.approx(12 / 30)


def test_linea_de_tiempo(cfg):
    tl = linea_de_tiempo(_plan(), cfg)
    assert [t["i"] for t in tl] == [0, 1, 2, 3]
    assert [t["dur"] for t in tl] == pytest.approx([2.0, 1.0, 1.2, 1.8])
    # la transición de entrada solapa la toma con la anterior
    assert [t["solape"] for t in tl] == pytest.approx([0.0, 0.4, 0.0, 7 / 30])
    assert [t["out_ini"] for t in tl] == pytest.approx([0.0, 1.6, 2.6, 3.5667], abs=1e-4)
    assert [t["out_fin"] for t in tl] == pytest.approx([2.0, 2.6, 3.8, 5.3667], abs=1e-4)
    assert duracion_total(tl) == pytest.approx(5.3667, abs=1e-4)
    assert duracion_total([]) == 0.0
    assert tl[2]["tipo"] == "polaroid" and tl[2]["de_toma"] == 1        # conserva los campos de la toma


def test_solape_limitado_a_media_toma(cfg):
    plan = {"tomas": [{"fuente": 0, "ini": 0, "fin": 2.0},
                      {"fuente": 0, "ini": 3, "fin": 3.4, "transicion": "negro"},   # 0,4 s: solape ≤ 0,2
                      {"fuente": 0, "ini": 5, "fin": 7, "transicion": "negro"}]}    # anterior 0,4 → ≤ 0,2
    tl = linea_de_tiempo(plan, cfg)
    assert [t["solape"] for t in tl] == pytest.approx([0.0, 0.2, 0.2])


def test_congelar_suma_duracion(cfg):
    plan = {"tomas": [{"fuente": 0, "ini": 0, "fin": 1.0, "congelar": 0.5,
                       "efectos": [{"tipo": "congelar", "seg": 0.3}]}]}
    assert linea_de_tiempo(plan, cfg)[0]["dur"] == pytest.approx(1.8)


def test_fuente_a_salida(cfg):
    tl = linea_de_tiempo(_plan(), cfg)
    assert fuente_a_salida(tl, 0, 1.0) == pytest.approx(1.0)
    assert fuente_a_salida(tl, 0, 5.75) == pytest.approx(1.6 + 0.75 / 1.5)
    assert fuente_a_salida(tl, 0, 3.0) is None


def test_textos_absolutos(cfg):
    plan = _plan()
    tl = linea_de_tiempo(plan, cfg)
    plan["textos"] = [
        {"texto": "absoluto", "ini": 0.5, "fin": 100},                  # se recorta al final del video
        {"texto": "toma0", "toma": 0},                                  # hasta que empieza la siguiente
        {"texto": "rel", "toma": 1, "ini": 0.1, "fin": 0.5},            # espera a que acabe la transición
        {"texto": "varias", "toma": 1, "hasta_toma": 2},
        {"texto": "vacio", "toma": 1, "ini": 0.1, "fin": 0.3},          # fin <= ini → se descarta
        {"texto": "ultima", "toma": 3, "ini": 0.5},
    ]
    res = {t["texto"]: (t["ini"], t["fin"]) for t in textos_absolutos(plan, tl)}
    assert set(res) == {"absoluto", "toma0", "rel", "varias", "ultima"}
    assert res["absoluto"] == pytest.approx((0.5, 5.3667), abs=1e-4)
    assert res["toma0"] == pytest.approx((0.0, 1.6))
    assert res["rel"] == pytest.approx((2.0, 2.1))
    assert res["varias"] == pytest.approx((2.0, 3.5667), abs=1e-4)   # hasta_toma 2, corta al entrar la 3
    assert res["ultima"] == pytest.approx((3.5667 + 0.5, 5.3667), abs=1e-4)
    assert plan["textos"][2]["ini"] == 0.1                            # no modifica el plan


def _golpes(T, n=80, desde=0.0):
    return [desde + k * T for k in range(n)]


def _cortes(tl):
    """Centro de cada transición de entrada (= momento del corte), tomas 1..n."""
    return [t["out_ini"] + t["solape"] / 2 for t in tl[1:]]


@pytest.fixture
def cfg_golpes(cfg):
    cfg = copy.deepcopy(cfg)
    cfg["transiciones"]["duracion"] = 0.2          # 6 cuadros: el centro de la transición cae en un cuadro
    return cfg


def _plan_golpes():
    return {"tomas": [
        {"fuente": 0, "ini": 1.0, "fin": 2.0, "golpes": 3},
        {"fuente": 0, "ini": 10.0, "fin": 11.0, "golpes": 2, "velocidad": 1.5, "transicion": "flash"},
        {"fuente": 0, "ini": 50.0, "fin": 51.0, "golpes": 2, "velocidad": 0.5, "continua": True,
         "transicion": "corte"},
        {"tipo": "polaroid", "de_toma": 2, "golpes": 1, "transicion": "corte"},
        {"fuente": 1, "ini": 3.0, "fin": 4.0, "transicion": "zoom"},           # sin "golpes": duración natural
        {"tipo": "collage", "de_tomas": [0, 1], "golpes": 4, "transicion": "negro"},
    ]}


def test_alinear_a_golpes_cada_corte_en_un_golpe(cfg_golpes):
    T = 0.5                                              # 120 BPM: cada golpe cae en un cuadro exacto
    g = _golpes(T)
    plan = _plan_golpes()
    n = alinear_a_golpes(plan, g, cfg_golpes, [60.0, 60.0])
    assert n == 6
    tl = linea_de_tiempo(plan, cfg_golpes)
    golpes_por_toma = [3, 2, 2, 1, 2, 4]                  # la toma 4 (1 s natural) = 2 golpes
    esperados = list(np.cumsum(golpes_por_toma) * T)
    for c, e in zip(_cortes(tl), esperados[:-1]):
        assert c == pytest.approx(e, abs=2e-4)
    assert duracion_total(tl) == pytest.approx(esperados[-1], abs=2e-4)   # el video acaba en un golpe
    # continua: la toma 2 empieza exactamente donde terminó la 1 (rampas)
    assert plan["tomas"][2]["ini"] == plan["tomas"][1]["fin"]
    # las tomas dibujadas cambian su "dur"
    # (cada toma se extiende media transición dentro de la siguiente y media dentro de la anterior)
    assert plan["tomas"][3]["dur"] == pytest.approx(T + 0.2 / 2, abs=1e-4)      # sale con "zoom" (0,2 s)
    assert plan["tomas"][5]["dur"] == pytest.approx(4 * T + 0.4 / 2, abs=1e-4)  # entra con "negro" (0,4 s)
    # cada toma guarda sus golpes internos (relativos a su inicio)
    tl0 = tl[0]
    assert plan["tomas"][0]["_golpes"] == pytest.approx([T, 2 * T, 3 * T], abs=1e-3)   # incl. el del corte
    for tm, t in zip(plan["tomas"], tl):
        for b in tm["_golpes"]:
            assert 0.05 < b < t["dur"] - 0.05
    assert tl0["out_ini"] == 0.0


def test_alinear_a_golpes_tempo_real_medio_cuadro(cfg_golpes):
    T = 0.6977                                           # tempo que no coincide con los cuadros
    g = _golpes(T, desde=0.0)
    plan = _plan_golpes()
    alinear_a_golpes(plan, g, cfg_golpes, [60.0, 60.0])
    tl = linea_de_tiempo(plan, cfg_golpes)
    esperados = np.cumsum([3, 2, 2, 1, 1, 4]) * T          # toma 4: 1 s natural ≈ 1 golpe
    for c, e in zip(_cortes(tl), esperados[:-1]):
        assert abs(c - e) <= 1 / (2 * FPS) + 1e-3          # a lo sumo medio cuadro de redondeo


def test_alinear_a_golpes_fuente_corta_corre_la_toma(cfg_golpes):
    plan = {"tomas": [{"fuente": 0, "ini": 9.0, "fin": 9.5, "golpes": 4},
                      {"fuente": 0, "ini": 0.0, "fin": 1.0, "golpes": 2}]}
    alinear_a_golpes(plan, _golpes(0.5), cfg_golpes, [10.0])
    t0 = plan["tomas"][0]
    assert t0["fin"] == pytest.approx(9.98)                # no pasa del final de la fuente
    assert t0["fin"] - t0["ini"] == pytest.approx(2.0, abs=1e-3)


def test_alinear_a_golpes_sin_golpes(cfg_golpes):
    plan = _plan_golpes()
    antes = copy.deepcopy(plan)
    assert alinear_a_golpes(plan, [1.0], cfg_golpes, [60.0, 60.0]) == 0
    assert plan == antes
