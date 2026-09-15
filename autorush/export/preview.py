"""Rendu de la video de prevaisualisation (MP4).

Le but est que la preview donne **exactement** la meme sensation que la
timeline Premiere : memes points de coupe (a l'image), memes zooms, meme
rythme.

Comment la fidelite est obtenue
-------------------------------
* les bornes des plans sont arrondies a l'image une seule fois
  (``autorush.editing.frames``) et servent a la fois au XML et a la preview ;
* les zooms sont rendus a partir des **memes keyframes** que celles ecrites
  dans le XML, interpolees lineairement - exactement ce que fait Premiere ;
* l'image est traitee en une seule passe ffmpeg (selection des images utiles,
  puis mise a l'echelle animee) : aucune derive de timecode ;
* le son est monte en Python, echantillon par echantillon, avec un micro-fondu
  a chaque raccord. Il ne peut donc pas se desynchroniser de l'image.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from autorush.editing.frames import FrameClip, frame_clips, total_frames
from autorush.editing.timeline import Timeline
from autorush.errors import ExportError, MediaError
from autorush.logging_setup import get_logger
from autorush.media.ffmpeg import MediaInfo, extract_audio, run_ffmpeg
from autorush.utils import clamp
from autorush.zoom.planner import ZOOM_DIRECT, ZoomPlan

log = get_logger("export.preview")

#: duree du micro-fondu audio applique de chaque cote d'un raccord
AUDIO_FADE_SECONDS = 0.004
#: taille des blocs de lecture audio (echantillons)
AUDIO_CHUNK = 1 << 16
#: au-dela, on abandonne le graphe unique pour un rendu plan par plan
MAX_FILTER_SCRIPT = 400_000


@dataclass
class PreviewResult:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    clips: int


# --------------------------------------------------------------------------- #
# Expressions ffmpeg
# --------------------------------------------------------------------------- #
def _select_expression(clips: list[FrameClip], fps: float) -> str:
    """Expression ``select`` qui garde exactement les images des plans.

    L'image ``k`` porte l'horodatage ``k / fps``. On encadre donc chaque plan
    par ``(in - 0.4) / fps`` et ``(out - 0.6) / fps`` : les images retenues sont
    exactement ``in`` a ``out - 1``.
    """
    terms = []
    for clip in clips:
        low = (clip.in_frame - 0.4) / fps
        high = (clip.out_frame - 0.6) / fps
        terms.append(f"between(t,{low:.6f},{high:.6f})")
    return "+".join(terms) if terms else "0"


def _piecewise_expression(points: list[tuple[float, float]], variable: str = "t") -> str:
    """Interpolation lineaire entre des points ``(temps, valeur)``.

    Produit exactement la meme courbe que l'interpolation lineaire de Premiere
    entre deux keyframes.
    """
    if not points:
        return "1"
    if len(points) == 1:
        return f"{points[0][1]:.6f}"

    expression = f"{points[-1][1]:.6f}"
    for index in range(len(points) - 2, -1, -1):
        t0, v0 = points[index]
        t1, v1 = points[index + 1]
        span = t1 - t0
        if span <= 1e-9:
            continue
        slope = (v1 - v0) / span
        segment = f"({v0:.6f}+({slope:.6f})*({variable}-{t0:.6f}))"
        expression = f"if(lt({variable},{t1:.6f}),{segment},{expression})"
    return expression


def _zoom_expression(
    clips: list[FrameClip],
    zooms: ZoomPlan | None,
    fps: float,
    max_keyframes: int,
    tolerance: float,
) -> str:
    """Facteur d'agrandissement en fonction du temps **source**.

    La forme est une somme de termes fermes : ``1 + somme(porte * (courbe - 1))``.
    Chaque porte vaut 1 pendant le plan concerne et 0 ailleurs, ce qui evite une
    expression profondement imbriquee.
    """
    if zooms is None or not zooms.events:
        return "1"

    by_shot = zooms.by_shot()
    terms: list[str] = []
    for clip in clips:
        event = by_shot.get(clip.index)
        if event is None:
            continue
        low = (clip.in_frame - 0.5) / fps
        high = (clip.out_frame - 0.5) / fps

        if event.kind == ZOOM_DIRECT:
            factor = event.end_scale / 100.0
            terms.append(f"between(t,{low:.6f},{high:.6f})*{factor - 1.0:.6f}")
            continue

        keyframes = event.keyframes(
            fps=fps, max_keyframes=max_keyframes, tolerance=tolerance
        )
        if not keyframes:
            continue
        # les keyframes sont relatives au debut du plan -> temps source
        points = [
            (clip.source_start + time, value / 100.0) for time, value in keyframes
        ]
        # maintien jusqu'a la fin du plan
        if points[-1][0] < clip.source_end:
            points.append((clip.source_end, points[-1][1]))
        curve = _piecewise_expression(points)
        terms.append(f"between(t,{low:.6f},{high:.6f})*(({curve})-1)")

    if not terms:
        return "1"
    return "1+" + "+".join(terms)


def _video_filter(
    clips: list[FrameClip],
    zooms: ZoomPlan | None,
    fps: float,
    width: int,
    height: int,
    focus_x: float,
    focus_y: float,
    max_keyframes: int,
    tolerance: float,
) -> str:
    select = _select_expression(clips, fps)
    zoom = _zoom_expression(clips, zooms, fps, max_keyframes, tolerance)
    focus_x = clamp(focus_x, 0.0, 1.0)
    focus_y = clamp(focus_y, 0.0, 1.0)
    return (
        f"[0:v]fps={fps:.6f},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"select='{select}',"
        f"scale=w='ceil({width}*({zoom})/2)*2':h='ceil({height}*({zoom})/2)*2':eval=frame:flags=bicubic,"
        f"crop={width}:{height}:'(iw-{width})*{focus_x:.4f}':'(ih-{height})*{focus_y:.4f}',"
        f"setsar=1,setpts=N/{fps:.6f}/TB[v]"
    )


# --------------------------------------------------------------------------- #
# Montage audio (exact, en Python)
# --------------------------------------------------------------------------- #
def _render_audio(
    source_wav: Path,
    destination: Path,
    clips: list[FrameClip],
    fade_seconds: float = AUDIO_FADE_SECONDS,
) -> float:
    """Assemble les plans audio avec un micro-fondu a chaque raccord."""
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - dependances declarees
        raise ExportError(
            "numpy et soundfile sont necessaires pour la preview.",
            "pip install numpy soundfile",
        ) from exc

    with sf.SoundFile(str(source_wav)) as reader:
        sample_rate = reader.samplerate
        channels = reader.channels
        total_samples = len(reader)
        fade = max(1, int(round(fade_seconds * sample_rate)))

        with sf.SoundFile(
            str(destination),
            mode="w",
            samplerate=sample_rate,
            channels=channels,
            subtype="PCM_16",
        ) as writer:
            written = 0
            for clip in clips:
                start = int(round(clip.source_start * sample_rate))
                stop = int(round(clip.source_end * sample_rate))
                start = max(0, min(start, total_samples))
                stop = max(start, min(stop, total_samples))
                if stop <= start:
                    continue
                reader.seek(start)
                remaining = stop - start
                local_fade = min(fade, max(1, remaining // 3))
                position = 0
                while remaining > 0:
                    block = reader.read(
                        min(AUDIO_CHUNK, remaining), dtype="float32", always_2d=True
                    )
                    if block.shape[0] == 0:
                        break
                    count = block.shape[0]
                    block = block.copy()
                    # fondu d'entree
                    if position < local_fade:
                        length = min(local_fade - position, count)
                        ramp = (
                            np.arange(position, position + length, dtype=np.float32)
                            / local_fade
                        )
                        block[:length] *= ramp[:, None]
                    # fondu de sortie
                    tail_start = (stop - start) - local_fade
                    if position + count > tail_start:
                        begin = max(0, tail_start - position)
                        indices = np.arange(
                            position + begin, position + count, dtype=np.float32
                        )
                        ramp = np.clip(
                            ((stop - start) - indices) / local_fade, 0.0, 1.0
                        )
                        block[begin:] *= ramp[:, None]
                    writer.write(block)
                    position += count
                    remaining -= count
                written += position
    return written / float(sample_rate)


# --------------------------------------------------------------------------- #
def render_preview(
    destination: str | Path,
    media: MediaInfo,
    timeline: Timeline,
    zooms: ZoomPlan | None = None,
    work_dir: str | Path | None = None,
    height: int = 720,
    crf: int = 20,
    preset: str = "veryfast",
    audio_bitrate: str = "160k",
    threads: int = 0,
    max_keyframes: int = 90,
    keyframe_tolerance: float = 0.22,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    audio_wav: str | Path | None = None,
    on_progress=None,
) -> PreviewResult:
    """Rend la video de prevaisualisation et retourne ses caracteristiques."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if not timeline.shots:
        raise ExportError("Le montage est vide : rien a previsualiser.")

    fps = media.fps
    media_frames = max(1, int(round(media.duration * fps)))
    clips = frame_clips(timeline, fps, media_frames)
    frames = total_frames(clips)
    duration = frames / fps

    # -- dimensions de sortie (paires, rapport d'origine) --------------- #
    out_height = max(2, int(height) // 2 * 2)
    source_width = max(1, media.display_width)
    source_height = max(1, media.display_height)
    out_width = int(round(out_height * source_width / source_height)) // 2 * 2
    out_width = max(2, out_width)

    temporary = work_dir is None
    work = Path(work_dir) if work_dir else Path(destination.parent / ".autorush_preview")
    work.mkdir(parents=True, exist_ok=True)

    try:
        # -- 1. image ---------------------------------------------------- #
        video_filter = _video_filter(
            clips, zooms, fps, out_width, out_height,
            focus_x, focus_y, max_keyframes, keyframe_tolerance,
        )
        if len(video_filter) > MAX_FILTER_SCRIPT:  # pragma: no cover - rush enorme
            raise ExportError(
                "Le montage comporte trop de plans pour la preview en une passe.",
                "Relancez avec un style moins decoupe, ou sans preview.",
            )
        script = work / "preview_filter.txt"
        script.write_text(video_filter, encoding="utf-8")

        video_only = work / "preview_video.mp4"
        args = [
            "-i", str(media.path),
            "-filter_complex_script", str(script),
            "-map", "[v]",
            "-an",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(int(crf)),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-r", f"{fps:.6f}",
        ]
        if threads > 0:
            args += ["-threads", str(int(threads))]
        args.append(str(video_only))
        run_ffmpeg(
            args,
            total_duration=media.duration,
            on_progress=(lambda f: on_progress(0.70 * f)) if on_progress else None,
            description="Rendu de l'image",
        )

        # -- 2. son ------------------------------------------------------ #
        if audio_wav and Path(audio_wav).exists():
            source_wav = Path(audio_wav)
        else:
            source_wav = work / "preview_source.wav"
            extract_audio(
                media.path,
                source_wav,
                sample_rate=48000,
                mono=media.audio_channels <= 1,
                duration=media.duration,
                on_progress=(
                    (lambda f: on_progress(0.70 + 0.10 * f)) if on_progress else None
                ),
            )
        edited_wav = work / "preview_audio.wav"
        audio_duration = _render_audio(source_wav, edited_wav, clips)
        if on_progress:
            on_progress(0.85)
        log.info(
            "Preview : image %.3f s / son %.3f s (ecart %.1f ms)",
            duration, audio_duration, abs(duration - audio_duration) * 1000,
        )

        # -- 3. assemblage ----------------------------------------------- #
        mux_args = [
            "-i", str(video_only),
            "-i", str(edited_wav),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", str(audio_bitrate),
            "-shortest",
            "-movflags", "+faststart",
            str(destination),
        ]
        run_ffmpeg(
            mux_args,
            total_duration=duration,
            on_progress=(lambda f: on_progress(0.85 + 0.15 * f)) if on_progress else None,
            description="Assemblage de la preview",
        )
    finally:
        if temporary and work.exists():
            shutil.rmtree(work, ignore_errors=True)

    if not destination.exists():  # pragma: no cover - ffmpeg silencieux
        raise MediaError("La preview n'a pas ete produite.")

    if on_progress:
        on_progress(1.0)
    log.info("Preview ecrite : %s (%d plans, %.1f s)", destination.name, len(clips), duration)
    return PreviewResult(
        path=destination,
        width=out_width,
        height=out_height,
        fps=fps,
        duration=duration,
        clips=len(clips),
    )
