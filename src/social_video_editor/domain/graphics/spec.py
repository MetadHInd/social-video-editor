"""Reading what a graphic was asked to show: cue text, label/value pairs, lists and numbers.

A cue is one line: `bars title="Battery" data="Cold start:100, Asleep:20" unit="%"` — the template
name, then key="value" pairs. In plan.json the same thing is a shot:
{"type": "graphic", "template": "bars", "title": "Battery", "data": [["Cold start", 100], ["Asleep", 20]]}.
"""
from __future__ import annotations

import re
import shlex

from ..models import PlanError

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def parse_cue(text: str) -> dict:
    """`template key="value" key=value …` → {"template": …, key: value}. Values stay strings."""
    try:
        parts = shlex.split(text.strip())
    except ValueError as e:
        raise PlanError(f"Graphic cue not understood ({e}): {text}") from None
    if not parts:
        raise PlanError("Empty graphic cue")
    spec = {"template": parts[0].lower()}
    for part in parts[1:]:
        key, sep, value = part.partition("=")
        if not sep or not key:
            raise PlanError(f"Graphic cue: '{part}' is not key=\"value\" (in: {text})")
        spec[key.strip().lower()] = value.strip()
    return spec


def split_list(value) -> list[str]:
    """A list as given, or a string split on ";" (or "|"), else on ","."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value]
    text = str(value)
    sep = next((s for s in (";", "|") if s in text), ",")
    return [p.strip() for p in text.split(sep) if p.strip()]


def pairs(value) -> list[tuple[str, str]]:
    """(label, value) pairs from "A:15, B:40" (use ";" between pairs when a value has a comma),
    a dict, or a list of [label, value] / {"label": …, "value": …}."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [(str(k), str(v)) for k, v in value.items()]
    out = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                out.append((str(item.get("label", "")), str(item.get("value", ""))))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                out.append((str(item[0]), str(item[1])))
            else:
                out += pairs(str(item))
        return out
    for part in split_list(value):
        label, sep, val = part.partition(":")
        out.append((label.strip(), val.strip()) if sep else ("", part))
    return out


def number(value) -> float | None:
    """The first number in the value ("40 %" → 40.0, "2.5" → 2.5); None when there is none.
    Write numbers plainly (14900, 2.5): they are formatted for the language when shown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUMBER.search(str(value))
    return float(m.group()) if m else None


def decimals_of(value) -> int:
    """How many decimals a written number has ("2.50" → 2, 40 → 0)."""
    m = _NUMBER.search(str(value))
    return len(m.group().partition(".")[2]) if m else 0


def format_number(v: float, language: str = "es", decimals: int = 0) -> str:
    """1234567.5 → "1.234.567,5" (es) / "1,234,567.5" (en)."""
    text = f"{v:,.{max(decimals, 0)}f}"
    if language == "en":
        return text
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def required(spec: dict, key: str, template: str):
    if spec.get(key) in (None, "", []):
        raise PlanError(f"Graphic '{template}' needs '{key}'")
    return spec[key]


def seconds(spec: dict, key: str, default: float) -> float:
    try:
        return float(spec.get(key) or default)
    except (TypeError, ValueError):
        raise PlanError(f"Graphic: '{key}' must be a number of seconds, not {spec.get(key)!r}") from None
