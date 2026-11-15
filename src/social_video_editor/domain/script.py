"""A narration script (script.md): what the presenter says, sentence by sentence, with cues that
say what is on screen meanwhile.

    # lines starting with # are comments
    How does a message travel? It goes further than expected.
    [bars title="Where messages travel" data="Under the sea:95, By satellite:5" unit="%"]
    Every time you send one, the phone cuts it into packets.
    [camera]
    And they travel apart.

A line wrapped in [ ] is a cue: it holds for the sentences after it, until the next cue. Without a
cue the presenter is on camera. A cue is written like a graphic cue (`template key="value" …`,
domain/graphics/spec.py); besides the graphic templates there are `camera`, `photo file="…"` and
`broll query="…"`. Any cue takes `seconds=N`: at most that long on screen.

`[short name]` … `[/short]` (Spanish `[corto name]` … `[/corto]`) wrap the sentences of a short: a
vertical video of its own made from the same recording (domain/shorts.py). They are not cues: the
cue in force and everything else stay as without them.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from .graphics.spec import parse_cue
from .models import PlanError

CAMERA, GRAPHIC, PHOTO, BROLL = "camera", "graphic", "photo", "broll"
PLAIN_KINDS = (CAMERA, PHOTO, BROLL)          # cue names that are not graphic templates
SHORT_MARKS = ("short", "corto")              # [short name] … [/short]: the sentences of a short
_SHORT_OPEN = re.compile(r"^(?:short|corto)(?:\s+(.*))?$", re.IGNORECASE)
_SHORT_CLOSE = re.compile(r"^/\s*(?:short|corto)$", re.IGNORECASE)
_SHORT_NAME = re.compile(r"^(?:name|nombre)\s*=\s*[\"'«“]?(.*?)[\"'»”]?$", re.IGNORECASE)

_SENTENCE = re.compile(r".+?(?:[.?!…]+[\"'»”)\]]*(?=\s|$)|$)")
_DIGIT_SEPARATOR = re.compile(r"(?<=\d)[.,](?=\d)")
_WORD = re.compile(r"[^\W_]+")


@dataclass
class Cue:
    """What is on screen from a sentence on. kind: camera | graphic | photo | broll."""

    index: int
    kind: str
    spec: dict                       # the cue's keys (a graphic's "template" included), without "seconds"
    line: int
    seconds: float | None = None     # at most this long on screen (None: until the next cue)


@dataclass
class Sentence:
    index: int
    text: str
    cue: int | None                  # index of the cue it is said under (None: on camera)
    line: int


@dataclass
class ShortMark:
    """A short written in the script: sentences `first` to `last` (inclusive)."""

    name: str
    first: int
    last: int
    line: int


@dataclass
class Script:
    sentences: list[Sentence] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)
    shorts: list[ShortMark] = field(default_factory=list)

    def cue_sentences(self, cue: int) -> list[int]:
        """Indices of the sentences said under a cue."""
        return [s.index for s in self.sentences if s.cue == cue]


def tokens(text: str) -> list[str]:
    """The words of a text for comparing: lower case, no accents, no punctuation ("1.000" → "1000")."""
    plain = unicodedata.normalize("NFKD", str(text).lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return _WORD.findall(_DIGIT_SEPARATOR.sub("", plain))


def split_sentences(text: str) -> list[str]:
    """Sentences of one line, each keeping its closing sign (. ? ! …). "2.5" is not a sentence end."""
    return [m.group().strip() for m in _SENTENCE.finditer(text.strip()) if tokens(m.group())]


def parse_script(text: str, translate: Callable[[dict], dict] | None = None,
                 templates: set[str] | None = None) -> Script:
    """Reads a script. translate: turns a cue written with other names into the engine's (Spanish
    aliases: "barras" → "bars"). templates: the graphic templates that exist (an unknown cue name
    is then an error here, with its line, instead of at render time)."""
    script = Script()
    current: int | None = None
    short: tuple[str, int, int] | None = None          # the short open now: (name, first sentence, line)
    for number, raw in enumerate(str(text).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]") and _short_mark(line[1:-1].strip()) is not None:
            short = _short(script, short, line[1:-1].strip(), number)
            continue
        if line.startswith("[") and line.endswith("]"):
            cue = _cue(line[1:-1], number, len(script.cues), translate, templates)
            if current is not None and script.cues[current].kind != CAMERA and not script.cue_sentences(current):
                raise PlanError(f"Script line {script.cues[current].line}: the cue has no sentence after it "
                                "(a cue shows while the next sentences are said)")
            script.cues.append(cue)
            current = cue.index
            continue
        for sentence in split_sentences(line):
            on_camera = current is None or script.cues[current].kind == CAMERA
            script.sentences.append(Sentence(len(script.sentences), sentence, None if on_camera else current, number))
    if current is not None and script.cues[current].kind != CAMERA and not script.cue_sentences(current):
        raise PlanError(f"Script line {script.cues[current].line}: the cue has no sentence after it "
                        "(a cue shows while the next sentences are said)")
    if not script.sentences:
        raise PlanError("The script has no sentences (write what is said, one or more sentences per line)")
    if short is not None:                       # a short left open runs to the end of the script
        _close_short(script, short, None)
    return script


def short_name(text: str) -> str:
    """A short's name as used in folders and on the command line: lower case, no accents, words
    joined by "-" ("Memoria RAM" → "memoria-ram")."""
    plain = unicodedata.normalize("NFKD", str(text).lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return "-".join(_WORD.findall(plain))


def _short_mark(body: str) -> str | None:
    """"open" / "close" for a short's mark ([short name], [/short]), None for any other cue."""
    if _SHORT_CLOSE.match(body):
        return "close"
    return "open" if _SHORT_OPEN.match(body) else None


