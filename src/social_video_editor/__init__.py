"""social_video_editor: vertical video editing engine for social media (Python + FFmpeg).

Layout (hexagonal / ports & adapters):
  domain/       pure rules (plan, pieces, timeline, selection, music, color, text timing)
  ports/        interfaces the application needs (typing.Protocol)
  adapters/     FFmpeg, Pillow, OpenCV, librosa, Whisper, yt-dlp, files (YAML/JSON, legacy Spanish files)
  application/  use cases (analyze, thumbs, plan, render, view, music)
  container.py  wires ports to adapters; cli.py is the command line
"""

__version__ = "0.5.0"
