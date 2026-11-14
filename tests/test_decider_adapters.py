"""The decider's adapters: the zero-shot classifier (NLI) and the local language model (Ollama).

Offline: the HTTP server and the transformers pipeline are fakes. One live test (SVE_LIVE=1) asks a
real local Ollama server."""
from __future__ import annotations

import base64
import io
import json
import math
import os
import sys
import urllib.error

import pytest

from social_video_editor.adapters.nli import classifier as nli
from social_video_editor.adapters.ollama import reasoner as ol
from social_video_editor.adapters.system import doctor
from social_video_editor.domain import decide as dd

S = dd.settings({})


# ── fake Ollama server ────────────────────────────────────────────────────────
class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeServer:
    """Stands in for urllib.request.urlopen: records each request, answers from a handler."""

    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        self.requests.append((req.get_method(), req.full_url, body, timeout))
        out = self.handler(req.full_url.rsplit("/api/", 1)[-1], body)
        if isinstance(out, Exception):
            raise out
        return _Response(json.dumps(out).encode())


def _tok(token: str, lp: float, alts: dict[str, float] | None = None) -> dict:
    alts = alts or {token: lp}
    return {"token": token, "logprob": lp, "top_logprobs": [{"token": t, "logprob": v} for t, v in alts.items()]}


def _chat(content: str, logprobs: list | None = None) -> dict:
    out = {"model": "m", "message": {"role": "assistant", "content": content}, "done": True}
    if logprobs is not None:
        out["logprobs"] = logprobs
    return out


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv("SVE_OFFLINE", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def _serve(monkeypatch, handler) -> FakeServer:
    server = FakeServer(handler)
    monkeypatch.setattr(ol.urllib.request, "urlopen", server)
    return server


def test_host_from_environment(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ol.host() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "192.168.1.5:9999")
    assert ol.host() == "http://192.168.1.5:9999"
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert ol.host() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "https://models.example.com/")
    assert ol.host() == "https://models.example.com:443"


def test_ready_offline_running_and_pulled(online, monkeypatch):
    monkeypatch.setenv("SVE_OFFLINE", "1")
    server = _serve(monkeypatch, lambda path, body: {"models": []})
    assert ol.OllamaReasoner().ready(S) == "offline (SVE_OFFLINE)" and server.requests == []

    monkeypatch.delenv("SVE_OFFLINE")
    tags = {"models": [{"name": "qwen3.5:9b", "model": "qwen3.5:9b"}, {"name": "other:latest"}]}
    server = _serve(monkeypatch, lambda path, body: tags)
    r = ol.OllamaReasoner()
    assert r.ready(S) is None and r.ready(S) is None
    assert len(server.requests) == 1                                     # cached for a few seconds
    method, url, _, timeout = server.requests[0]
    assert (method, url, timeout) == ("GET", "http://127.0.0.1:11434/api/tags", ol.TAGS_TIMEOUT)
    assert r.ready({**S, "llm_model": "other"}) is None                   # "other" = "other:latest"
    assert r.ready({**S, "llm_model": "big:70b"}) == "model big:70b is not pulled (ollama pull big:70b)"

    _serve(monkeypatch, lambda path, body: urllib.error.URLError("connection refused"))
    assert ol.OllamaReasoner().ready(S).startswith("Ollama is not running (install: brew install ollama")