def _short(script: Script, short, body: str, line: int):
    """Opens or closes a short at a mark; → the short open after it (None when closed)."""
    if _short_mark(body) == "close":
        if short is None:
            raise PlanError(f"Script line {line}: [/short] without a [short name] before it")
        _close_short(script, short, line)
        return None
    if short is not None:
        raise PlanError(f"Script line {line}: a short starts before the one of line {short[2]} ends "
                        "(close it with [/short] first; shorts do not overlap)")
    raw = (_SHORT_OPEN.match(body).group(1) or "").strip()
    named = _SHORT_NAME.match(raw)
    name = short_name(named.group(1) if named else raw)
    if not name:
        raise PlanError(f"Script line {line}: a short needs a name: [short memory] … [/short]")
    if any(s.name == name for s in script.shorts):
        raise PlanError(f"Script line {line}: there is already a short called '{name}'")
    return name, len(script.sentences), line


def _close_short(script: Script, short, line: int | None) -> None:
    name, first, opened = short
    last = len(script.sentences) - 1
    if last < first:
        raise PlanError(f"Script line {line or opened}: the short '{name}' has no sentences")
    script.shorts.append(ShortMark(name, first, last, opened))


def _cue(body: str, line: int, index: int, translate, templates) -> Cue:
    try:
        spec = parse_cue(body)
    except PlanError as e:
        raise PlanError(f"Script line {line}: {e}") from None
    if translate is not None:
        spec = translate(spec)
    name = str(spec.get("template") or "").lower()
    seconds = spec.pop("seconds", None)
    if seconds is not None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = -1.0
        if seconds <= 0:
            raise PlanError(f"Script line {line}: seconds must be a number above 0 (in [{body}])")
    if name in PLAIN_KINDS:
        spec.pop("template")
        if name == PHOTO and not spec.get("file"):
            raise PlanError(f"Script line {line}: a photo cue needs file=\"…\" (in [{body}])")
        if name == BROLL and not (spec.get("query") or spec.get("file")):
            raise PlanError(f"Script line {line}: a broll cue needs query=\"…\" or file=\"…\" (in [{body}])")
        return Cue(index, name, spec, line, seconds)
    if templates is not None and name not in templates:
        options = ", ".join([*PLAIN_KINDS, *sorted(templates)])
        raise PlanError(f"Script line {line}: unknown cue '{name}'. Options: {options}")
    return Cue(index, GRAPHIC, spec, line, seconds)
