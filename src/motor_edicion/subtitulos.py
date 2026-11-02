"""Subtítulos y textos en pantalla, dibujados con Pillow y superpuestos como capa transparente.

- Subtítulos automáticos (si hay voz): grupos de 2-3 palabras, la palabra que se dice resaltada,
  animación "pop" al aparecer. Estilo en receta → subtitulos.
- Textos del plan (gancho, etiquetas, tarjetas, contador, CTA…). Estilos en receta → textos.estilos.
  Animaciones: "escribir" (letra a letra con cursor; si hay "descripcion", se escribe debajo después
  del título), "pop" (aparece con rebote) o "ninguna". Admite emojis a color.
Se genera una lista de imágenes con su duración (concat de FFmpeg) que se monta encima del video.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import texto_rico as tr

DIRS_FUENTES = [Path.home() / "Library/Fonts", Path("/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts")]
_cache_fuentes: dict = {}
POP = [(0.0, 0.80), (1 / 30, 1.10), (3 / 30, 1.0)]


def fuente(nombre: str, tam: int):
    """Busca una fuente por nombre de archivo (sin extensión). 'Nombre:2' = estilo 2 de un .ttc."""
    clave = (nombre, tam)
    if clave in _cache_fuentes:
        return _cache_fuentes[clave]
    base, _, idx = nombre.partition(":")
    cand = [Path(base).expanduser()] + [d / f"{base}{ext}" for d in DIRS_FUENTES for ext in (".otf", ".ttf", ".ttc")]
    cand.append(Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"))
    for c in cand:
        if c.exists() and c.is_file():
            f = ImageFont.truetype(str(c), tam, index=int(idx or 0))
            _cache_fuentes[clave] = f
            return f
    return ImageFont.load_default()


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()


def _color(c: str | None):
    if not c:
        return None
    c = c.lstrip("#")
    if len(c) == 6:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))


# ── imágenes ─────────────────────────────────────────────────────────────────

def img_subtitulo(palabras: list[str], activa: int, est: dict, W: int) -> Image.Image:
    f = fuente(est.get("fuente", "Inter-Black"), int(est["tam"]))
    borde = int(est.get("borde", 8))
    pal = [p.upper() if est.get("mayusculas", True) else p for p in palabras]
    esp = f.getlength(" ")
    lineas, actual = [], []
    for k, p in enumerate(pal):   # reparto en líneas por palabras
        prueba = actual + [k]
        if actual and sum(f.getlength(pal[j]) for j in prueba) + esp * (len(prueba) - 1) > W * 0.86:
            lineas.append(actual)
            actual = [k]
        else:
            actual = prueba
    lineas.append(actual)
    asc, desc = f.getmetrics()
    alto_l = asc + desc
    ancho = int(max(sum(f.getlength(pal[j]) for j in ids) + esp * (len(ids) - 1) for ids in lineas)) + 2 * borde + 20
    alto = alto_l * len(lineas) + 2 * borde + 24
    img = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(sombra)
    for li, ids in enumerate(lineas):
        lw = sum(f.getlength(pal[k]) for k in ids) + esp * (len(ids) - 1)
        x, y = (ancho - lw) / 2, borde + 6 + li * alto_l
        for k in ids:
            col = _color(est["resalte"]) if k == activa else _color(est["color"])
            if est.get("sombra", True):
                ds.text((x, y + 7), pal[k], font=f, fill=(0, 0, 0, 160), stroke_width=borde, stroke_fill=(0, 0, 0, 160))
            d.text((x, y), pal[k], font=f, fill=col, stroke_width=borde, stroke_fill=_color(est["borde_color"]))
            x += f.getlength(pal[k]) + esp
    if est.get("sombra", True):
        sombra = sombra.filter(ImageFilter.GaussianBlur(6))
        sombra.alpha_composite(img)
        return sombra
    return img


class _Bloque:
    """Un párrafo (título o descripción) ya repartido en líneas con la frase COMPLETA."""

    def __init__(self, texto: str, nombre_fuente: str, tam: int, color: str, max_px: float, mayus: bool,
                 interlineado: float = 1.05):
        t = limpiar(texto)
        self.t = t.upper() if mayus else t
        self.f, self.tam, self.color = fuente(nombre_fuente, tam), tam, _color(color)
        self.lineas = tr.partir(self.t, self.f, tam, max_px) if self.t else []
        asc, desc = self.f.getmetrics()
        self.asc, self.alto_l = asc, int((asc + desc) * interlineado)
        self.n = sum(len(ln) for ln in self.lineas)
        self.ancho = max((tr.ancho(ln, self.f, tam) for ln in self.lineas), default=0)
        self.alto = self.alto_l * len(self.lineas)

    def dibujar(self, img, cx: float, y0: float, visibles: int, cursor: bool, borde: int = 0):
        """Dibuja las primeras 'visibles' unidades centradas en cx. Cursor tras la última visible."""
        resto, pos_cursor = visibles, None
        for i, ln in enumerate(self.lineas):
            x = cx - tr.ancho(ln, self.f, self.tam) / 2
            y = y0 + i * self.alto_l
            parte = ln[:max(resto, 0)]
            xf = tr.dibujar(img, x, y, parte, self.f, self.tam, self.color, borde) if parte else x
            if resto <= len(ln) and pos_cursor is None:
                pos_cursor = (xf, y)
            resto -= len(ln)
        if cursor and pos_cursor:
            x, y = pos_cursor
            ImageDraw.Draw(img).rectangle([x + 3, y + self.asc * 0.12, x + 3 + max(self.tam * 0.07, 3),
                                           y + self.asc * 1.08], fill=self.color)


def _bloques(tx: dict, est: dict, W: int, fuente_def: str):
    max_px = W * float(est.get("max_ancho", 0.84)) - 2 * est["tam"] * 0.45
    tit = _Bloque(tx["texto"], est.get("fuente", fuente_def), est["tam"], est.get("color", "#FFFFFF"),
                  max_px, est.get("mayusculas", False))
    des = None
    if tx.get("descripcion"):
        tam_d = int(est.get("desc_tam", est["tam"] * 0.72))
        des = _Bloque(tx["descripcion"], est.get("desc_fuente", "Bradley Hand Bold"), tam_d,
                      est.get("desc_color", est.get("color", "#FFFFFF")), max_px, False, 1.0)
    return tit, des


def img_texto(tit: "_Bloque", des: "_Bloque | None", est: dict, n_t: int, n_d: int, cursor: str | None) -> Image.Image:
    """Caja (o texto con borde) con título y descripción opcional; n_t/n_d = unidades visibles."""
    tam = est["tam"]
    pad_x, pad_y = int(tam * 0.45), int(tam * 0.28)
    sep = int(tam * 0.18) if des else 0
    ancho = int(max(tit.ancho, des.ancho if des else 0)) + 2 * pad_x
    alto = tit.alto + (des.alto + sep if des else 0) + 2 * pad_y
    m = 16
    img = Image.new("RGBA", (ancho + 2 * m, alto + 2 * m), (0, 0, 0, 0))
    fondo = _color(est.get("fondo"))
    radio = int(tam * float(est.get("radio", 0.3)))
    if fondo:
        sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sombra).rounded_rectangle([m, m + 6, m + ancho, m + 6 + alto], radius=radio, fill=(0, 0, 0, 70))
        img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(9)))
        ImageDraw.Draw(img).rounded_rectangle([m, m, m + ancho, m + alto], radius=radio, fill=fondo,
                                              outline=_color(est.get("contorno")), width=3 if est.get("contorno") else 0)
    borde = 0 if fondo else max(int(tam / 10), 4)
    cx = m + ancho / 2
    y = m + pad_y - int(tit.asc * 0.08)
    tit.dibujar(img, cx, y, n_t, cursor == "t", borde)
    if des:
        des.dibujar(img, cx, y + tit.alto + sep, n_d, cursor == "d", 0 if fondo else max(borde // 2, 3))
    return img


# ── elementos animados ──────────────────────────────────────────────────────

class Elemento:
    """Algo que aparece en pantalla entre ini y fin; sabe qué imagen mostrar en cada instante."""

    def __init__(self, ini, fin, x, y, pop, imagenes_fn, cortes, clave):
        self.ini, self.fin, self.x, self.y, self.pop = ini, fin, x, y, pop
        self._fn, self._cortes, self.clave = imagenes_fn, cortes, clave

    def cortes(self):
        c = {self.ini, self.fin, *self._cortes}
        if self.pop:
            c.update(self.ini + dt for dt, _ in POP)
        return c

    def estado(self, t):
        return self._fn(t)


def _elemento_texto(tx: dict, est: dict, W: int, H: int, fuente_def: str, idx: int, teclas: list):
    tit, des = _bloques(tx, est, W, fuente_def)
    anim = tx.get("animacion", est.get("animacion", "pop"))
    ini, fin = tx["ini"], tx["fin"]
    cache = {}

    def imagen(n_t, n_d, cur):
        k = (n_t, n_d, cur)
        if k not in cache:
            cache[k] = img_texto(tit, des, est, n_t, n_d, cur)
        return cache[k]

    cortes = []
    if anim == "escribir":
        v = float(tx.get("velocidad", est.get("velocidad", 30)))
        n_total = tit.n + (des.n if des else 0)
        disponible = max(fin - ini - 0.35, 0.3)
        v = max(v, n_total / (0.75 * disponible))      # que siempre termine con tiempo para leer
        t0 = ini + 0.04
        tiempos_t = [t0 + (k + 1) / v for k in range(tit.n)]
        t1 = (tiempos_t[-1] if tiempos_t else t0) + (0.14 if des else 0)
        tiempos_d = [t1 + (k + 1) / (v * 1.05) for k in range(des.n)] if des else []
        fin_escritura = (tiempos_d or tiempos_t or [t0])[-1]
        parpadeo = [fin_escritura + 0.38 * (j + 1) for j in range(int((fin - fin_escritura) / 0.38))]
        cortes = tiempos_t + tiempos_d + parpadeo
        unidades_t = [u for ln in tit.lineas for u in ln]
        unidades_d = [u for ln in des.lineas for u in ln] if des else []
        teclas += [t for t, u in zip(tiempos_t, unidades_t) if u != " "]
        teclas += [t for t, u in zip(tiempos_d, unidades_d) if u != " "]

        def fn(t):
            n_t = sum(1 for x in tiempos_t if x <= t)
            n_d = sum(1 for x in tiempos_d if x <= t)
            escribiendo = t < fin_escritura
            if escribiendo:
                cur = "t" if (n_t < tit.n or not des) else "d"
            else:
                cur = ("d" if des else "t") if int((t - fin_escritura) / 0.38) % 2 == 1 else None
            return (f"t{idx}_{n_t}_{n_d}_{cur}", imagen(n_t, n_d, cur))
    else:
        def fn(t):
            return (f"t{idx}", imagen(tit.n, des.n if des else 0, None))
    return Elemento(ini, fin, float(est.get("x", 0.5)) * W, float(est.get("y", 0.5)) * H,
                    anim in ("pop", "escribir") and tx.get("pop", True), fn, cortes, f"t{idx}")


# ── construcción de la capa ─────────────────────────────────────────────────

def grupos_subtitulos(analisis: list[dict], tl: list[dict], cfg: dict) -> list[dict]:
    """Palabras de todas las fuentes → grupos de 2-3 palabras en tiempos del video final."""
    s = cfg["subtitulos"]
    pal = []
    for tm in tl:   # cada palabra pertenece a UNA toma y debe caber entera en ella
        if tm.get("tipo"):
            continue
        v = float(tm.get("velocidad", 1.0))
        for w in analisis[tm["fuente"]].get("palabras", []):
            if w["ini"] < tm["ini"] - 0.02 or w["fin"] > tm["fin"] + 0.08:
                continue
            t0 = tm["out_ini"] + (w["ini"] - tm["ini"]) / v
            if tm["i"] > 0 and t0 < tm["out_ini"] + tm["solape"]:
                continue                                   # dentro de la transición de entrada
            t1 = tm["out_ini"] + (min(w["fin"], w["ini"] + 1.5) - tm["ini"]) / v
            pal.append({"p": limpiar(w["p"]), "ini": t0, "fin": max(t1, t0 + 0.12), "toma": tm["i"]})
    pal.sort(key=lambda w: w["ini"])
    grupos, actual = [], []
    for w in pal:
        if not w["p"]:
            continue
        txt = " ".join(x["p"] for x in actual + [w])
        corta = actual and (len(actual) >= s["max_palabras"] or len(txt) > s["max_caracteres"]
                            or w["toma"] != actual[-1]["toma"]
                            or w["ini"] - actual[-1]["fin"] > 0.5 or actual[-1]["p"][-1:] in ".?!,")
        if corta:
            grupos.append(actual)
            actual = []
        actual.append(w)
    if actual:
        grupos.append(actual)
    res = []
    for i, g in enumerate(grupos):
        sig = grupos[i + 1][0]["ini"] if i + 1 < len(grupos) else g[-1]["fin"] + 0.4
        res.append({"palabras": g, "ini": g[0]["ini"], "fin": min(g[-1]["fin"] + 0.15, sig)})
    return res


def construir_capa(textos: list[dict], grupos: list[dict], cfg: dict, W: int, H: int, total: float,
                   carpeta: Path) -> tuple[Path | None, list[float]]:
    """Genera las imágenes y la lista concat. Devuelve (ruta_lista, tiempos_de_cada_tecla)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    for viejo in carpeta.glob("*.png"):
        viejo.unlink()
    escala_w = W / 1080
    est_sub = {**cfg["subtitulos"], "tam": int(cfg["subtitulos"]["tam"] * escala_w),
               "borde": max(int(cfg["subtitulos"]["borde"] * escala_w), 2)}
    estilos = cfg["textos"]["estilos"]
    fuente_def = cfg["subtitulos"].get("fuente", "Inter-Black")
    elementos, teclas = [], []
    for gi, g in enumerate(grupos):
        palabras = [w["p"] for w in g["palabras"]]
        for k, w in enumerate(g["palabras"]):
            ini = g["ini"] if k == 0 else w["ini"]
            fin = g["palabras"][k + 1]["ini"] if k + 1 < len(g["palabras"]) else g["fin"]
            if fin <= ini:
                continue
            im = img_subtitulo(palabras, k, est_sub, W)
            elementos.append(Elemento(ini, fin, W / 2, float(est_sub["y"]) * H,
                                      k == 0 and est_sub.get("animacion") == "pop",
                                      (lambda t, c=f"s{gi}_{k}", im=im: (c, im)), [], f"s{gi}_{k}"))
    for ti, tx in enumerate(textos):
        est = dict(estilos.get(tx.get("estilo", "simple"), estilos["simple"]))
        est.update({k: v for k, v in tx.items() if k in ("tam", "color", "fondo", "x", "y", "mayusculas",
                                                         "max_ancho", "fuente", "desc_fuente", "desc_color")})
        est["tam"] = int(est["tam"] * escala_w)
        if "desc_tam" in est:
            est["desc_tam"] = int(est["desc_tam"] * escala_w)
        elementos.append(_elemento_texto(tx, est, W, H, fuente_def, ti, teclas))
    if not elementos:
        return None, []

    cortes = {0.0, total}
    for e in elementos:
        cortes.update(e.cortes())
    cortes = sorted(c for c in cortes if 0 <= c <= total)
    lista, cache_frames, escaladas = [], {}, {}
    for a, b in zip(cortes, cortes[1:]):
        if b - a < 1e-4:
            continue
        medio = (a + b) / 2
        activos = []
        for e in elementos:
            if e.ini <= medio < e.fin:
                esc = 1.0
                if e.pop:
                    for dt, s in POP:
                        if medio >= e.ini + dt:
                            esc = s
                clave, im = e.estado(medio)
                activos.append((clave, round(esc, 2), e.x, e.y, im))
        llave = tuple(x[:4] for x in activos)
        if llave not in cache_frames:
            lienzo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for clave, esc, x, y, im in activos:
                if esc != 1.0:
                    k2 = (clave, esc)
                    if k2 not in escaladas:
                        escaladas[k2] = im.resize((max(int(im.width * esc), 1), max(int(im.height * esc), 1)),
                                                  Image.LANCZOS)
                    im = escaladas[k2]
                x0 = int(min(max(x - im.width / 2, 0), W - im.width))
                y0 = int(min(max(y - im.height / 2, 0), H - im.height))
                lienzo.alpha_composite(im, (max(x0, 0), max(y0, 0)))
            ruta = carpeta / f"c{len(cache_frames):05d}.png"
            lienzo.save(ruta, compress_level=1)
            cache_frames[llave] = ruta
        lista.append((cache_frames[llave], b - a))
    lst = carpeta / "capa.ffconcat"
    lineas = ["ffconcat version 1.0"]
    for ruta, d in lista:
        lineas += [f"file '{ruta.name}'", f"duration {d:.5f}"]
    lineas.append(f"file '{lista[-1][0].name}'")
    lst.write_text("\n".join(lineas) + "\n")
    return lst, sorted(teclas)
