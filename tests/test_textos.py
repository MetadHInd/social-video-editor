"""Texto con emojis (unidades) y línea de tiempo del efecto de escritura."""
from __future__ import annotations

import pytest

from motor_edicion import texto_rico as tr
from motor_edicion.subtitulos import _bloques, _elemento_texto, fuente


def test_unidades_letras_y_emojis():
    assert tr.unidades("Hola 👀") == ["H", "o", "l", "a", " ", "👀"]
    assert tr.unidades("") == []
    familia = "\U0001F469‍\U0001F469‍\U0001F467"          # 👩‍👩‍👧 (ZWJ)
    corazon = "❤️"                                       # ❤️ (selector de variación VS16)
    bolsas = "\U0001F6CD️"                                    # 🛍️
    assert tr.unidades(f"a{familia}b") == ["a", familia, "b"]
    assert tr.unidades(f"{corazon}{bolsas}!") == [corazon, bolsas, "!"]
    assert tr.unidades("ok ✨ ya") == ["o", "k", " ", "✨", " ", "y", "a"]
    assert all(tr.es_emoji(u) for u in (familia, corazon, bolsas, "👀"))
    assert not tr.es_emoji("a") and not tr.es_emoji(" ")


def test_partir_conserva_unidades():
    f = fuente("Inter-Black", 40)
    lineas = tr.partir("uno dos tres cuatro 👀", f, 40, 160)
    assert len(lineas) > 1
    plano = [u for ln in lineas for u in ln]
    assert "".join(plano).replace(" ", "") == "unodostrescuatro👀"
    assert lineas[-1][-1] == "👀"


EST = {"tam": 60, "fuente": "Inter-Black", "color": "#2B2B2B", "fondo": "#FFFFFFEB", "y": 0.2,
       "max_ancho": 0.88, "radio": 0.5, "animacion": "escribir", "velocidad": 34,
       "desc_fuente": "Inter-Black", "desc_tam": 40, "desc_color": "#D6457F"}


def _estado(e, t):
    """'t{idx}_{n_titulo}_{n_desc}_{cursor}' → (n_t, n_d, cursor)."""
    clave, _ = e.estado(t)
    _, n_t, n_d, cur = clave.split("_")
    return int(n_t), int(n_d), (None if cur == "None" else cur)


@pytest.mark.parametrize("texto,desc,ini,fin", [
    ("Hola mundo", "abc", 1.0, 3.0),
    ("Un título bastante largo que no cabe a la velocidad normal", "y una descripción también larga", 0.0, 1.5),
    ("Solo título", None, 2.0, 4.0),
])
def test_escritura_letra_a_letra(texto, desc, ini, fin):
    tx = {"texto": texto, "ini": ini, "fin": fin}
    if desc:
        tx["descripcion"] = desc
    teclas = []
    e = _elemento_texto(tx, dict(EST), 540, 960, "Inter-Black", 0, teclas)
    tit, des = _bloques(tx, EST, 540, "Inter-Black")
    n_tit, n_des = tit.n, (des.n if des else 0)
    assert e.pop and e.ini == ini and e.fin == fin

    paso = 0.001
    ts = [ini + k * paso for k in range(int((fin - ini) / paso))]
    estados = [_estado(e, t) for t in ts]
    prev = (0, 0)
    t_completo = None
    for t, (n_t, n_d, cur) in zip(ts, estados):
        assert n_t >= prev[0] and n_d >= prev[1]                  # las letras aparecen en orden
        assert n_t <= n_tit and n_d <= n_des
        if n_d > 0:
            assert n_t == n_tit                                   # la descripción va después del título
        completo = (n_t, n_d) == (n_tit, n_des)
        if not completo:
            assert cur == ("t" if n_t < n_tit or not des else "d")   # cursor mientras escribe
        elif t_completo is None:
            t_completo = t
        prev = (n_t, n_d)
    assert _estado(e, ini) == (0, 0, "t")
    # termina antes de que el texto desaparezca, con tiempo para leer (≤ 75 % del tiempo disponible)
    disponible = max(fin - ini - 0.35, 0.3)
    assert t_completo is not None
    assert t_completo <= ini + 0.04 + 0.75 * disponible + (0.14 if des else 0) + paso
    # parpadeo del cursor cada 0,38 s tras terminar: apagado, encendido, apagado…
    lado = "d" if des else "t"
    t0 = t_completo
    assert _estado(e, t0 + 0.19)[2] is None
    assert _estado(e, t0 + 0.57)[2] == lado
    assert _estado(e, t0 + 0.95)[2] is None
    # una tecla por unidad visible que no es espacio
    unidades = [u for ln in tit.lineas for u in ln] + ([u for ln in des.lineas for u in ln] if des else [])
    assert len(teclas) == sum(1 for u in unidades if u != " ")
    assert teclas == sorted(teclas) and ini < teclas[0] and teclas[-1] < fin


def test_escritura_emoji_es_una_tecla():
    tx = {"texto": "Hola 👀", "ini": 0.0, "fin": 2.0}
    teclas = []
    e = _elemento_texto(tx, dict(EST), 540, 960, "Inter-Black", 3, teclas)
    tit, _ = _bloques(tx, EST, 540, "Inter-Black")
    assert tit.n == 6 and len(teclas) == 5
    assert e.clave == "t3"


def test_animacion_pop_estatica():
    tx = {"texto": "Hola", "ini": 0.0, "fin": 1.0, "animacion": "pop"}
    e = _elemento_texto(tx, dict(EST), 540, 960, "Inter-Black", 1, [])
    assert e.estado(0.1)[0] == e.estado(0.9)[0] == "t1"
    assert e.pop
    assert {0.0, 1.0} <= e.cortes()
