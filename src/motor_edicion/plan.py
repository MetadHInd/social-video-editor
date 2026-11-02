"""Plan de edición (plan.json) y línea de tiempo del video final.

Formato de una toma:
  {"fuente": 0, "ini": 12.3, "fin": 14.8,          # segundos dentro de la fuente (índice en proyecto.yaml)
   "velocidad": 1.0, "rol": "historia",              # gancho | historia | revelacion | cierre
   "transicion": "corte",                            # cómo ENTRA esta toma (ver transiciones.py)
   "efectos": [{"tipo": "zoom_golpe", "t": 0.3}],    # ver efectos.py
   "zoom": 1.0, "enfoque_x": 0.5, "enfoque_y": 0.5,  # encuadre opcional por toma
   "volumen_db": 0, "silenciar": false}
Formato de un texto:
  {"texto": "…", "ini": 0, "fin": 2, "estilo": "gancho"}            # tiempos del video final
  {"texto": "…", "toma": 3, "ini": 0.1, "fin": 1.5, "estilo": "etiqueta"}  # relativo al inicio de la toma 3
  {"texto": "…", "descripcion": "…", "toma": 3, "hasta_toma": 4, "estilo": "tarjeta"}  # dura las tomas 3 y 4
"""
from __future__ import annotations

from .efectos import congelar
from .transiciones import duracion_transicion

FPS_DEF = 30


def cuadros(seg: float, fps: int = FPS_DEF) -> float:
    """Redondea a cuadros enteros (evita desfases en las uniones)."""
    return round(seg * fps) / fps


def linea_de_tiempo(plan: dict, cfg: dict) -> list[dict]:
    """Calcula dónde cae cada toma en el video final."""
    fps = cfg["salida"]["fps"]
    out, t = [], 0.0
    for i, tm in enumerate(plan["tomas"]):
        v = float(tm.get("velocidad", 1.0))
        if tm.get("tipo"):            # polaroid, collage…: duración propia
            dur = cuadros(float(tm.get("dur", 0.6 * float(tm.get("golpes", 2)))), fps)
        else:
            dur = cuadros((tm["fin"] - tm["ini"]) / v + congelar(tm), fps)
        d = 0.0 if i == 0 else duracion_transicion(tm.get("transicion", "corte"), cfg)
        d = min(d, dur / 2, out[-1]["dur"] / 2) if out else 0.0
        d = cuadros(d, fps)
        ini_out = t - d
        out.append({**tm, "i": i, "dur": dur, "solape": d, "out_ini": round(ini_out, 4),
                    "out_fin": round(ini_out + dur, 4)})
        t = ini_out + dur
    return out


def duracion_total(tl: list[dict]) -> float:
    return tl[-1]["out_fin"] if tl else 0.0


def fuente_a_salida(tl: list[dict], fuente: int, t_src: float) -> float | None:
    """Convierte un instante de una fuente al tiempo del video final (None si no se usa)."""
    for tm in tl:
        if not tm.get("tipo") and tm["fuente"] == fuente and tm["ini"] <= t_src < tm["fin"]:
            return tm["out_ini"] + (t_src - tm["ini"]) / float(tm.get("velocidad", 1.0))
    return None


def textos_absolutos(plan: dict, tl: list[dict]) -> list[dict]:
    """Pasa los textos anclados a tomas a tiempos del video final."""
    res = []
    total = duracion_total(tl)
    for tx in plan.get("textos", []):
        tx = dict(tx)
        if "toma" in tx:
            k = int(tx["toma"])
            base = tl[k]
            fin_rel = tx.get("fin", base["dur"])
            # aparece cuando termina la transición de entrada y se va antes de la siguiente transición
            ini = max(base["out_ini"] + tx.get("ini", 0), base["out_ini"] + base["solape"])
            fin = base["out_ini"] + fin_rel
            k_fin = int(tx.get("hasta_toma", k))          # un texto puede durar varias tomas seguidas
            if k_fin != k:
                fin = tl[k_fin]["out_fin"]
            if k_fin + 1 < len(tl):
                fin = min(fin, tl[k_fin + 1]["out_ini"])
            tx["ini"], tx["fin"] = ini, fin
        tx["fin"] = min(tx["fin"], total)
        if tx["fin"] > tx["ini"]:
            res.append(tx)
    return res