def test_choose_sends_a_constrained_request_and_reads_the_letter(online, monkeypatch, tmp_path):
    picture = tmp_path / "frame.jpg"
    picture.write_bytes(b"\xff\xd8 not really a jpeg")
    logprobs = [_tok('{"', -0.01), _tok("answer", 0.0), _tok('":', 0.0), _tok(' "', 0.0),
                _tok("b", math.log(0.6), {"b": math.log(0.6), "a": math.log(0.2), " c": math.log(0.1), "x": math.log(0.05)}),
                _tok('"}', 0.0)]
    server = _serve(monkeypatch, lambda path, body: _chat('{"answer": "b"}', logprobs))
    chosen, conf = ol.OllamaReasoner().choose("¿Qué es?", ["a", "b", "c"], {**S, "timeout": 7}, picture)
    assert chosen == ["b"] and conf == pytest.approx(0.6 / 0.9, abs=1e-4)
    method, url, body, timeout = server.requests[0]
    assert (method, url, timeout) == ("POST", "http://127.0.0.1:11434/api/chat", 7.0)
    assert body["model"] == "qwen3.5:9b" and body["stream"] is False and body["think"] is False
    assert body["options"]["temperature"] == 0 and body["options"]["presence_penalty"] == 0 and body["logprobs"] is True and body["top_logprobs"] == 5
    assert body["format"]["properties"]["answer"] == {"type": "string", "enum": ["a", "b", "c"]}
    assert body["format"]["required"] == ["answer"]
    message = body["messages"][0]
    assert message["role"] == "user" and message["content"] == "¿Qué es?"
    assert base64.b64decode(message["images"][0]) == picture.read_bytes()


def test_choose_many_takes_the_mean_confidence(online, monkeypatch):
    logprobs = [_tok('{"answer": ["', 0.0),
                _tok("a", math.log(0.8), {"a": math.log(0.8), "b": math.log(0.2)}),
                _tok('", "', 0.0),
                _tok("c", math.log(0.5), {"c": math.log(0.5), "b": math.log(0.25), "]": math.log(0.25)}),
                _tok('"]}', 0.0)]
    server = _serve(monkeypatch, lambda path, body: _chat('{"answer": ["a", "c", "a"]}', logprobs))
    chosen, conf = ol.OllamaReasoner().choose("p", ["a", "b", "c"], S, many=True)
    assert chosen == ["a", "c"] and conf == pytest.approx((0.8 + 0.5 / 0.75) / 2, abs=1e-4)   # deduplicated
    items = server.requests[-1][2]["format"]["properties"]["answer"]
    assert items["type"] == "array" and items["items"]["enum"] == ["a", "b", "c"]


def test_choose_without_logprobs_and_invalid_answers(online, monkeypatch):
    server = _serve(monkeypatch, lambda path, body: _chat('{"answer": "a"}'))
    assert ol.OllamaReasoner().choose("p", ["a", "b"], S) == (["a"], None)
    server.handler = lambda path, body: _chat('{"answer": "z"}')
    assert ol.OllamaReasoner().choose("p", ["a", "b"], S) == ([], None)
    server.handler = lambda path, body: _chat("I cannot tell.")
    assert ol.OllamaReasoner().choose("p", ["a", "b"], S) == ([], None)


@pytest.mark.parametrize("error", [urllib.error.URLError("refused"), TimeoutError("timed out"),
                                   urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b'{"error":"oom"}'))])
def test_choose_never_raises(online, monkeypatch, error):
    _serve(monkeypatch, lambda path, body: error)
    assert ol.OllamaReasoner().choose("p", ["a", "b"], S) == ([], None)


def test_choose_is_silent_offline(monkeypatch):
    server = _serve(monkeypatch, lambda path, body: _chat('{"answer": "a"}'))
    assert ol.OllamaReasoner().choose("p", ["a", "b"], S) == ([], None) and server.requests == []


def test_runner_without_structured_output_falls_back_to_plain_letters(online, monkeypatch):
    """Some runners answer 501 "structured output is unavailable": the letter is read from plain text."""
    def handler(path, body):
        if "format" in body:
            return urllib.error.HTTPError("u", 501, "Not Implemented", {},
                                          io.BytesIO(b'{"error":"structured output is unavailable"}'))
        alts = {"b": math.log(0.6), " b": math.log(0.1), " a": math.log(0.1), "N": math.log(0.2)}
        return _chat("b) no", [_tok("b", math.log(0.6), alts), _tok(")", 0.0), _tok(" no", 0.0)])

    server = _serve(monkeypatch, handler)
    r = ol.OllamaReasoner()
    chosen, conf = r.choose("p", ["a", "b"], S)
    assert chosen == ["b"] and conf == pytest.approx(0.7)       # unconstrained: not renormalised ("N" = none)
    assert [("format" in body) for _, _, body, _ in server.requests] == [True, False]
    r.choose("p", ["a", "b"], S)
    assert "format" not in server.requests[-1][2] and len(server.requests) == 3     # remembered per model


