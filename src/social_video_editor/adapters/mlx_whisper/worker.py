"""Internal: python -m social_video_editor.adapters.mlx_whisper.worker <wav> <config.json> <out.json>

Whisper on the Apple GPU (mlx-whisper). The speech is cut out with the same voice detector and the same
time map faster-whisper uses (`vad_filter=True`), so silences, word times and the words kept follow the
same rules as the default engine; only the decoding runs elsewhere (greedy instead of a 5-beam search).
"""
import json
import sys

from .transcriber import model_repo


def speech_only(audio, sr: int = 16000):
    """(speech audio joined end to end, its time map) — faster-whisper's own voice detector and map."""
    import numpy as np
    from faster_whisper.vad import SpeechTimestampsMap, VadOptions, collect_chunks, get_speech_timestamps

    chunks = get_speech_timestamps(audio, VadOptions())
    if not chunks:
        return None, None
    pieces, _ = collect_chunks(audio, chunks)
    return np.concatenate(pieces, axis=0), SpeechTimestampsMap(chunks, sr)


def dtw(x):
    """Dynamic time warping of the token × frame cost matrix → [2, steps] path (tokens, frames).

    The same result (and tie-breaking) as mlx-whisper's numba version, computed one anti-diagonal at a
    time with numpy: numba would compile its version again in every worker process (≈1.7 s each time)."""
    import numpy as np

    x = np.asarray(x, dtype=np.float32)
    N, M = x.shape
    cost = np.full((N + 1, M + 1), np.inf, dtype=np.float32)
    trace = np.full((N + 1, M + 1), -1, dtype=np.int8)
    cost[0, 0] = 0
    for d in range(2, N + M + 1):                         # cells (i, j) with i + j = d, i, j ≥ 1
        i = np.arange(max(1, d - M), min(N, d - 1) + 1)
        j = d - i
        c0, c1, c2 = cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1]
        t = np.where((c0 < c1) & (c0 < c2), 0, np.where((c1 < c0) & (c1 < c2), 1, 2)).astype(np.int8)
        cost[i, j] = x[i - 1, j - 1] + np.choose(t, (c0, c1, c2))
        trace[i, j] = t
    trace[0, :], trace[:, 0] = 2, 1
    i, j, path = N, M, []
    while i > 0 or j > 0:
        path.append((i - 1, j - 1))
        t = trace[i, j]
        if t == 0:
            i, j = i - 1, j - 1
        elif t == 1:
            i -= 1
        elif t == 2:
            j -= 1
        else:
            raise ValueError("Unexpected trace value")
    return np.array(path[::-1]).T


def words_of(result: dict, time_map, min_probability: float) -> list[dict]:
    """mlx-whisper's segments → [{"word", "start", "end", "prob"}] on the original timeline."""
    words = []
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            text = str(w.get("word", "")).strip()
            prob = float(w.get("probability", 0.0))
            if not text or prob < min_probability:
                continue
            start, end = float(w["start"]), float(w["end"])
            if time_map is not None:      # the word's start and end resolved in the same speech chunk
                k = time_map.get_chunk_index((start + end) / 2)
                start, end = time_map.get_original_time(start, k), time_map.get_original_time(end, k)
            words.append({"word": text, "start": round(start, 3), "end": round(end, 3), "prob": round(prob, 2)})
    return words


def main() -> None:
    wav, cfg_p, out_p = sys.argv[1:4]
    with open(cfg_p, encoding="utf-8") as f:
        t = json.load(f)
    import soundfile as sf

    audio, sr = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(1)
    assert sr == 16000, "the wav must be 16 kHz"
    speech, time_map = speech_only(audio, sr)
    words: list[dict] = []
    if speech is not None:
        import mlx_whisper
        from mlx_whisper import timing

        timing.dtw = dtw            # same path, without numba's per-process compilation
        result = mlx_whisper.transcribe(
            speech, path_or_hf_repo=model_repo(t.get("model", "small")), language=t.get("language", "es"),
            initial_prompt=t.get("context") or None, word_timestamps=True, verbose=None,
        )
        words = words_of(result, time_map, float(t.get("min_probability", 0.35)))
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
