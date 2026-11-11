"""Animated graphics (bars, big numbers, product cards, titles, headlines, lists, captions,
diagrams) described as data and drawn locally.

    domain/graphics/
      spec.py       reading a cue / plan shot (pairs, lists, numbers)
      theme.py      the look as data (colors, fonts, strokes, printed effects)
      scene.py      Stage, Scene and the registries of templates and elements
      canvas.py     the canvas interface templates draw on (+ a recording canvas for tests)
      paths.py      vector paths as data
      motion.py     timing curves
      kit.py        shared drawing helpers (house style)
      templates.py  the built-in templates

Packs (workspace `packs/<name>/`) add a theme and may register their own templates and elements:

    from social_video_editor.domain.graphics import element, template, Scene, kit, paths, motion
"""
from . import kit, motion, paths, templates  # noqa: F401  (templates registers the built-in ones)
from .canvas import Canvas, RecordingCanvas, fake_measure, saved
from .scene import ELEMENTS, TEMPLATES, Scene, Stage, element, make_scene, template
from .spec import parse_cue
from .theme import DEFAULT_THEME, build_theme

__all__ = ["Canvas", "DEFAULT_THEME", "ELEMENTS", "RecordingCanvas", "Scene", "Stage", "TEMPLATES", "build_theme",
           "element", "fake_measure", "kit", "make_scene", "motion", "parse_cue", "paths", "saved", "template"]
