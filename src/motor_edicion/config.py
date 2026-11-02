"""Configuración en capas: recetas/base.yaml → recetas/<tipo>.yaml → proyecto.yaml (ajustes).

Cada capa solo escribe lo que quiere cambiar; el resto se hereda de la capa anterior.
Una receta se busca primero en <espacio>/recetas/<nombre>.yaml y si no, en las del paquete.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .util import RECETAS_PAQUETE, espacio


def carpetas_recetas() -> list[Path]:
    """Dónde se buscan recetas, en orden de prioridad."""
    return [espacio() / "recetas", RECETAS_PAQUETE]


def ruta_receta(nombre: str) -> Path | None:
    for d in carpetas_recetas():
        if (d / f"{nombre}.yaml").exists():
            return d / f"{nombre}.yaml"
    return None


def recetas_disponibles() -> dict[str, Path]:
    """nombre → ruta (una receta del espacio tapa a la del paquete con el mismo nombre)."""
    out = {}
    for d in reversed(carpetas_recetas()):
        for p in d.glob("*.yaml"):
            out[p.stem] = p
    return dict(sorted(out.items()))


def mezclar(base: dict, encima: dict) -> dict:
    """Mezcla profunda: los diccionarios se combinan, lo demás se reemplaza."""
    out = copy.deepcopy(base)
    for k, v in (encima or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = mezclar(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def leer_yaml(ruta: Path) -> dict:
    return yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}


def cargar_receta(nombre: str) -> dict:
    cfg = leer_yaml(ruta_receta("base"))
    if nombre and nombre != "base":
        ruta = ruta_receta(nombre)
        if ruta is None:
            disponibles = ", ".join(recetas_disponibles())
            raise SystemExit(f"No existe la receta '{nombre}'. Disponibles: {disponibles}")
        receta = leer_yaml(ruta)
        cfg = mezclar(cfg, receta)
    return cfg


class Proyecto:
    """Un video a producir: sus fuentes, su receta y su carpeta de trabajo."""

    def __init__(self, carpeta: str | Path):
        self.dir = Path(carpeta).expanduser().resolve()
        archivo = self.dir / "proyecto.yaml"
        if not archivo.exists():
            raise SystemExit(f"Falta {archivo}. Crea uno con: motor nuevo {self.dir.name}")
        self.datos = leer_yaml(archivo)
        self.nombre = self.datos.get("nombre", self.dir.name)
        self.receta = self.datos.get("receta", "base")
        self.cfg = mezclar(cargar_receta(self.receta), self.datos.get("ajustes", {}))
        self.fuentes = []
        for f in self.datos.get("fuentes", []):
            f = {"ruta": f} if isinstance(f, str) else dict(f)
            f["ruta"] = str(Path(f["ruta"]).expanduser())
            if not Path(f["ruta"]).exists():
                raise SystemExit(f"No encuentro la fuente: {f['ruta']}")
            self.fuentes.append(f)
        self.cache = self.dir / "cache"
        self.cache.mkdir(exist_ok=True)

    @property
    def plan_path(self) -> Path:
        return self.dir / "plan.json"

    def nueva_version(self) -> Path:
        n = 1
        while (self.dir / f"v{n}").exists():
            n += 1
        d = self.dir / f"v{n}"
        d.mkdir()
        return d

    def carpeta_variante(self, sufijo: str) -> Path:
        """Variante de la última versión con el mismo número: v3 → v3SinMusica, v3Borrador, v3Youtube…

        Si esa variante ya existe, se agrega un contador (v3SinMusica_2) para no borrar nada.
        """
        base = self.ultima_version()
        n = int(base.name[1:]) if base else 1
        d = self.dir / f"v{n}{sufijo}"
        k = 2
        while d.exists():
            d = self.dir / f"v{n}{sufijo}_{k}"
            k += 1
        d.mkdir()
        return d

    def ultima_version(self, incluir_variantes: bool = False) -> Path | None:
        """Última versión principal (v1, v2…); con incluir_variantes, la carpeta más reciente de todas."""
        if incluir_variantes:
            todas = [p for p in self.dir.glob("v[0-9]*") if p.is_dir() and not p.name.endswith("_fallida")]
            return max(todas, key=lambda p: p.stat().st_mtime) if todas else None
        vs = sorted((p for p in self.dir.glob("v*") if p.is_dir() and p.name[1:].isdigit()),
                    key=lambda p: int(p.name[1:]))
        return vs[-1] if vs else None
