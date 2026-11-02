"""Análisis de cada fuente (sobre el proxy): qué pasa en cada momento del video.

Guarda cache/<id>_analisis.json con:
  muestras   : t, luz, nitidez, movimiento, color (varias por segundo)
  escenas    : cambios de plano detectados
  audio      : volumen (dB) cada 0,1 s y picos (sonidos fuertes: rasgar, abrir, risas)
  palabras   : transcripción palabra por palabra con tiempos (Whisper)
  voz        : tramos con habla y % del video con voz
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .util import guardar_json, leer_json, log


def _muestras(proxy: str, por_seg: float) -> list[dict]:
    import cv2

    cap = cv2.VideoCapture(proxy)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    paso = max(int(round(fps / por_seg)), 1)
    out, prev, i = [], None, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % paso == 0:
            _, f = cap.retrieve()
            peq = cv2.resize(f, (180, int(180 * f.shape[0] / f.shape[1])))
            gris = cv2.cvtColor(peq, cv2.COLOR_BGR2GRAY).astype(np.float32)
            mov = float(np.mean(np.abs(gris - prev))) if prev is not None else 0.0
            prev = gris
            b, g, r = [peq[..., k].astype(np.float32) for k in range(3)]
            rg, yb = np.abs(r - g), np.abs(0.5 * (r + g) - b)
            color = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
            out.append({
                "t": round(i / fps, 3),
                "luz": round(float(gris.mean() / 255), 4),
                "nitidez": round(float(cv2.Laplacian(gris, cv2.CV_32F).var()), 2),
                "movimiento": round(mov, 3),
                "color": round(color, 2),
            })
        i += 1
    cap.release()
    return out


def _escenas(proxy: str, umbral: float) -> list[list[float]]:
    try:
        from scenedetect import ContentDetector, detect
        lista = detect(proxy, ContentDetector(threshold=umbral), show_progress=False)
        return [[round(a.get_seconds(), 3), round(b.get_seconds(), 3)] for a, b in lista]
    except Exception as e:  # noqa: BLE001
        log(f"Aviso: detección de escenas falló ({e}); se usa una sola escena.")
        return []


def _audio(wav: str) -> dict:
    import soundfile as sf

    x, sr = sf.read(wav, dtype="float32")
    if x.ndim > 1:
        x = x.mean(1)
    hop = int(sr * 0.1)
    n = len(x) // hop
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-10)
    db = 20 * np.log10(rms)
    # picos: subidas bruscas sobre la mediana local de ~2 s
    from scipy.ndimage import median_filter
    base = median_filter(db, size=21)
    picos = [round(i * 0.1, 2) for i in range(1, n - 1)
             if db[i] - base[i] > 9 and db[i] >= db[i - 1] and db[i] >= db[i + 1] and db[i] > -45]
    return {"paso": 0.1, "db": [round(float(v), 1) for v in db], "picos": picos,
            "db_medio": round(float(np.median(db)), 1)}


def _transcribir(wav: str, cfg: dict, cache: Path) -> list[dict]:
    import os
    import subprocess
    import sys

    t = cfg.get("transcripcion", {})
    cfg_p, out_p = cache / "_whisper_cfg.json", cache / (Path(wav).stem + "_palabras.json")
    guardar_json(cfg_p, t)
    # el subproceso encuentra el paquete aunque no esté instalado o se ejecute desde otra carpeta
    raiz_paquete = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(x for x in (raiz_paquete, os.environ.get("PYTHONPATH")) if x)}
    r = subprocess.run([sys.executable, "-m", "motor_edicion._whisper", wav, str(cfg_p), str(out_p)],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        log("Aviso: la transcripción falló; se sigue sin subtítulos automáticos.\n" + r.stderr[-800:])
        return []
    return leer_json(out_p)


def _tramos_voz(palabras: list[dict], hueco: float = 0.7) -> list[list[float]]:
    tramos = []
    for w in palabras:
        if tramos and w["ini"] - tramos[-1][1] <= hueco:
            tramos[-1][1] = w["fin"]
        else:
            tramos.append([w["ini"], w["fin"]])
    return tramos


def analizar(info: dict, cache: Path, cfg: dict, forzar: bool = False) -> dict:
    ruta = cache / f"{info['id']}_analisis.json"
    if ruta.exists() and not forzar:
        return leer_json(ruta)
    a = cfg.get("analisis", {})
    log(f"Analizando {Path(info['ruta']).name}: imagen…")
    datos = {"id": info["id"], "duracion": info["duracion"],
             "muestras": _muestras(info["proxy"], float(a.get("muestras_por_seg", 4)))}
    log("  escenas…")
    datos["escenas"] = _escenas(info["proxy"], float(a.get("umbral_escena", 27)))
    if info.get("wav"):
        log("  audio…")
        datos["audio"] = _audio(info["wav"])
        log("  transcripción (Whisper)…")
        datos["palabras"] = _transcribir(info["wav"], cfg, cache)
    else:
        datos["audio"], datos["palabras"] = None, []
    tramos = _tramos_voz(datos["palabras"])
    datos["voz"] = {"tramos": tramos,
                    "proporcion": round(sum(b - a_ for a_, b in tramos) / max(info["duracion"], 1e-3), 3)}
    guardar_json(ruta, datos)
    log(f"  listo: {len(datos['escenas'])} escenas, {len(datos['palabras'])} palabras, "
        f"voz {datos['voz']['proporcion'] * 100:.0f}% del video")
    return datos
