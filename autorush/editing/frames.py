"""Conversion de la timeline en images entieres.

Premiere coupe a l'image. Si la preview coupait a la milliseconde, les deux
montages divergeraient de quelques images et, surtout, le son et l'image de la
preview se decaleraient peu a peu. On arrondit donc **une seule fois**, ici, et
tous les exports partent de ces memes bornes.
"""

from __future__ import annotations

from dataclasses import dataclass

from autorush.editing.timeline import Timeline


@dataclass
class FrameClip:
    """Un plan exprime en images."""

    index: int
    in_frame: int
    out_frame: int
    start_frame: int
    fps: float

    @property
    def length(self) -> int:
        return self.out_frame - self.in_frame

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.length

    @property
    def source_start(self) -> float:
        return self.in_frame / self.fps

    @property
    def source_end(self) -> float:
        return self.out_frame / self.fps

    @property
    def timeline_start(self) -> float:
        return self.start_frame / self.fps

    @property
    def timeline_end(self) -> float:
        return self.end_frame / self.fps

    @property
    def duration(self) -> float:
        return self.length / self.fps


def frame_clips(
    timeline: Timeline, fps: float, media_frames: int = 0
) -> list[FrameClip]:
    """Arrondit chaque plan a l'image, sans trou ni recouvrement."""
    if fps <= 0:
        raise ValueError("fps doit etre strictement positif")
    limit = media_frames if media_frames > 0 else None

    clips: list[FrameClip] = []
    cursor = 0
    for shot in timeline.shots:
        in_frame = max(0, int(round(shot.source_start * fps)))
        out_frame = int(round(shot.source_end * fps))
        # On borne d'abord l'entree, puis la sortie : l'inverse laisserait une
        # sortie au-dela de la fin du media quand le plan commence tout au bout.
        if limit is not None:
            in_frame = min(in_frame, max(0, limit - 1))
            out_frame = min(out_frame, limit)
        if out_frame <= in_frame:
            out_frame = in_frame + 1
        clips.append(
            FrameClip(
                index=len(clips),
                in_frame=in_frame,
                out_frame=out_frame,
                start_frame=cursor,
                fps=fps,
            )
        )
        cursor += out_frame - in_frame
    return clips


def total_frames(clips: list[FrameClip]) -> int:
    return clips[-1].end_frame if clips else 0
