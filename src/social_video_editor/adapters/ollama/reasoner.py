"""The decider's local language model: closed questions answered by a model served by Ollama.

Plain HTTP to the local server (stdlib only; nothing is installed or loaded in this process). The
server is `OLLAMA_HOST` (default http://127.0.0.1:11434). Each question is one /api/chat request:
no thinking, temperature 0, and the answer constrained to the option letters with a JSON schema
(`format`). Some runners cannot constrain the output (the server answers 501 "structured output is
unavailable"): then the request is sent again without `format` and the letters are read from the
plain answer; options a runner does not support are remembered per model.

How sure the model is comes from the token log-probabilities (`logprobs`, `top_logprobs`) at the
token that holds the chosen letter. With the output constrained, the probability is renormalised over
the alternatives that are option letters; unconstrained it is used as it is (the rest of the mass is
the model wanting to say something else, e.g. "none of them"). Without log-probabilities the
confidence is None (the caller treats it as `llm_min`).

Sampling is greedy with no repetition penalties: some models ship a presence penalty, which would
count against the option letters already written in the prompt and change the answer.

A picture larger than IMAGE_MAX_SIDE pixels on its longest side is sent scaled down (a full-HD frame took
the model ≈16 s a question, the same frame at 960 pixels ≈4 s, with the same answers on the b-roll
questions compared).

The server on demand (`decider.llm_start`, on by default): when no server answers on this machine and
the `ollama` program is installed, ready() starts `ollama serve` itself and the process stops it when
it ends (after unloading the model). A server that was already running is never stopped, a server on
another machine (OLLAMA_HOST) is never started, and a server this process started is left running when
another program has a model loaded on it.

Nothing here ever raises for a server or model failure: choose() returns ([], None) and the rules'
answer stands.
"""
from __future__ import annotations

import atexit
import base64
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ...log import log

DEFAULT_HOST = "http://127.0.0.1:11434"
INSTALL_HINT = "brew install ollama && brew services start ollama"
TAGS_TIMEOUT = 0.5          # seconds: a local server answers at once
READY_TTL = 5.0             # seconds a ready() answer is reused
RELEASE_TIMEOUT = 10
START_TIMEOUT = 20.0        # seconds a server started here gets to answer
STOP_TIMEOUT = 5.0          # seconds it gets to end before it is killed
IDLE_WAIT = 2.0             # seconds the server gets to show no model loaded before it is stopped
LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]")
# where the program lives when it is not on PATH (apps started outside a login shell, macOS only)
MAC_PROGRAMS = ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama",
                "/Applications/Ollama.app/Contents/Resources/ollama")
NOT_RUNNING = "Ollama is not running"
IMAGE_MAX_SIDE = 960        # pixels: larger pictures are scaled down before they are sent
KEEP_ALIVE = "2m"           # between the questions of one batch; release() unloads at the end
# greedy, and no penalty for letters that are already in the prompt (a model's own defaults may set one)
OPTIONS = {"temperature": 0, "presence_penalty": 0, "frequency_penalty": 0, "repeat_penalty": 1,
           "num_predict": 16}                   # letters only: no room to explain (that took up to 30 s an answer)
# request fields a runner may not support → words of the error that name them
OPTIONAL_FIELDS = (("format", ("structured output", "format")), ("logprobs", ("logprob",)), ("think", ("think",)))

_ANSWER = re.compile(r'"answer"\s*:\s*(\[[^\]]*\]|"[^"]*")', re.S)
_STRING = re.compile(r'"\s*([^"]*?)\s*"')
_LETTER = re.compile(r"(?<![^\W\d_])([A-Za-z])(?![^\W\d_])")     # a letter standing alone (not in a word)
_STRIP = " \t\r\n\"'()[]{},.:;"


def host() -> str:
    """The Ollama server (OLLAMA_HOST; "host:port" without a scheme is accepted; 0.0.0.0 = this machine)."""
    raw = (os.environ.get("OLLAMA_HOST") or "").strip().rstrip("/") or DEFAULT_HOST
    if "://" not in raw:
        raw = "http://" + raw
    scheme, rest = raw.split("://", 1)
    netloc, _, path = rest.partition("/")
    if netloc.startswith("0.0.0.0"):
        netloc = "127.0.0.1" + netloc[len("0.0.0.0"):]
    if ":" not in netloc.split("]")[-1]:
        netloc += ":443" if scheme == "https" else ":11434"
    return f"{scheme}://{netloc}" + (f"/{path}" if path else "")


