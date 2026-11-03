"""Layered configuration: recipes/base.yaml → recipes/<type>.yaml → project settings.

Each layer only writes what it wants to change; everything else is inherited (deep merge).
After merging, `resolve()` applies the language setting:
  - `language: es` (default) | `en`
  - any value written as {es: …, en: …} becomes the value for the configured language
    (e.g. default keyword lists), and `transcription.language: null` follows `language`.
The language also names the version folders (v3SinMusica / v3NoMusic…) and output files.
"""
from __future__ import annotations

import copy

from .models import UserError

LANGUAGES = ("es", "en")
DEFAULT_LANGUAGE = "es"

# Platform export profiles: size, CRF and max bitrate (H.264 High, yuv420p, BT.709, AAC 48 kHz)
EXPORT_PROFILES = {
    "tiktok":  {"width": 1080, "height": 1920, "crf": 17, "maxrate": "20M"},
    "reels":   {"width": 1080, "height": 1920, "crf": 17, "maxrate": "15M"},
    "shorts":  {"width": 1080, "height": 1920, "crf": 17, "maxrate": "20M"},
    "youtube": {"width": 1920, "height": 1080, "crf": 18, "maxrate": "16M"},
    "landscape": {"width": 1920, "height": 1080, "crf": 18, "maxrate": "16M"},   # 16:9, any platform
    "square":  {"width": 1080, "height": 1080, "crf": 17, "maxrate": "15M"},
}
# Other names accepted for a profile (sve render --profile horizontal)
PROFILE_ALIASES = {"horizontal": "landscape"}

# Names of version folders and output files, per language
_LABELS = {
    "es": {"draft": "Borrador", "no_music": "SinMusica", "failed": "_fallida",
           "file_draft": "_borrador", "file_no_music": "_sin_musica", "cover": "_portada",
           "listen": "escuchar", "profiles": {"square": "cuadrado", "landscape": "horizontal"}},
    "en": {"draft": "Draft", "no_music": "NoMusic", "failed": "_failed",
           "file_draft": "_draft", "file_no_music": "_no_music", "cover": "_cover",
           "listen": "listen", "profiles": {}},
}


def deep_merge(base: dict, over: dict | None) -> dict:
    """Deep merge: dicts are combined, everything else is replaced. Inputs are never mutated."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _is_localized(v) -> bool:
    return isinstance(v, dict) and bool(v) and set(v) <= set(LANGUAGES)


def _localize(v, language: str):
    if _is_localized(v):
        for lang in (language, DEFAULT_LANGUAGE, *v):
            if lang in v:
                return copy.deepcopy(v[lang])
    if isinstance(v, dict):
        return {k: _localize(x, language) for k, x in v.items()}
    if isinstance(v, list):
        return [_localize(x, language) for x in v]
    return v


def resolve(cfg: dict) -> dict:
    """Apply the language setting to a merged configuration (returns a new dict)."""
    language = cfg.get("language") or DEFAULT_LANGUAGE
    if language not in LANGUAGES:
        raise UserError(f"Unknown language '{language}'. Options: {', '.join(LANGUAGES)}")
    out = _localize(cfg, language)
    out["language"] = language
    tr = out.get("transcription")
    if isinstance(tr, dict) and not tr.get("language"):
        tr["language"] = language
    return out


def canonical_profile(name: str | None) -> str | None:
    """Export profile name with aliases resolved ('horizontal' → 'landscape')."""
    return PROFILE_ALIASES.get(name, name) if name else name


def orientation(W: int, H: int) -> str:
    """'landscape' when wider than tall, 'square' when equal, else 'portrait'.

    Taken from the output size only, so a project gets it from `output.width/height` alone.
    """
    return "landscape" if W > H else "square" if W == H else "portrait"


def is_landscape(W: int, H: int) -> bool:
    return W > H


def labels(language: str) -> dict:
    return _LABELS.get(language, _LABELS[DEFAULT_LANGUAGE])


def profile_label(language: str, profile: str) -> str:
    return labels(language)["profiles"].get(profile, profile)


def variant_suffix(language: str, draft: bool = False, profile: str | None = None,
                   no_music: bool = False) -> str:
    """Suffix of a variant version folder: es → 'Borrador', 'Youtube', 'SinMusica'; en → 'Draft', 'NoMusic'."""
    lb = labels(language)
    return ((lb["draft"] if draft else "") + (profile_label(language, profile).capitalize() if profile else "")
            + (lb["no_music"] if no_music else ""))


def output_name(name: str, language: str, draft: bool = False, profile: str | None = None,
                no_music: bool = False) -> str:
    """Base name of the rendered files (video, .srt, cover)."""
    lb = labels(language)
    return (name + (lb["file_draft"] if draft else "") + (f"_{profile_label(language, profile)}" if profile else "")
            + (lb["file_no_music"] if no_music else ""))