def ajustar_a_beats(plan: dict, beats: list[float], cfg: dict, duraciones_fuente: list[float],
                    ventana: float = 0.3) -> int:
    """Mueve cada corte al golpe musical más cercano alargando/acortando la toma anterior."""
    import numpy as np

    if not beats:
        return 0
    b = sorted(beats)
    if cfg["musica"].get("medio_tiempo", True) and len(b) > 1:   # también en el contratiempo (entre golpes)
        b = sorted(b + [(x + y) / 2 for x, y in zip(b, b[1:])])
    b = np.array(b)
    movidos = 0
    for i in range(1, len(plan["tomas"])):
        tl = linea_de_tiempo(plan, cfg)
        corte = tl[i]["out_ini"] + tl[i]["solape"] / 2
        j = int(np.argmin(np.abs(b - corte)))
        delta = float(b[j] - corte)
        if abs(delta) < 1 / 60 or abs(delta) > ventana:
            continue
        prev = plan["tomas"][i - 1]
        v = float(prev.get("velocidad", 1.0))
        nuevo_fin = prev["fin"] + delta * v
        if nuevo_fin - prev["ini"] < cfg["ritmo"]["toma_min"] * v or nuevo_fin > duraciones_fuente[prev["fuente"]]:
            continue
        prev["fin"] = round(nuevo_fin, 3)
        movidos += 1
    return movidos


def alinear_a_golpes(plan: dict, golpes: list[float], cfg: dict, duraciones_fuente: list[float]) -> int:
    """Hace que cada corte caiga EXACTAMENTE en un golpe de la música.

    "continua": true → la toma empieza donde terminó la anterior (para rampas rápido→lento).
    Cada toma dura "golpes": N golpes (si no lo dice, los golpes más cercanos a su duración natural).
    El centro de cada transición queda sobre el golpe. Las tomas normales se alargan/acortan desde
    su inicio (o se corren hacia atrás si la fuente no da para más); polaroid/collage cambian su "dur".
    Guarda en cada toma "_golpes" (golpes dentro de la toma, en segundos relativos) para pulso_ritmo.
    """
    import numpy as np

    if len(golpes) < 2:
        return 0
    g = np.array(sorted(golpes))
    T = float(np.median(np.diff(g)))
    fps = cfg["salida"]["fps"]
    tomas = plan["tomas"]
    # golpe en el que empieza el video (el primero >= 0)
    k = int(np.searchsorted(g, -1e-6))
    cortes = []
    for tm in tomas:
        if "golpes" in tm:
            n = int(tm["golpes"])
        else:
            v = float(tm.get("velocidad", 1.0))
            natural = float(tm["dur"]) if tm.get("tipo") else (tm["fin"] - tm["ini"]) / v
            n = max(1, int(round(natural / T)))
        k += n
        cortes.append(float(g[k]) if k < len(g) else float(g[-1] + (k - len(g) + 1) * T))
    solapes = [0.0] + [cuadros(duracion_transicion(tm.get("transicion", "corte"), cfg), fps) for tm in tomas[1:]]
    fin_prev = 0.0
    for i, tm in enumerate(tomas):
        out_ini = fin_prev - solapes[i]
        fin_out = cortes[i] + (solapes[i + 1] / 2 if i + 1 < len(tomas) else 0)
        dur = cuadros(fin_out - out_ini, fps)
        if tm.get("tipo"):
            tm["dur"] = round(dur, 4)
        else:
            v = float(tm.get("velocidad", 1.0))
            largo = (dur - congelar(tm)) * v
            prev = tomas[i - 1] if i else None
            if tm.get("continua") and prev and not prev.get("tipo") and prev["fuente"] == tm["fuente"]:
                tm["ini"] = prev["fin"]          # sigue exactamente donde terminó la toma anterior (rampas)
            maximo = duraciones_fuente[tm["fuente"]]
            tm["fin"] = round(tm["ini"] + largo, 3)
            if tm["fin"] > maximo:
                tm["fin"] = round(maximo - 0.02, 3)
                tm["ini"] = round(max(tm["fin"] - largo, 0), 3)
        tm["_golpes"] = [round(float(b - out_ini), 3) for b in g if out_ini + 0.05 < b < out_ini + dur - 0.05]
        fin_prev = out_ini + dur
    return len(tomas)