def offline() -> bool:
    return os.environ.get("SVE_OFFLINE", "").strip() not in ("", "0")


def local() -> bool:
    """Whether the server (OLLAMA_HOST) is on this machine: the only one the engine may start."""
    netloc = host().split("://", 1)[1].split("/", 1)[0]
    return netloc.rsplit(":", 1)[0].lower() in LOCAL_NAMES


def program() -> str | None:
    """Full path of the `ollama` program, None when it is not installed."""
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "darwin":
        return next((p for p in MAC_PROGRAMS if Path(p).is_file()), None)
    return None


def wants_start(settings: dict) -> bool:
    """decider.llm_start (a missing key = no: only a caller that asks for it gets a server started)."""
    value = settings.get("llm_start")
    return value is True or str(value).strip().lower() in ("true", "yes", "si", "sí", "1", "on", "auto")


def schema(keys: list[str], many: bool) -> dict:
    """JSON schema of the answer: one option letter, or a list of them."""
    letter = {"type": "string", "enum": list(keys)}
    answer = {"type": "array", "items": letter, "uniqueItems": True} if many else letter
    return {"type": "object", "properties": {"answer": answer}, "required": ["answer"]}


def letters(text: str, keys: list[str], many: bool) -> list[tuple[str, int]]:
    """The letters answered, each with its position in `text`: from {"answer": …} when the text holds
    it, else the letters standing alone (the first one, or those on the first line for `many`). Empty
    when one of them is not an option."""
    keys_set = {k.lower() for k in keys}
    m = _ANSWER.search(text)
    if m:
        found = [(s.group(1).strip(_STRIP).lower(), m.start(1) + s.start(1)) for s in _STRING.finditer(m.group(1))]
    else:
        start = len(text) - len(text.lstrip())
        end = text.find("\n", start)
        region = text[:end] if many and end >= 0 else text
        found = [(x.group(1).lower(), x.start(1)) for x in _LETTER.finditer(region)]
        word = region.strip().strip(_STRIP).lower()
        if many and not found and word and all(ch in keys_set or ch in ", ;" for ch in word):
            found = [(ch, region.find(ch)) for ch in word if ch in keys_set]      # letters written together: "ab"
        found = found if many else found[:1]
    out: list[tuple[str, int]] = []
    for letter, pos in found:
        if letter not in keys_set:
            return []
        if letter not in (x for x, _ in out):
            out.append((letter, pos))
    return out if (many or len(out) == 1) else []


def confidence(logprobs, chosen: list[tuple[str, int]], keys: list[str], many: bool,
               renormalise: bool = True) -> float | None:
    """How sure the model was of the letters chosen, from the response's `logprobs` (None without them).

    The tokens are joined back into the answer text; for each chosen letter the token that holds it is
    found, and its probability (with the alternatives that spell the same letter: " b", '"b' count as
    "b") is renormalised over that token's top alternatives that are option letters — or, with
    `renormalise` False (unconstrained output), taken as it is. Several letters → the mean."""
    if not logprobs or not chosen or not isinstance(logprobs, list):
        return None
    try:
        tokens = [str(t.get("token", "")) for t in logprobs]
        text = "".join(tokens)
        found = letters(text, keys, many)
        if [x for x, _ in found] != [x for x, _ in chosen]:
            return None
        starts, at = [], 0
        for tok in tokens:
            starts.append(at)
            at += len(tok)
        keys_set = {k.lower() for k in keys}
        values = []
        for letter, pos in found:
            j = max(i for i, s in enumerate(starts) if s <= pos)
            entry = logprobs[j]
            probs: dict[str, float] = {}
            for alt in entry.get("top_logprobs") or []:
                k = str(alt.get("token", "")).strip(_STRIP).lower()
                if k in keys_set:
                    probs[k] = probs.get(k, 0.0) + math.exp(float(alt["logprob"]))
            if letter not in probs:
                probs[letter] = math.exp(float(entry["logprob"]))
            values.append(min(1.0, probs[letter] / (sum(probs.values()) if renormalise else 1.0)))
        return round(sum(values) / len(values), 4)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def picture(path: Path, max_side: int = IMAGE_MAX_SIDE) -> str:
    """The picture as base64 for the request: as it is, or scaled down (JPEG) when it is larger than
    `max_side` on its longest side; a file Pillow cannot read is sent as it is."""
    raw = Path(path).read_bytes()
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            if max(im.size) > max_side:
                small = im.convert("RGB")
                small.thumbnail((max_side, max_side))
                out = io.BytesIO()
                small.save(out, "JPEG", quality=90)
                raw = out.getvalue()
    except (OSError, ValueError, ImportError):
        pass
    return base64.b64encode(raw).decode("ascii")


