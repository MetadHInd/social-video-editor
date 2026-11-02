"""Elige los mejores momentos del material y arma un plan de edición inicial.

Cada instante recibe un puntaje (movimiento, volumen, picos de sonido, voz, palabras clave,
nitidez, color). Se toman las ventanas con más puntaje sin cortar frases ni cruzar cambios
de escena, hasta llenar la duración objetivo. El plan resultante (plan.json) es editable.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np

from .util import log


def _norm_txt(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[^a-zñ0-9 ]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def _serie(an: dict, cfg: dict, refs: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """Puntaje por muestra (4/s) para una fuente."""
    sel = cfg["seleccion"]
    w = sel["pesos"]
    m = an["muestras"]
    t = np.array([x["t"] for x in m])
    mov = np.clip(np.array([x["movimiento"] for x in m]) / refs["mov"], 0, 1.5)
    mov_s = np.where(mov > 1.0, 1.0 - (mov - 1.0), mov)               # demasiado movimiento = barrido borroso
    nit = np.log1p([x["nitidez"] for x in m])
    nit_n = np.clip((nit - refs["nit_lo"]) / max(refs["nit_hi"] - refs["nit_lo"], 1e-3), 0, 1)
    col = np.clip(np.array([x["color"] for x in m]) / refs["col"], 0, 1.2)
    luz = np.array([x["luz"] for x in m])

    aud = np.zeros_like(t)
    picos = np.zeros_like(t)
    if an.get("audio"):
        db = np.array(an["audio"]["db"])
        idx = np.clip((t / an["audio"]["paso"]).astype(int), 0, len(db) - 1)
        aud = np.clip((db[idx] - an["audio"]["db_medio"]) / 18, 0, 1)
        for p in an["audio"]["picos"]:
            picos[(t >= p - 0.25) & (t <= p + 0.6)] = 1
    voz = np.zeros_like(t)
    for a, b in an["voz"]["tramos"]:
        voz[(t >= a) & (t <= b)] = 1
    kw = np.zeros_like(t)
    claves = {_norm_txt(k) for k in sel.get("palabras_clave", [])}
    hits = []
    for p in an.get("palabras", []):
        if _norm_txt(p["p"]) in claves:
            kw[(t >= p["ini"] - 0.6) & (t <= p["fin"] + 0.6)] = 1
            hits.append(p["p"])

    s = (w["movimiento"] * mov_s + w["audio"] * aud + w["picos"] * picos + w["voz"] * voz
         + w["palabras_clave"] * kw + w["nitidez"] * nit_n + w["color"] * col)
    malo = (luz < sel["luz_minima"]) | (nit < refs["nit_borroso"])
    s = np.where(malo, s - 5, s)
    return t, s, {"picos": picos, "voz": voz, "kw_hits": hits}


def _referencias(analisis: list[dict], cfg: dict) -> dict:
    todas = [x for a in analisis for x in a["muestras"]]
    nit = np.log1p([x["nitidez"] for x in todas])
    return {
        "mov": float(np.percentile([x["movimiento"] for x in todas], 90) or 1),
        "nit_lo": float(np.percentile(nit, 10)), "nit_hi": float(np.percentile(nit, 90)),
        "nit_borroso": float(np.percentile(nit, cfg["seleccion"]["borroso_percentil"])),
        "col": float(np.percentile([x["color"] for x in todas], 90) or 1),
    }


def _limites(an: dict) -> list[float]:
    cortes = [a for a, _ in an.get("escenas", [])[1:]]
    return sorted(cortes)


def _ajustar_a_frase(ini: float, fin: float, an: dict, frase_max: float) -> tuple[float, float]:
    """Si la ventana corta una frase, la extiende para no cortar palabras."""
    for a, b in an["voz"]["tramos"]:
        if a < fin and b > ini:                       # se solapa con un tramo de voz
            ni, nf = min(ini, a - 0.08), max(fin, b + 0.12)
            if nf - ni <= frase_max:
                ini, fin = ni, nf
            else:                                     # frase demasiado larga: corta en un silencio entre palabras
                pals = [p for p in an["palabras"] if ini - 0.5 <= p["ini"] <= fin + 0.5]
                if pals:
                    ini = min(ini, pals[0]["ini"] - 0.08) if pals[0]["ini"] < ini else ini
                    fins = [p["fin"] for p in pals if p["fin"] <= ini + frase_max]
                    fin = max(fins) + 0.1 if fins else fin
    return max(ini, 0), min(fin, an["duracion"])


def proponer(fuentes: list[dict], analisis: list[dict], cfg: dict) -> dict:
    r, d = cfg["ritmo"], cfg["duracion"]
    refs = _referencias(analisis, cfg)
    candidatos = []
    for fi, an in enumerate(analisis):
        t, s, extra = _serie(an, cfg, refs)
        if len(t) < 4:
            continue
        paso = t[1] - t[0]
        largo = max(int(round(r["toma_max"] / paso)), 2)
        suave = np.convolve(s, np.ones(largo) / largo, mode="same")
        cortes = _limites(an)
        usado = np.zeros(len(t), bool)
        for i in np.argsort(-suave):
            if usado[i] or suave[i] < 0:
                continue
            ini, fin = t[i] - r["toma_max"] / 2, t[i] + r["toma_max"] / 2
            for c in cortes:                          # no cruzar cambios de escena
                if ini < c < t[i]:
                    ini = c + 0.05
                if t[i] < c < fin:
                    fin = c - 0.05
            ini, fin = _ajustar_a_frase(ini, fin, an, r["frase_max"])
            if fin - ini < r["toma_min"]:
                continue
            m = (t >= ini - r["separacion_min"]) & (t <= fin + r["separacion_min"])
            if usado[m].any():
                continue
            usado[m] = True
            seg = (t >= ini) & (t <= fin)
            candidatos.append({
                "fuente": fi, "ini": round(float(ini), 2), "fin": round(float(fin), 2),
                "puntaje": round(float(s[seg].mean()), 3),
                "pico": bool(extra["picos"][seg].any()), "voz": bool(extra["voz"][seg].mean() > 0.3),
            })
            if len(candidatos) > 400:
                break

    # Elegir: primero lo mejor de cada fuente (si se pide), luego lo mejor global
    candidatos.sort(key=lambda c: -c["puntaje"])
    objetivo = d["objetivo"] - (cfg["gancho"]["duracion"] if cfg["gancho"]["estrategia"] == "teaser" else 0)
    elegidos, total = [], 0.0

    def dur(c):
        v = 1.0 if c["voz"] else r["acelerar_sin_voz"]
        return (c["fin"] - c["ini"]) / v

    if cfg["seleccion"].get("usar_todas_las_fuentes", True):
        for fi in range(len(analisis)):
            mejor = next((c for c in candidatos if c["fuente"] == fi and c["puntaje"] > 0), None)
            if mejor:
                elegidos.append(mejor)
                total += dur(mejor)
    tramos_n = int(cfg["seleccion"].get("cobertura", 0) or 0)
    if tramos_n:   # cobertura: el mejor momento de cada tramo del material (no todo del mismo minuto)
        largo_total = sum(a["duracion"] for a in analisis)
        bordes, acum = [], 0.0
        for fi, a in enumerate(analisis):
            bordes.append((fi, acum, acum + a["duracion"]))
            acum += a["duracion"]

        def pos(c):
            return next(b0 for fi, b0, _ in bordes if fi == c["fuente"]) + c["ini"]

        for k in range(tramos_n):
            a0, a1 = largo_total * k / tramos_n, largo_total * (k + 1) / tramos_n
            mejor = next((c for c in candidatos if a0 <= pos(c) < a1 and c not in elegidos
                          and c["puntaje"] > 0), None)
            if mejor and total < objetivo:
                elegidos.append(mejor)
                total += dur(mejor)
    for c in candidatos:
        if total >= objetivo:
            break
        if c in elegidos or c["puntaje"] <= 0:
            continue
        if total + dur(c) > d["maximo"]:
            continue
        elegidos.append(c)
        total += dur(c)
    elegidos.sort(key=lambda c: (c["fuente"], c["ini"]))      # orden de la historia
    log(f"Momentos: {len(candidatos)} candidatos → {len(elegidos)} tomas (~{total:.1f} s)")
    return {"tomas": elegidos, "candidatos": candidatos[:80]}


def armar_plan(fuentes: list[dict], analisis: list[dict], cfg: dict) -> dict:
    """Plan de edición completo: tomas + gancho + transiciones + efectos."""
    prop = proponer(fuentes, analisis, cfg)
    tomas = []
    for c in prop["tomas"]:
        an = analisis[c["fuente"]]
        dicho = " ".join(p["p"] for p in an.get("palabras", []) if c["ini"] <= p["ini"] < c["fin"])
        tomas.append({
            "fuente": c["fuente"], "ini": c["ini"], "fin": c["fin"],
            "velocidad": 1.0 if c["voz"] else cfg["ritmo"]["acelerar_sin_voz"],
            "rol": "historia", "transicion": "corte", "efectos": [],
            "puntaje": c["puntaje"], "nota": dicho[:120],
        })
    if cfg["efectos"]["zoom_en_picos"]:   # zoom solo en una parte de las tomas con pico (las de más puntaje)
        con_pico = sorted((k for k, c in enumerate(prop["tomas"]) if c["pico"]),
                          key=lambda k: -prop["tomas"][k]["puntaje"])
        cupo = int(round(len(tomas) * float(cfg["efectos"].get("zoom_proporcion", 0.3))))
        for k in con_pico[:cupo]:
            tomas[k]["efectos"].append({"tipo": "zoom_golpe"})

    g = cfg["gancho"]
    if tomas and g["estrategia"] == "teaser":
        mejor = max(tomas, key=lambda x: x["puntaje"])
        mejor["rol"] = "revelacion"
        centro = (mejor["ini"] + mejor["fin"]) / 2
        gancho = {**mejor, "ini": round(max(centro - g["duracion"] / 2, mejor["ini"]), 2),
                  "rol": "gancho", "velocidad": 1.0, "efectos": [{"tipo": "zoom_golpe"}]}
        gancho["fin"] = round(min(gancho["ini"] + g["duracion"], mejor["fin"]), 2)
        tomas.insert(0, gancho)

    rot = cfg["transiciones"]["rotacion"] or ["corte"]
    for i, tm in enumerate(tomas):
        if i == 0:
            tm["transicion"] = "corte"
        elif i == 1 and g["estrategia"] == "teaser":
            tm["transicion"] = g.get("transicion", "flash")
        else:
            tm["transicion"] = rot[(i - 1) % len(rot)]
        if tm["rol"] == "revelacion" and cfg["efectos"].get("flash_en_reveal"):
            tm["transicion"] = "flash"

    textos = []
    if g.get("texto"):
        textos.append({"texto": g["texto"], "ini": 0.0, "fin": max(g["duracion"] + 0.8, 2.2), "estilo": "gancho"})
    return {
        "nota": "Plan editable. Tiempos de tomas = segundos dentro de cada fuente; "
                "tiempos de textos = segundos del video final. Vuelve a renderizar con: motor render <proyecto>",
        "tomas": tomas,
        "textos": textos,
        "candidatos_extra": [c for c in prop["candidatos"] if c not in prop["tomas"]][:30],
    }


def mejor_ventana(an: dict, en: list[float], dur: float, criterio: str = "producto") -> tuple[float, float]:
    """Dentro de [a, b] de una fuente, la ventana de 'dur' segundos más adecuada.

    producto = imagen nítida, colorida y quieta (mostrar un objeto)
    accion   = movimiento y sonido (abrir, rasgar, sacar)
    """
    a, b = max(0.0, en[0]), min(an["duracion"], en[1])
    if b - a <= dur:
        return round(a, 2), round(b, 2)
    m = [x for x in an["muestras"] if a <= x["t"] <= b]
    if len(m) < 3:
        return round(a, 2), round(a + dur, 2)
    t = np.array([x["t"] for x in m])
    nit = np.log1p([x["nitidez"] for x in m])
    nit = (nit - nit.min()) / max(np.ptp(nit), 1e-6)
    col = np.array([x["color"] for x in m]); col = (col - col.min()) / max(np.ptp(col), 1e-6)
    mov = np.array([x["movimiento"] for x in m]); mov = (mov - mov.min()) / max(np.ptp(mov), 1e-6)
    if criterio == "accion":
        s = mov + 0.5 * nit
        if an.get("audio"):
            db = np.array(an["audio"]["db"])
            s = s + np.clip((db[np.clip((t / 0.1).astype(int), 0, len(db) - 1)] - an["audio"]["db_medio"]) / 18, 0, 1)
    else:
        s = 1.2 * nit + 0.8 * col - 1.0 * mov
    mejor, ini_mejor = -1e9, a
    for i, t0 in enumerate(t):
        if t0 + dur > b:
            break
        sel = (t >= t0) & (t <= t0 + dur)
        v = s[sel].mean() - 0.5 * s[sel].std()
        if v > mejor:
            mejor, ini_mejor = v, t0
    return round(float(ini_mejor), 2), round(float(ini_mejor + dur), 2)


def resolver_rangos(plan: dict, analisis: list[dict], cfg: dict) -> dict:
    """Completa ini/fin de las tomas que piden 'buscar' (se recalcula en cada render)."""
    for tm in plan["tomas"]:
        b = tm.get("buscar")
        if b:
            tm["ini"], tm["fin"] = mejor_ventana(analisis[tm["fuente"]], b["en"], float(b.get("dur", 1.8)),
                                                 b.get("criterio", "producto"))
    return plan
