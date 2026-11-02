"""Transcripción en un proceso aparte (OpenCV y PyAV traen librerías que chocan si se cargan juntas).

Uso interno: python -m motor_edicion._whisper <wav> <config.json> <salida.json>
"""
import json
import sys


def main() -> None:
    wav, cfg_p, out_p = sys.argv[1:4]
    t = json.load(open(cfg_p, encoding="utf-8"))
    import soundfile as sf
    from faster_whisper import WhisperModel

    # Se pasa el audio ya decodificado (16 kHz mono): evita el lector de PyAV, que cambió de API.
    audio, sr = sf.read(wav, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(1)
    assert sr == 16000, "el wav debe ser de 16 kHz"

    modelo = WhisperModel(t.get("modelo", "small"), device="cpu", compute_type="int8")
    segs, _ = modelo.transcribe(
        audio, language=t.get("idioma", "es"), word_timestamps=True, vad_filter=True,
        initial_prompt=t.get("contexto") or None, beam_size=5,
    )
    palabras = []
    for s in segs:
        for w in s.words or []:
            texto = w.word.strip()
            if texto and w.probability >= float(t.get("prob_min", 0.35)):
                palabras.append({"p": texto, "ini": round(w.start, 3), "fin": round(w.end, 3),
                                 "prob": round(w.probability, 2)})
    json.dump(palabras, open(out_p, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
