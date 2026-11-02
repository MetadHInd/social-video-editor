"""Internal: python -m social_video_editor.adapters.whisper.worker <wav> <config.json> <out.json>"""
import json
import sys


def main() -> None:
    wav, cfg_p, out_p = sys.argv[1:4]
    with open(cfg_p, encoding="utf-8") as f:
        t = json.load(f)
    import soundfile as sf
    from faster_whisper import WhisperModel

    # the audio is passed already decoded (16 kHz mono): avoids PyAV's reader, whose API changed
    audio, sr = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(1)
    assert sr == 16000, "the wav must be 16 kHz"

    model = WhisperModel(t.get("model", "small"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        audio, language=t.get("language", "es"), word_timestamps=True, vad_filter=True,
        initial_prompt=t.get("context") or None, beam_size=5,
    )
    words = []
    for s in segments:
        for w in s.words or []:
            text = w.word.strip()
            if text and w.probability >= float(t.get("min_probability", 0.35)):
                words.append({"word": text, "start": round(w.start, 3), "end": round(w.end, 3),
                              "prob": round(w.probability, 2)})
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
