"""Domain layer: pure Python + numpy. No subprocesses, no file access, no media libraries.

Everything here works on plain data (dicts, lists, numbers) with English keys; the adapters
translate legacy files and talk to FFmpeg, Pillow, OpenCV, Whisper, librosa…
"""
