"""Splits text into visible units: single characters or whole emoji.

A composed emoji counts as ONE unit, so the typewriter effect never shows half an emoji:
ZWJ sequences (👩‍👩‍👧), variation selector (❤️), skin tones (👍🏽), flags (🇲🇽) and tag
sequences (subdivision flags).
"""
from __future__ import annotations

import re

_VS16, _ZWJ = "️", "‍"
_RI = "\U0001F1E6-\U0001F1FF"                 # regional indicators (flags are pairs)
_MOD = "\U0001F3FB-\U0001F3FF"                # skin tone modifiers
_TAGS = "\U000E0020-\U000E007F"               # tag sequences (🏴 subdivision flags)
_BASE = "\U0001F000-\U0001F1E5\U0001F200-\U0001F3FA\U0001F400-\U0001FAFF☀-➿⬀-⯿⌀-⏿"
_ELEMENT = f"[{_BASE}]{_VS16}?[{_MOD}]?[{_TAGS}]*"
_EMOJI = re.compile(f"[{_RI}]{{2}}|{_ELEMENT}(?:{_ZWJ}{_ELEMENT})*|[{_RI}{_MOD}]")


def units(text: str) -> list[str]:
    """Visible units: single characters or complete emoji."""
    out, i = [], 0
    for m in _EMOJI.finditer(text):
        out += list(text[i:m.start()])
        out.append(m.group())
        i = m.end()
    out += list(text[i:])
    return [u for u in out if u not in (_VS16, _ZWJ)]


def is_emoji(u: str) -> bool:
    return bool(_EMOJI.fullmatch(u))


def clean(text: str) -> str:
    """Collapses whitespace."""
    return re.sub(r"\s+", " ", text).strip()