def test_plain_letters_parsing():
    assert ol.letters("a", ["a", "b"], False) == [("a", 0)]
    assert ol.letters("  B. Porque sí", ["a", "b"], False) == [("b", 2)]
    assert ol.letters("a, c", ["a", "b", "c"], True) == [("a", 0), ("c", 3)]
    assert ol.letters("a, c\nb is wrong", ["a", "b", "c"], True) == [("a", 0), ("c", 3)]
    assert ol.letters("d", ["a", "b"], False) == []
    assert ol.letters("Ninguna", ["a", "b"], False) == []


def test_release_unloads_only_a_model_it_used(online, monkeypatch):
    server = _serve(monkeypatch, lambda path, body: _chat('{"answer": "a"}') if path == "chat" else {})
    r = ol.OllamaReasoner()
    r.release(S)
    assert server.requests == []
    r.choose("p", ["a", "b"], S)
    r.release(S)
    assert server.requests[-1][1].endswith("/api/generate")
    assert server.requests[-1][2] == {"model": "qwen3.5:9b", "keep_alive": 0}
    _serve(monkeypatch, lambda path, body: urllib.error.URLError("gone"))
    r.choose("p", ["a", "b"], S)
    r.release(S)                                                         # errors are ignored


# ── zero-shot classifier ──────────────────────────────────────────────────────
class FakePipeline:
    """Like transformers' zero-shot pipeline: labels sorted by score (here: by length, longest first)."""

    def __init__(self):
        self.calls = []

    def __call__(self, texts, candidate_labels, hypothesis_template, multi_label, **kw):
        self.calls.append((texts, tuple(candidate_labels), hypothesis_template, multi_label))

        def one(text):
            if "boom" in text:
                raise ValueError("cannot score")
            ranked = sorted(candidate_labels, key=len, reverse=True)
            return {"sequence": text, "labels": ranked, "scores": [len(x) / 100 for x in ranked]}

        return [one(t) for t in texts] if isinstance(texts, list) else one(texts)


def test_classify_maps_scores_back_to_the_labels_order(monkeypatch):
    pipe, loads = FakePipeline(), []
    c = nli.NliClassifier()
    monkeypatch.setattr(c, "_load", lambda name, device, download: loads.append((name, device, download)) or pipe)
    items = [("Antes 3, ahora 1.", ["a list", "a comparison of figures", "a quote"], "This is {}.", False),
             ("Uno, dos y tres.", ["a list", "a comparison of figures", "a quote"], "This is {}.", False),
             ("boom", ["x", "yy"], "Is {}?", True),
             ("", ["x"], "{}", False),
             ("no template", ["x"], "plain", False)]
    out = c.classify(items, S)
    assert out[0] == out[1] == [0.06, 0.23, 0.07]
    assert out[2:] == [[], [], []]
    assert loads == [(S["nli_model"], "cpu", False)]                     # one model, from the cache
    assert pipe.calls[0][0] == ["Antes 3, ahora 1.", "Uno, dos y tres."]  # same labels → one batch
    assert pipe.calls[1][1:] == (("x", "yy"), "Is {}?", True)
    c.classify(items[:1], S)
    assert len(loads) == 1                                               # memoised
    c.release()
    assert c._pipes == {}
    c.classify(items[:1], S)
    assert len(loads) == 2


def test_classify_without_a_model_gives_empty_scores(monkeypatch):
    c = nli.NliClassifier()

    def missing(name, device, download):
        raise OSError("not in the cache")

    monkeypatch.setattr(c, "_load", missing)
    assert c.classify([("text", ["a", "b"], "{}", False)], S) == [[]]


class _Forbid:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("transformers", "torch"):
            raise ImportError(f"{name} must not be imported here")
        return None