class OllamaReasoner:
    def __init__(self):
        self._ready: dict[tuple[str, str], tuple[float, str | None]] = {}
        self._unsupported: dict[str, set[str]] = {}      # model → request fields its runner rejected
        self._used: set[str] = set()                      # models this process asked (release() unloads them)
        self._told: set[str] = set()
        self._server: subprocess.Popen | None = None      # the `ollama serve` this process started
        self._start_failed: str | None = None             # why a start did not work (not tried again)

    def _tell(self, message: str) -> None:
        if message not in self._told:
            self._told.add(message)
            log(message)

    # ── HTTP ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _request(path: str, body: dict | None, timeout: float) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(host() + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 (the local model server)
            return json.loads(r.read().decode("utf-8") or "{}")

    # ── ready ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _model(settings: dict) -> str:
        return str(settings.get("llm_model") or "qwen3.5:9b")

    def ready(self, settings: dict, download: bool = False) -> str | None:
        """None when the server is running and has the model; otherwise why not (never pulls a model)."""
        if offline():
            return "offline (SVE_OFFLINE)"
        model, now = self._model(settings), time.monotonic()
        key = (host(), model)
        cached = self._ready.get(key)
        if cached and now - cached[0] < READY_TTL:
            return cached[1]
        names: set[str] | None = None
        try:
            names = self._tags()
        except urllib.error.HTTPError as e:
            reason = f"the Ollama server at {host()} answered HTTP {e.code}"
        except (OSError, ValueError, AttributeError):
            reason = self._start(settings)                 # None = a server answers now
            if reason is None:
                try:
                    names = self._tags()
                except (OSError, ValueError, AttributeError):
                    reason = f"{NOT_RUNNING} (it stopped answering)"
        if names is not None:
            wanted = {model} if ":" in model else {model, f"{model}:latest"}
            reason = None if names & wanted else f"model {model} is not pulled (ollama pull {model})"
        self._ready[key] = (time.monotonic(), reason)
        return reason

    def _tags(self) -> set[str]:
        """The names of the models the server has."""
        tags = self._request("/api/tags", None, TAGS_TIMEOUT)
        return {str(m.get(f)) for m in tags.get("models") or [] for f in ("name", "model") if m.get(f)}

    # ── the server on demand ──────────────────────────────────────────────────
    def can_start(self, settings: dict) -> bool:
        """Whether a server that is not running would be started here (decider.llm_start, the program
        installed, OLLAMA_HOST on this machine, not offline)."""
        return wants_start(settings) and not offline() and local() and program() is not None

    def _answers(self) -> bool:
        try:
            self._request("/api/tags", None, TAGS_TIMEOUT)
            return True
        except urllib.error.HTTPError:
            return True                                    # an HTTP error is still an answer
        except (OSError, ValueError):
            return False

    def _start(self, settings: dict) -> str | None:
        """Starts `ollama serve` when it may (can_start) → None once a server answers; otherwise why
        there is none. The process stops it when it ends (stop())."""
        if not local():
            return f"the Ollama server at {host()} does not answer"
        exe = program()
        if exe is None:
            return f"{NOT_RUNNING} (install: {INSTALL_HINT})"
        if not self.can_start(settings):
            return f"{NOT_RUNNING} (start it: ollama serve, or set decider.llm_start: true)"
        if self._start_failed:
            return self._start_failed
        try:
            proc = subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError as e:
            self._start_failed = f"{NOT_RUNNING} and could not be started ({e})"
            return self._start_failed
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline and proc.poll() is None:
            if self._answers():
                self._server = proc
                atexit.register(self.stop)
                log("Decider: started the local Ollama server (it is stopped when the engine finishes)")
                return None
            time.sleep(0.2)
        if proc.poll() is not None:
            if self._answers():                            # somebody else started one at the same time
                return None
            self._start_failed = f"{NOT_RUNNING} and could not be started (ollama serve ended with code {proc.returncode})"
        else:
            self._end(proc)
            self._start_failed = f"{NOT_RUNNING} and did not answer {START_TIMEOUT:g} s after it was started"
        return self._start_failed

    @staticmethod
    def _end(proc: subprocess.Popen) -> None:
        proc.terminate()
        try:
            proc.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _loaded(self) -> bool:
        """Whether the server has a model in memory (false when it does not answer)."""
        try:
            return bool(self._request("/api/ps", None, TAGS_TIMEOUT).get("models"))
        except Exception:  # noqa: BLE001 - best effort
            return False

    def stop(self) -> None:
        """Stops the server this process started (never one that was already running): its models are
        unloaded first, and it is left running when another program still has a model loaded on it."""
        proc, self._server = self._server, None
        if proc is None or proc.poll() is not None:
            return
        for model in list(self._used):
            self._unload(model)
        deadline = time.monotonic() + IDLE_WAIT
        while self._loaded() and time.monotonic() < deadline:
            time.sleep(0.2)
        if self._loaded():
            log("Decider: the local Ollama server is left running (another program has a model loaded)")
            return
        self._end(proc)
        self._ready.clear()
        log("Decider: stopped the local Ollama server")

    # ── choose ────────────────────────────────────────────────────────────────
    def _body(self, model: str, prompt: str, keys: list[str], many: bool, image: Path | None) -> dict:
        message: dict = {"role": "user", "content": prompt}
        if image is not None:
            message["images"] = [picture(image)]
        body = {"model": model, "messages": [message], "stream": False, "format": schema(keys, many),
                "think": False, "options": dict(OPTIONS), "keep_alive": KEEP_ALIVE,
                "logprobs": True, "top_logprobs": 5}
        for field in self._unsupported.get(model, ()):
            body.pop(field, None)
            if field == "logprobs":
                body.pop("top_logprobs", None)
        return body

    def _rejected_field(self, model: str, body: dict, error: urllib.error.HTTPError) -> str | None:
        """The optional field the server's error names (it is dropped and the request sent again)."""
        try:
            text = error.read().decode("utf-8", "replace").lower()
        except Exception:  # noqa: BLE001
            text = ""
        for field, words in OPTIONAL_FIELDS:
            if field in body and (any(w in text for w in words) or (field == "format" and error.code == 501)):
                self._unsupported.setdefault(model, set()).add(field)
                return field
        return None

    def choose(self, prompt: str, keys: list[str], settings: dict, image: Path | None = None,
               many: bool = False) -> tuple[list[str], float | None]:
        """The letters chosen among `keys` and how sure the model is; ([], None) on any failure."""
        model = self._model(settings)
        if not keys or offline():
            return [], None
        try:
            body = self._body(model, prompt, keys, many, image)
            timeout = float(settings.get("timeout") or 60)
            for _ in range(len(OPTIONAL_FIELDS) + 1):
                try:
                    self._used.add(model)
                    data = self._request("/api/chat", body, timeout)
                    break
                except urllib.error.HTTPError as e:
                    if self._rejected_field(model, body, e) is None:
                        raise
                    body = self._body(model, prompt, keys, many, image)
            else:
                return [], None
            if data.get("error"):
                raise ValueError(str(data["error"]))
            content = str((data.get("message") or {}).get("content") or "")
            chosen = letters(content, keys, many)
            if not chosen:
                return [], None
            return [x for x, _ in chosen], confidence(data.get("logprobs"), chosen, keys, many, "format" in body)
        except Exception as e:  # noqa: BLE001 - never stops the video: the rules answer
            self._tell(f"Decider: the language model did not answer ({type(e).__name__}: {e})")
            return [], None

    def release(self, settings: dict) -> None:
        """Unloads the model from the server's memory (only when this process used it)."""
        model = self._model(settings)
        if model in self._used:
            self._unload(model)

    def _unload(self, model: str) -> None:
        self._used.discard(model)
        try:
            self._request("/api/generate", {"model": model, "keep_alive": 0}, RELEASE_TIMEOUT)
        except Exception:  # noqa: BLE001 - best effort
            pass
