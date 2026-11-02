"""Pruebas con FFmpeg sobre medios sintéticos generados al vuelo (nada del usuario)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import yaml

from conftest import requiere_ffmpeg
from motor_edicion import color
from motor_edicion.util import FFMPEG, FFPROBE, leer_json, sondear

pytestmark = [pytest.mark.integracion, requiere_ffmpeg]


def ff(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


def sonda(ruta: Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


# ── HDR (HLG) → SDR ──────────────────────────────────────────────────────────

def test_lut_hlg_se_genera_en_cache_de_usuario(tmp_path):
    lut = color.lut_hlg_a_sdr(tam=17)
    assert lut.parent == tmp_path / "cache_usuario" / "motor_edicion" / "luts"     # nunca en el paquete
    lineas = lut.read_text().splitlines()
    assert lineas[0] == 'TITLE "HLG a SDR709"'
    assert lineas[1] == "LUT_3D_SIZE 17"
    datos = np.array([[float(x) for x in ln.split()] for ln in lineas[2:]])
    assert datos.shape == (17 ** 3, 3)
    assert datos.min() >= 0 and datos.max() <= 1
    assert np.allclose(datos[0], 0)                                     # negro → negro
    assert np.all(np.diff(datos[:17, 0]) >= 0)                          # rampa de R monótona
    assert color.lut_hlg_a_sdr(tam=17) == lut                           # segunda vez: se reutiliza


def test_cadena_sdr_convierte_hlg(tmp_path):
    hlg = tmp_path / "hlg.mp4"
    # etiquetas en los cuadros (setparams): FFmpeg 7+ ignora -color_trc de salida si el cuadro no lo trae
    ff("-f", "lavfi", "-i", "testsrc2=s=320x568:d=0.5:r=30", "-vf",
       "format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
       "-c:v", "libx264", hlg)
    info = sondear(hlg)
    assert info["hlg"] and info["hdr"] and not info["audio"]
    cadena = color.cadena_sdr(info)
    assert "lut3d=" in cadena and str(tmp_path / "cache_usuario") in cadena
    sdr = tmp_path / "sdr.mp4"
    ff("-i", hlg, "-vf", f"scale=270:480,{cadena}", "-c:v", "libx264", "-preset", "ultrafast", sdr)
    v = sonda(sdr)["streams"][0]
    assert (v["width"], v["height"], v["pix_fmt"]) == (270, 480, "yuv420p")
    assert v.get("color_transfer") == "bt709"
    # una fuente SDR no usa LUT
    assert "lut3d" not in color.cadena_sdr({"hlg": False, "hdr": False})


# ── Render completo (borrador) ───────────────────────────────────────────────

def _proyecto_sintetico(espacio: Path) -> Path:
    fuentes = espacio / "fuentes"
    fuentes.mkdir()
    clip = fuentes / "clip.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=540x960:d=4:r=30",
       "-f", "lavfi", "-i", "sine=f=330:d=4:sample_rate=48000",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip)
    musica = espacio / "musica"
    musica.mkdir()
    # pista de clics a 120 BPM (un golpe cada 0,5 s) con un tono de fondo
    ff("-f", "lavfi", "-i",
       "aevalsrc='0.8*sin(2*PI*1000*t)*exp(-mod(t,0.5)*40)+0.1*sin(2*PI*110*t)':d=20:s=44100",
       "-ac", "2", musica / "clics.wav")
    d = espacio / "proyectos" / "demo"
    d.mkdir(parents=True)
    proyecto = {
        "nombre": "demo", "receta": "unboxing", "fuentes": [{"ruta": str(clip)}],
        "ajustes": {"subtitulos": {"auto": False}, "transcripcion": {"modelo": "tiny"},
                    "collage": {"subdivision": 4}},
    }
    (d / "proyecto.yaml").write_text(yaml.safe_dump(proyecto, allow_unicode=True), encoding="utf-8")
    plan = {
        "tomas": [
            {"fuente": 0, "ini": 0.5, "fin": 2.5, "golpes": 4, "transicion": "corte",
             "efectos": [{"tipo": "zoom_golpe", "t": 0}, {"tipo": "pulso_ritmo"}]},
            {"tipo": "polaroid", "de_toma": 0, "golpes": 2, "texto": "Foto 📸", "transicion": "corte"},
            {"tipo": "collage", "de_tomas": [0, 0, 0], "fondo_de": 0, "golpes": 4, "columnas": 3,
             "transicion": "zoom"},
        ],
        "textos": [
            {"texto": "Hola 👀", "descripcion": "prueba ✨", "toma": 0, "estilo": "tarjeta"},
            {"texto": "fin", "toma": 2, "estilo": "etiqueta"},
        ],
    }
    (d / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return d


def test_render_borrador_de_punta_a_punta(espacio, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")             # Whisper nunca descarga modelos en las pruebas
    monkeypatch.chdir(espacio.parent)                     # el espacio sale de MOTOR_ESPACIO, no de la carpeta actual
    d = _proyecto_sintetico(espacio)
    from motor_edicion.cli import main
    from motor_edicion.plan import duracion_total, linea_de_tiempo

    main(["render", "demo", "--borrador"])

    vdir = d / "v1Borrador"
    final = vdir / "demo_borrador.mp4"
    assert final.exists()
    for extra in ("demo_borrador.srt", "demo_borrador_portada.jpg", "plan_usado.json", "config_usada.json",
                  "resumen.json"):
        assert (vdir / extra).exists(), extra
    assert not (vdir / "_capa").exists() and not (vdir / "_sfx.wav").exists()

    plan_usado, cfg_usada = leer_json(vdir / "plan_usado.json"), leer_json(vdir / "config_usada.json")
    tl = linea_de_tiempo(plan_usado, cfg_usada)
    total = duracion_total(tl)
    assert 4.0 <= total <= 6.0
    assert [t.get("tipo") for t in tl] == [None, "polaroid", "collage"]
    resumen = leer_json(vdir / "resumen.json")
    assert resumen["resolucion"] == "540x960"
    assert resumen["duracion_seg"] == pytest.approx(total, abs=0.01)
    assert resumen["musica"]["archivo"] == "clics.wav"

    s = sonda(final)
    v = next(x for x in s["streams"] if x["codec_type"] == "video")
    a = next(x for x in s["streams"] if x["codec_type"] == "audio")
    assert (v["width"], v["height"]) == (540, 960)
    assert a["sample_rate"] == "48000"
    assert abs(float(s["format"]["duration"]) - total) <= 1 / 30 + 1e-3
    assert abs(float(v["duration"]) - total) <= 1 / 30 + 1e-3

    lufs = float(resumen["audio"]["despues_lufs"])
    assert lufs == pytest.approx(-14, abs=1.0)
    from motor_edicion.audio import medir_loudness
    assert float(medir_loudness(final, cfg_usada)["input_i"]) == pytest.approx(-14, abs=1.0)

    # efectos de sonido generados en <espacio>/sfx (obturador del polaroid, pop del collage, tecleo, whoosh)
    assert {p.stem for p in (espacio / "sfx").glob("*.wav")} >= {"obturador", "pop", "tecla", "whoosh"}
    assert resumen["efectos_sonido"] > 10
    assert resumen["textos"] == 2
    # segmentos en la caché del proyecto; fuente SDR → no hizo falta LUT
    assert list((d / "cache" / "segmentos").glob("polaroid_*.mkv"))
    assert list((d / "cache" / "segmentos").glob("collage_*.mkv"))
    assert not (espacio.parent / "cache_usuario").exists()
