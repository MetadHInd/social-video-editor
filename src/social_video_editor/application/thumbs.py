"""Use case: contact sheets + transcript, to LOOK at the footage before deciding.

Creates in cache/thumbs/:
  <id>_sheet_01.jpg …  grid of frames with their time, motion bar (green) and loudness bar (orange)
  <id>_transcript.txt  what is said, with times
  photos_sheet_01.jpg …  grid of the still photos themselves (photo sources have no video to sample)
Fine-grained: sve thumbs <project> --source 0 --start 120 --end 150 --every 1
"""
from __future__ import annotations

from pathlib import Path

from ..domain.models import Project
from ..domain.timebase import mmss
from .analyze import is_photo, prepare_sources
from .services import Services


def transcript_text(info: dict, analysis: dict) -> str:
    """Transcript grouped in phrases with their time."""
    lines, phrase, start = [], [], None
    words = analysis.get("words", [])
    for i, w in enumerate(words):
        if start is None:
            start = w["start"]
        phrase.append(w["word"])
        cut = i == len(words) - 1 or words[i + 1]["start"] - w["end"] > 0.6 or w["word"][-1:] in ".?!"
        if cut:
            lines.append(f"[{mmss(start)} → {mmss(w['end'])}] {' '.join(phrase)}")
            phrase, start = [], None
    au = analysis.get("audio") or {}
    head = [f"# {Path(info['path']).name} — {info['duration']:.1f} s",
            f"# speech in {analysis['voice']['ratio'] * 100:.0f}% of the video; "
            f"{len(analysis['scenes'])} scenes; sound peaks at: "
            + ", ".join(mmss(p) for p in au.get("peaks", [])[:40]), ""]
    return "\n".join(head + (lines or ["(no speech detected)"])) + "\n"


def contact_sheets(svc: Services, project: Project, source: int | None = None, start: float | None = None,
                   end: float | None = None, every: float | None = None) -> list[Path]:
    infos, analyses = prepare_sources(svc, project)
    out_dir = project.cache / "thumbs"
    out = []
    chosen = range(len(infos)) if source is None else [source]
    photos = [{**infos[k], "index": k} for k in chosen if is_photo(infos[k])]
    for k in chosen:
        if is_photo(infos[k]):
            continue
        read = svc.video.frame_reader(infos[k]["proxy"])
        try:
            out += svc.composer.contact_sheets(infos[k], analyses[k], read, out_dir, start or 0, end, every)
        finally:
            read.close()
        path = out_dir / f"{infos[k]['id']}_transcript.txt"
        svc.projects.write_text(path, transcript_text(infos[k], analyses[k]))
        out.append(path)
    if photos:
        name = "photos" if source is None else f"{photos[0]['id']}_photo"
        out += svc.composer.photo_sheets(photos, out_dir, name)
    return out
