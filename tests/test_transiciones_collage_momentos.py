"""Unión con transiciones, apariciones del collage y elección de la mejor ventana."""
from __future__ import annotations

import pytest

from motor_edicion.collage import tiempos_aparicion
from motor_edicion.momentos import mejor_ventana, resolver_rangos
from motor_edicion.transiciones import CATALOGO, duracion_transicion, info, unir


def test_unir_cuenta_xfade_y_concat():
    segs = [{"dur": 2.0, "solape": 0.0, "transicion": "corte"},
            {"dur": 1.0, "solape": 0.0, "transicion": "corte"},
            {"dur": 1.5, "solape": 0.35, "transicion": "fundido"},
            {"dur": 1.0, "solape": 0.2, "transicion": "zoom"},
            {"dur": 1.0, "solape": 0.0, "transicion": "whip"}]      # xfade sin solape → concat
    filtro, v, a = unir(segs, 30)
    assert (v, a) == ("[v4]", "[a4]")
    assert filtro.count("xfade=") == 2
    assert filtro.count("acrossfade=") == 2
    assert filtro.count("concat=n=2") == 2
    assert "xfade=transition=fade:duration=0.3500:offset=2.6500" in filtro
    assert "xfade=transition=zoomin:duration=0.2000:offset=3.9500" in filtro
    for i in range(5):
        assert f"[{i}:v]fps=30,settb=AVTB" in filtro


def test_unir_un_solo_segmento():
    filtro, v, a = unir([{"dur": 1.0, "solape": 0.0, "transicion": "corte"}], 30)
    assert (v, a) == ("[sv0]", "[sa0]")
    assert "xfade" not in filtro and "concat" not in filtro


def test_duracion_transicion(cfg):
    assert duracion_transicion("corte", cfg) == 0.0
    assert duracion_transicion("fundido", cfg) == 0.35          # duración propia fija
    assert duracion_transicion("zoom", cfg) == cfg["transiciones"]["duracion"] == 0.22
    with pytest.raises(SystemExit):
        info("no_existe")
    assert all(len(v) == 3 for v in CATALOGO.values())


def test_tiempos_aparicion_subdivision_4():
    tm = {"dur": 2.0, "_golpes": [0.3, 0.8, 1.3, 1.8]}
    assert tiempos_aparicion(tm, 6, 4) == pytest.approx([0.3, 0.425, 0.55, 0.675, 0.8, 0.925])
    # incluye el golpe del arranque si cabe antes del primero
    tm = {"dur": 2.0, "_golpes": [0.6, 1.1, 1.6]}
    assert tiempos_aparicion(tm, 3, 4) == pytest.approx([0.1, 0.225, 0.35])
    # la toma puede fijar su propia subdivisión
    assert tiempos_aparicion({**tm, "subdivision": 1}, 3, 4) == pytest.approx([0.1, 0.6, 1.1])
    assert tiempos_aparicion(tm, 3, 2) == pytest.approx([0.1, 0.35, 0.6])


def test_tiempos_aparicion_sin_musica():
    tm = {"dur": 2.0}
    assert tiempos_aparicion(tm, 4, 4) == pytest.approx([0.1, 0.35, 0.6, 0.85])   # primer 50 %
    # golpes insuficientes para todas las fotos → reparto uniforme
    tm = {"dur": 1.0, "_golpes": [0.3, 0.8]}
    assert tiempos_aparicion(tm, 20, 4) == pytest.approx([0.1 + k * 0.5 / 20 for k in range(20)])


def _analisis(dur=10.0, por_seg=4):
    muestras = []
    for k in range(int(dur * por_seg) + 1):
        t = k / por_seg
        producto = 6.0 <= t <= 7.5                     # objeto nítido, colorido y quieto
        accion = 3.0 <= t <= 4.0                       # mucho movimiento
        muestras.append({"t": t, "luz": 0.5, "nitidez": 500.0 if producto else 50.0,
                         "color": 40.0 if producto else 10.0, "movimiento": 9.0 if accion else 1.0})
    db = [-40.0] * int(dur * 10)
    for i in range(30, 41):
        db[i] = -10.0                                  # sonido fuerte en 3–4 s
    return {"duracion": dur, "muestras": muestras, "audio": {"paso": 0.1, "db": db, "db_medio": -40.0}}


def test_mejor_ventana_producto_y_accion():
    an = _analisis()
    assert mejor_ventana(an, [2, 9], 1.5, "producto") == (6.0, 7.5)
    ini, fin = mejor_ventana(an, [0, 9], 1.0, "accion")
    assert (ini, fin) == (3.0, 4.0)


def test_mejor_ventana_bordes():
    an = _analisis()
    assert mejor_ventana(an, [-1, 3], 5) == (0.0, 3.0)          # rango más corto que la ventana
    assert mejor_ventana(an, [8, 30], 1.0) in {(round(8 + k * 0.25, 2), round(9 + k * 0.25, 2)) for k in range(5)}
    pocas = {"duracion": 10.0, "muestras": [{"t": 0, "nitidez": 1, "color": 1, "movimiento": 0}]}
    assert mejor_ventana(pocas, [2, 8], 1.5) == (2.0, 3.5)       # < 3 muestras: desde el inicio


def test_resolver_rangos():
    plan = {"tomas": [{"fuente": 0, "buscar": {"en": [2, 9], "dur": 1.5}},
                      {"fuente": 0, "ini": 1, "fin": 2}]}
    resolver_rangos(plan, [_analisis()], {})
    assert (plan["tomas"][0]["ini"], plan["tomas"][0]["fin"]) == (6.0, 7.5)
    assert plan["tomas"][1] == {"fuente": 0, "ini": 1, "fin": 2}
