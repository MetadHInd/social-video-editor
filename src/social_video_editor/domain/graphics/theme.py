"""The look of the graphics as data: colors, fonts, strokes and which "printed" effects are on.

The default theme is a plain dark stage (flat shapes, no outlines, no grain). A pack overrides any
part of it (packs/<name>/pack.yaml → theme) and a project can override both (graphics.theme).
"""
from __future__ import annotations

import copy

from ..config import deep_merge

DEFAULT_THEME: dict = {
    "colors": {
        "bg": "#0B0B0D",          # stage
        "grid": "#16171B",        # grid lines over the stage
        "paper": "#F4F4F0",       # light text and light boxes
        "ink": "#0B0B0D",         # outlines and text on paper
        "muted": "#8C8C88",       # secondary text, bars that are not the point
        "accent": "#C6F432",      # the value that matters
        "alert": "#FF4D6D",       # negatives (a cross in a list)
        "shadow": "#000000",      # halftone shadows
        "ghost_a": "#2F7BFF",     # the two color ghosts of a text that prints
        "ghost_b": "#FF48B0",
        "trace": "#1D1E24",       # circuit traces and other scenery drawn on the stage
        "device": "#2A2C35",      # dark bodies of illustrated devices (phone, chip…)
        "metal": "#D1A545",       # connector pins
    },
    "inks": {},                   # named colors with a fixed meaning: {"green": "#00A95C"}; color="green"
    "accent_ink": None,           # name (in inks) of the default accent, for its registration offset
    "registration": {},           # ink name → [dx, dy] px (at 1080): fills print slightly off their outline
    "fonts": {"display": None, "number": None, "label": None},      # font file names or paths; None = a bold sans
    "stroke": {"outline": 0.0, "halo": 0.0, "thread": 5.0, "thread_halo": 0.0},   # px at 1080; halos are light lines around ink
    "halftone": {"cell": 11, "angle": 45, "radius": 2.4, "shadow_offset": 0},     # offset 0 = no shadows
    "grid": {"cell": 96, "width": 2},                                # cell 0 = no grid
    "grain": {"strength": 0.0, "fps": 15},
    "motion": {"on_twos": False, "boil": False, "print_in": False, "ambient": True},
    # scenery behind every graphic: circuit traces with pulses of data, small chips, twinkling bits; and
    # the sparkles / glow templates put around their subject. 0 turns a kind off.
    "decor": {"traces": 7, "chips": 2, "bits": 14, "sparkles": 4, "glow": True},
    "icon_inks": {},              # illustration name → ink name (phone: blue): the color it gets by default
    "poster": {"extrude": 0, "outline": 0.0, "tilt": 0.0},           # big titles: extrusion steps, ink border, degrees
    "box": {"tilts": [0.0, 0.0], "radius": 10},                      # text boxes: alternate tilts, corner radius
    "sticker": {"border": 0.0},                                      # die-cut border around product photos
    # a graphic grows up to `max`× (vertical: `max_vertical`×) while it fits in `fill` of the safe height
    "scale": {"max": 1.8, "max_vertical": 2.6, "fill": 0.94},
}


def build_theme(*overrides: dict | None) -> dict:
    """DEFAULT_THEME with each override merged on top, in order."""
    theme = copy.deepcopy(DEFAULT_THEME)
    for o in overrides:
        if o:
            theme = deep_merge(theme, o)
    return theme


def ink(theme: dict, name: str | None) -> tuple[str, str | None]:
    """(hex color, ink name or None) for a color given as an ink name, a theme color name or "#RRGGBB".
    None → the accent."""
    if not name:
        accent = theme.get("accent_ink")
        if accent and accent in theme["inks"]:
            return theme["inks"][accent], accent
        return theme["colors"]["accent"], None
    name = str(name)
    if name in theme["inks"]:
        return theme["inks"][name], name
    if name in theme["colors"]:
        return theme["colors"][name], None
    if name.startswith("#") and len(name) in (7, 9):
        return name, None
    raise ValueError(f"unknown color '{name}' (inks: {', '.join(theme['inks']) or 'none'}; or #RRGGBB)")


def color(theme: dict, name: str) -> str:
    """A theme color by name, or the value itself when it is already "#RRGGBB"."""
    return name if name.startswith("#") else theme["colors"][name]