def test_nli_ready_reasons_without_importing_transformers(monkeypatch, tmp_path):
    for name in ("transformers", "torch"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Forbid(), *sys.meta_path])
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(nli.importlib.util, "find_spec", lambda name: object())         # as if installed
    c = nli.NliClassifier()
    assert "downloads are off" in c.ready(S, download=True)              # SVE_OFFLINE (conftest)
    monkeypatch.delenv("SVE_OFFLINE")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert "not downloaded yet" in c.ready(S)
    assert c.ready(S, download=True) is None and c._download
    snap = tmp_path / "hub" / ("models--" + S["nli_model"].replace("/", "--")) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert "not downloaded yet" in nli.NliClassifier().ready(S)          # a folder without weights
    (snap / "model.safetensors").write_bytes(b"x")
    assert nli.NliClassifier().ready(S) is None
    monkeypatch.setattr(nli.importlib.util, "find_spec", lambda name: None)
    assert nli.NliClassifier().ready(S) == "transformers is not installed (pip install 'social-video-editor[decider]')"


def test_doctor_rows_for_the_decider(monkeypatch):
    class Part:
        def __init__(self, why):
            self.why = why

        def ready(self, settings, download=False):
            assert download is False
            return self.why

    rows = doctor.decider_checks(Part(None), Part("offline (SVE_OFFLINE)"), {})
    assert rows[0][:2] == ("ok", "decider: classifier") and S["nli_model"] in rows[0][2]
    assert rows[1][:2] == ("optional", "decider: language model") and rows[1][2].startswith("offline")
    assert doctor.decider_checks(None, None, {}) == []                 # no services → no rows


# ── live (a real local Ollama server) ─────────────────────────────────────────
@pytest.mark.live
@pytest.mark.skipif(os.environ.get("SVE_LIVE") != "1", reason="set SVE_LIVE=1 with a local Ollama server")
def test_live_ollama_answers_a_closed_question(monkeypatch, capsys):
    monkeypatch.delenv("SVE_OFFLINE", raising=False)
    r = ol.OllamaReasoner()
    why = r.ready(S)
    if why:
        pytest.skip(why)
    q = dd.Question("is_comparison", "yesno", "¿Esta frase compara dos cifras?", dd.yes_no("es"),
                    "Antes tardaba 3 segundos y ahora tarda 1.", ("no",), 0.3)
    prompt, keys, many = dd.llm_request(q)
    try:
        chosen, conf = r.choose(prompt, keys, S, None, many)
        assert dd.from_keys(q, chosen) == ("yes",) and conf is not None and conf > 0.5
        # the true answer (Paris) is not an option: with the output constrained the probabilities are
        # renormalised over the letters; unconstrained, the model may say so (no letter → ([], None))
        q2 = dd.Question("icon", "choice", "¿Cuál es la capital de Francia?",
                         (dd.Option("madrid", "Madrid"), dd.Option("roma", "Roma")), "", ("madrid",), 0.3)
        prompt2, keys2, _ = dd.llm_request(q2)
        chosen2, conf2 = r.choose(prompt2, keys2, S)
        assert dd.pick(q2, "llm", chosen2, conf2, S, dd.rules_only(q2)) is None      # unsure → the rules stand
        with capsys.disabled():
            print(f"\nlive: yes/no → {chosen} {conf}; not among the options → {chosen2} {conf2}; "
                  f"fields the runner rejected: {sorted(r._unsupported.get(S['llm_model'], ()))}")
    finally:
        r.release(S)


def test_letters_written_together_are_read_one_by_one():
    from social_video_editor.adapters.ollama.reasoner import letters
    assert [x for x, _ in letters("ab", ["a", "b", "c"], True)] == ["a", "b"]
    assert [x for x, _ in letters("a, c", ["a", "b", "c"], True)] == ["a", "c"]
    assert letters("ab", ["a", "b", "c"], False) == []           # one answer expected: "ab" is not one letter
    assert letters("hello", ["a", "b", "c"], True) == []
