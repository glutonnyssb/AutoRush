"""Export EDL CMX3600.

L'EDL ne transporte que les points de coupe : ni zoom, ni effet. Il sert de
filet de securite universel (Premiere, Resolve, Avid, Final Cut le lisent tous)
et de piece a conviction pour verifier les timecodes a la main.
"""

from __future__ import annotations

from pathlib import Path

from autorush.editing.timeline import Timeline
from autorush.logging_setup import get_logger
from autorush.utils import format_smpte, safe_filename

log = get_logger("export.edl")

MAX_TITLE = 70


def render_edl(
    timeline: Timeline,
    title: str,
    reel: str = "AX",
    fps: float = 25.0,
    source_name: str = "",
) -> str:
    """Genere le texte de l'EDL."""
    drop = "NON-DROP FRAME" if abs(fps - round(fps)) < 0.01 else "DROP FRAME"
    lines = [
        f"TITLE: {title[:MAX_TITLE]}",
        f"FCM: {drop}",
        "",
    ]
    record = 0.0
    for position, shot in enumerate(timeline.shots, start=1):
        source_in = format_smpte(shot.source_start, fps)
        source_out = format_smpte(shot.source_end, fps)
        record_in = format_smpte(record, fps)
        record += shot.duration
        record_out = format_smpte(record, fps)
        lines.append(
            f"{position:03d}  {reel:<8} AA/V  C        "
            f"{source_in} {source_out} {record_in} {record_out}"
        )
        if source_name:
            lines.append(f"* FROM CLIP NAME: {source_name}")
        if shot.text:
            comment = shot.text[:60].replace("\n", " ")
            lines.append(f"* COMMENT: {comment}")
        lines.append("")
    return "\n".join(lines)


def write_edl(
    path: str | Path,
    timeline: Timeline,
    title: str = "AutoRush",
    fps: float = 25.0,
    source_name: str = "",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_edl(timeline, safe_filename(title, MAX_TITLE), fps=fps, source_name=source_name),
        encoding="utf-8",
    )
    log.info("EDL ecrit : %s", path.name)
    return path
