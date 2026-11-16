"""The decider's small model: a zero-shot text classifier (NLI) through Hugging Face transformers.

The default model (decider.nli_model, mDeBERTa-v3 base trained on multilingual NLI, MIT, ≈560 MB)
reads Spanish, English and 100 more languages and scores how well each label fits a text: each label
is put into a hypothesis template ("This sentence is {}.") and the model says whether the text
entails it. It runs on the CPU in well under a second per question.

transformers / torch are imported lazily (only when a question is classified): without them the
engine still works and the rules answer. The model is read from the Hugging Face cache; it is only
downloaded when ready() was called with download=True (decider.enabled: true) and neither
SVE_OFFLINE nor HF_HUB_OFFLINE is set.
"""
from __future__ import annotations

import gc
import importlib.util
import os
import sys
from pathlib import Path

from ...log import log
from ..clip.detector import hub_cache

INSTALL_HINT = "pip install 'social-video-editor[decider]'"
WEIGHTS = ("model.safetensors", "pytorch_model.bin", "model.safetensors.index.json", "pytorch_model.bin.index.json")


def offline() -> bool:
    """Nothing may be downloaded (SVE_OFFLINE or HF_HUB_OFFLINE set to something other than "" / "0")."""
    return any(os.environ.get(v, "").strip() not in ("", "0") for v in ("SVE_OFFLINE", "HF_HUB_OFFLINE"))


def downloaded(name: str) -> bool:
    """Cheap check (nothing imported): the model is a local folder or its weights are in the HF cache."""
    if Path(name).expanduser().is_dir():
        return True
    snapshots = hub_cache() / ("models--" + name.replace("/", "--")) / "snapshots"
    return snapshots.is_dir() and any((s / w).exists() for s in snapshots.iterdir() for w in WEIGHTS)


class NliClassifier:
    def __init__(self):
        self._pipes: dict[tuple[str, str], object] = {}
        self._download = False              # ready(download=True) allowed a download
        self._told: set[str] = set()

    def _tell(self, message: str) -> None:
        if message not in self._told:
            self._told.add(message)
            log(message)

    @staticmethod
    def _name(settings: dict) -> str:
        return str(settings.get("nli_model") or "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7")

    def ready(self, settings: dict, download: bool = False) -> str | None:
        """None when it can be used; otherwise why not. Imports nothing heavy and loads nothing."""
        for module in ("transformers", "torch"):
            if importlib.util.find_spec(module) is None:
                return f"{module} is not installed ({INSTALL_HINT})"
        name = self._name(settings)
        if downloaded(name):
            return None
        if offline():
            return f"the model {name} is not downloaded and downloads are off (SVE_OFFLINE / HF_HUB_OFFLINE)"
        if not download:
            return (f"the model {name} is not downloaded yet (≈560 MB, once); "
                    "set decider.enabled: true to download it")
        self._download = True
        return None

    # ── model ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _device(settings: dict) -> str:
        device = str(settings.get("device") or "cpu").strip().lower()
        if device == "cpu":
            return device
        from ..upscale.realesrgan import pick_device
        return pick_device(device, "decider.device")

    def _load(self, name: str, device: str, download: bool):
        """The zero-shot pipeline (tokenizer + model read from the cache unless `download`)."""
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline
        try:
            from transformers.utils import logging as hf_logging
            hf_logging.set_verbosity_error()
            hf_logging.disable_progress_bar()
        except Exception:  # noqa: BLE001 - cosmetic only
            pass
        local = not download
        tokenizer = AutoTokenizer.from_pretrained(name, local_files_only=local)
        model = AutoModelForSequenceClassification.from_pretrained(name, local_files_only=local)
        return pipeline("zero-shot-classification", model=model, tokenizer=tokenizer, device=device)

    def _pipe(self, settings: dict):
        name, device = self._name(settings), self._device(settings)
        key = (name, device)
        if key not in self._pipes:
            download = self._download and not offline() and not downloaded(name)
            if download:
                log(f"Decider: downloading the small classifier {name} (≈560 MB, once)…")
            self._pipes[key] = self._load(name, device, download)
        return self._pipes[key]

    # ── classify ──────────────────────────────────────────────────────────────
    @staticmethod
    def _scores(result, labels: list[str]) -> list[float]:
        """The pipeline sorts the labels by score: back to the order they were given."""
        by_label = dict(zip(result["labels"], result["scores"]))
        return [float(by_label[label]) for label in labels]

    def classify(self, items: list[tuple[str, list[str], str, bool]], settings: dict) -> list[list[float]]:
        """A score per label (in order) for each item; [] for an item that cannot be scored. Items that
        share labels, template and kind go to the model together."""
        out: list[list[float]] = [[] for _ in items]
        groups: dict[tuple, list[int]] = {}
        for i, (text, labels, template, multi) in enumerate(items):
            if str(text or "").strip() and labels and "{}" in str(template or ""):
                groups.setdefault((tuple(labels), str(template), bool(multi)), []).append(i)
        if not groups:
            return out
        try:
            pipe = self._pipe(settings)
        except Exception as e:  # noqa: BLE001 - a missing model never stops the video: the rules answer
            self._tell(f"Decider: the small classifier could not be loaded ({type(e).__name__}: {e})")
            return out
        batch = int(settings.get("batch_size") or 8)
        for (labels, template, multi), rows in groups.items():
            texts = [str(items[i][0]) for i in rows]
            kwargs = {"candidate_labels": list(labels), "hypothesis_template": template, "multi_label": multi}
            try:
                results = pipe(texts, batch_size=batch, **kwargs)
                if isinstance(results, dict):
                    results = [results]
                for i, r in zip(rows, results):
                    out[i] = self._scores(r, list(labels))
            except Exception:  # noqa: BLE001 - one at a time, so one bad item does not lose the others
                for i, text in zip(rows, texts):
                    try:
                        out[i] = self._scores(pipe(text, **kwargs), list(labels))
                    except Exception:  # noqa: BLE001
                        out[i] = []
        return out

    def release(self) -> None:
        """Drops the model(s) and gives the memory back (also the GPU cache when torch is loaded)."""
        self._pipes.clear()
        gc.collect()
        torch = sys.modules.get("torch")
        if torch is None:
            return
        try:
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - freeing memory is best effort
            pass
