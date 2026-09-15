"""Pipeline complet d'AutoRush.

    import -> transcription -> analyse -> reprises -> montage -> zooms -> export

Le pipeline est utilise tel quel par la ligne de commande et par l'interface
graphique. Il ne connait rien de l'affichage : il se contente d'appeler
``on_progress`` avec l'etape en cours et une progression globale.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from autorush.analysis.decisions import AnalysisResult, analyze
from autorush.analysis.seams import check_seams
from autorush.config import Settings
from autorush.errors import AutoRushError, CancelledError
from autorush.export.edl import write_edl
from autorush.export.fcp7xml import write_premiere_xml
from autorush.export.json_edl import write_json_edl
from autorush.export.report import write_html_report, write_markdown_report
from autorush.logging_setup import get_logger
from autorush.media.audio import AudioProfile
from autorush.media.ffmpeg import MediaInfo, extract_audio, probe_media
from autorush.transcription.base import Transcript
from autorush.transcription.io import load_transcript, save_transcript, write_srt
from autorush.utils import human_duration, safe_filename
from autorush.zoom.planner import ZoomPlan, plan_zooms

log = get_logger("pipeline")

#: etapes affichees a l'utilisateur, avec leur poids relatif
STAGES: list[tuple[str, str, float]] = [
    ("import", "Import", 2.0),
    ("transcription", "Transcription", 52.0),
    ("analyse", "Analyse", 7.0),
    ("reprises", "Reprises", 5.0),
    ("montage", "Montage", 6.0),
    ("zooms", "Zooms", 4.0),
    ("export", "Export", 24.0),
]

STAGE_LABELS: dict[str, str] = {key: label for key, label, _ in STAGES}


@dataclass
class Progress:
    """Etat d'avancement transmis a l'interface."""

    stage: str
    label: str
    fraction: float
    detail: str = ""

    @property
    def percent(self) -> int:
        return int(round(100 * max(0.0, min(1.0, self.fraction))))


ProgressCallback = Callable[[Progress], None] | None


class ProgressReporter:
    """Convertit des etapes ponderees en une progression globale 0-1."""

    def __init__(self, callback: ProgressCallback = None, weights=None) -> None:
        self.callback = callback
        entries = weights or STAGES
        total = sum(weight for _, _, weight in entries) or 1.0
        self._offsets: dict[str, tuple[float, float]] = {}
        cursor = 0.0
        for key, _, weight in entries:
            share = weight / total
            self._offsets[key] = (cursor, share)
            cursor += share
        self._labels = {key: label for key, label, _ in entries}
        self._current = entries[0][0] if entries else "import"
        self._detail = ""

    def stage(self, key: str, detail: str = "") -> None:
        self._current = key
        self._detail = detail
        self._emit(0.0)

    def sub(self, fraction: float, detail: str | None = None) -> None:
        if detail is not None:
            self._detail = detail
        self._emit(fraction)

    def done(self) -> None:
        if self.callback:
            self.callback(Progress("termine", "Termine", 1.0, ""))

    def _emit(self, fraction: float) -> None:
        if not self.callback:
            return
        offset, share = self._offsets.get(self._current, (0.0, 0.0))
        overall = offset + share * max(0.0, min(1.0, fraction))
        self.callback(
            Progress(
                stage=self._current,
                label=self._labels.get(self._current, self._current),
                fraction=overall,
                detail=self._detail,
            )
        )


@dataclass
class PipelineOptions:
    """Tout ce qu'il faut pour lancer un traitement."""

    input_path: Path
    output_dir: Path | None = None
    settings: Settings = field(default_factory=Settings)
    #: transcription deja disponible (json/srt) : evite de refaire le calcul
    transcript_path: Path | None = None
    #: sauvegarde la transcription a cote des sorties
    save_transcript: bool = True
    write_srt: bool = True
    make_preview: bool = False
    #: prefixe des fichiers de sortie (defaut : nom du rush)
    basename: str = ""
    #: dossier de travail (fichiers temporaires)
    work_dir: Path | None = None
    #: fonction appelee regulierement ; si elle renvoie ``True``, on s'arrete
    cancel_check: Callable[[], bool] | None = None
    #: analyse audio (respirations, energie des zooms)
    audio_analysis: bool = True
    #: ecrit aussi une variante du XML avec l'autre base de temps des keyframes
    write_alternate_xml: bool = True

    def resolved_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        return Path(self.input_path).resolve().parent / "AutoRush_out"

    def resolved_basename(self) -> str:
        if self.basename:
            return safe_filename(self.basename)
        return safe_filename(Path(self.input_path).stem)


@dataclass
class PipelineResult:
    """Resultat complet d'un traitement."""

    media: MediaInfo
    transcript: Transcript
    analysis: AnalysisResult
    zooms: ZoomPlan
    outputs: dict[str, Path] = field(default_factory=dict)
    elapsed: float = 0.0
    output_dir: Path = field(default_factory=Path)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        stats = self.analysis.stats
        return (
            f"{human_duration(stats.get('source_duration', 0))} -> "
            f"{human_duration(stats.get('final_duration', 0))} | "
            f"{stats.get('shots', 0)} plans | "
            f"{len(self.zooms.events)} zooms | "
            f"{len(self.analysis.flags) + len(self.analysis.seams)} points a verifier"
        )


# --------------------------------------------------------------------------- #
def _check_cancel(options: PipelineOptions) -> None:
    if options.cancel_check and options.cancel_check():
        raise CancelledError()


def run_pipeline(
    options: PipelineOptions, on_progress: ProgressCallback = None
) -> PipelineResult:
    """Execute le pipeline complet et retourne le resultat."""
    started = time.time()
    settings = options.settings
    input_path = Path(options.input_path)
    if not input_path.exists():
        raise AutoRushError(f"Fichier introuvable : {input_path}")

    # la preview pese lourd : on ajuste les poids pour que la barre soit honnete
    weights = list(STAGES)
    if options.make_preview:
        weights = [
            (key, label, weight * (3.4 if key == "export" else 1.0))
            for key, label, weight in weights
        ]
    reporter = ProgressReporter(on_progress, weights)

    output_dir = options.resolved_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = options.resolved_basename()
    outputs: dict[str, Path] = {}
    warnings: list[str] = []

    temporary_work = options.work_dir is None
    work_dir = (
        Path(options.work_dir)
        if options.work_dir
        else Path(tempfile.mkdtemp(prefix="autorush_"))
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # -------------------------------------------------------------- #
        # 1. import
        # -------------------------------------------------------------- #
        reporter.stage("import", f"Lecture de {input_path.name}")
        media = probe_media(input_path)
        reporter.sub(0.5, f"{media.width}x{media.height} · {media.fps:.2f} fps")
        _check_cancel(options)

        audio_wav = work_dir / "audio16k.wav"
        extract_audio(
            media.path,
            audio_wav,
            sample_rate=16000,
            mono=True,
            duration=media.duration,
            on_progress=lambda f: reporter.sub(0.5 + 0.5 * f),
        )
        _check_cancel(options)

        # -------------------------------------------------------------- #
        # 2. transcription
        # -------------------------------------------------------------- #
        reporter.stage("transcription", "Reconnaissance de la parole")
        if options.transcript_path:
            transcript = load_transcript(options.transcript_path)
            if transcript.duration <= 0:
                transcript.duration = media.duration
            reporter.sub(1.0, f"transcription fournie : {transcript.word_count()} mots")
        else:
            from autorush.transcription.whisper_backend import WhisperBackend

            backend = WhisperBackend(settings.transcription)
            transcript = backend.transcribe(
                audio_wav,
                media_path=media.path,
                duration=media.duration,
                on_progress=lambda f: reporter.sub(f),
            )
        _check_cancel(options)

        if options.save_transcript:
            outputs["transcript"] = save_transcript(
                transcript, output_dir / f"{basename}_transcription.json"
            )
        if options.write_srt:
            outputs["srt"] = write_srt(
                transcript, output_dir / f"{basename}_transcription.srt"
            )

        # -------------------------------------------------------------- #
        # 3. analyse (audio + hesitations)
        # -------------------------------------------------------------- #
        reporter.stage("analyse", "Analyse du son et des hesitations")
        profile: AudioProfile | None = None
        if options.audio_analysis:
            try:
                profile = AudioProfile.from_file(audio_wav)
                reporter.sub(0.6, "enveloppe audio calculee")
            except Exception as exc:  # pragma: no cover - audio exotique
                warnings.append(f"Analyse audio indisponible : {exc}")
                log.warning("Analyse audio ignoree : %s", exc)
        _check_cancel(options)

        # -------------------------------------------------------------- #
        # 4. reprises + 5. montage (une seule passe de decision)
        # -------------------------------------------------------------- #
        reporter.stage("reprises", "Detection des reprises de phrases")
        analysis = analyze(
            transcript, settings, profile=profile, media_duration=media.duration
        )
        reporter.sub(1.0, f"{len(analysis.retake_groups)} reprise(s) detectee(s)")
        _check_cancel(options)

        reporter.stage("montage", "Construction de la timeline")
        analysis.seams = check_seams(
            analysis.timeline,
            analysis.utterances,
            analysis.removed_word_indices,
            settings.seam,
            analysis.benign_word_indices,
        )
        reporter.sub(
            1.0,
            f"{len(analysis.timeline.shots)} plans, "
            f"{len(analysis.seams)} raccord(s) a verifier",
        )
        _check_cancel(options)

        # -------------------------------------------------------------- #
        # 6. zooms
        # -------------------------------------------------------------- #
        reporter.stage("zooms", "Placement des zooms")
        zooms = plan_zooms(analysis.timeline, settings.zoom, settings.seed)
        reporter.sub(1.0, f"{len(zooms.events)} zoom(s) place(s)")
        _check_cancel(options)

        # -------------------------------------------------------------- #
        # 7. export
        # -------------------------------------------------------------- #
        reporter.stage("export", "Ecriture des fichiers")
        export = settings.export
        step = 0.0
        total_steps = (
            sum(
                [
                    export.write_premiere_xml,
                    export.write_edl,
                    export.write_json,
                    export.write_report_html,
                    export.write_report_markdown,
                ]
            )
            + (1 if options.make_preview else 0)
            + (1 if (export.write_premiere_xml and options.write_alternate_xml) else 0)
        ) or 1

        def advance(detail: str) -> None:
            nonlocal step
            step += 1.0
            reporter.sub(min(0.999, step / total_steps), detail)

        sequence_name = export.sequence_name.format(name=Path(input_path).stem)

        if export.write_premiere_xml:
            outputs["premiere_xml"] = write_premiere_xml(
                output_dir / f"{basename}_premiere.xml",
                media,
                analysis.timeline,
                zooms,
                sequence_name=sequence_name,
                fps=export.fps,
                width=export.width,
                height=export.height,
                audio_crossfade=export.audio_crossfade,
                crossfade_frames=export.audio_crossfade_frames,
                audio_level_fallback=export.audio_level_fallback,
                keyframe_time_base=export.keyframe_time_base,
                max_keyframes=settings.zoom.max_keyframes,
                keyframe_tolerance=settings.zoom.keyframe_tolerance,
            )
            advance("sequence Premiere")

            if options.write_alternate_xml:
                other = "clip" if export.keyframe_time_base == "source" else "source"
                outputs["premiere_xml_alt"] = write_premiere_xml(
                    output_dir / f"{basename}_premiere_keyframes-{other}.xml",
                    media,
                    analysis.timeline,
                    zooms,
                    sequence_name=f"{sequence_name} (keyframes {other})",
                    fps=export.fps,
                    width=export.width,
                    height=export.height,
                    audio_crossfade=export.audio_crossfade,
                    crossfade_frames=export.audio_crossfade_frames,
                    audio_level_fallback=export.audio_level_fallback,
                    keyframe_time_base=other,
                    max_keyframes=settings.zoom.max_keyframes,
                    keyframe_tolerance=settings.zoom.keyframe_tolerance,
                )
                advance("variante de keyframes")

        if export.write_edl:
            outputs["edl"] = write_edl(
                output_dir / f"{basename}.edl",
                analysis.timeline,
                title=sequence_name,
                fps=media.fps,
                source_name=media.path.name,
            )
            advance("EDL")

        if export.write_json:
            outputs["json"] = write_json_edl(
                output_dir / f"{basename}_montage.json",
                media,
                settings,
                analysis,
                zooms,
                analysis.seams,
            )
            advance("JSON de montage")

        if export.write_report_html:
            outputs["report_html"] = write_html_report(
                output_dir / f"{basename}_rapport.html", media, settings, analysis, zooms
            )
            advance("rapport HTML")

        if export.write_report_markdown:
            outputs["report_md"] = write_markdown_report(
                output_dir / f"{basename}_rapport.md", media, settings, analysis, zooms
            )
            advance("rapport Markdown")

        if options.make_preview:
            from autorush.export.preview import render_preview

            _check_cancel(options)
            reporter.sub(step / total_steps, "rendu de la preview MP4")
            preview_base = step / total_steps
            preview_share = 1.0 / total_steps

            def preview_progress(fraction: float) -> None:
                reporter.sub(
                    min(0.999, preview_base + preview_share * fraction),
                    "rendu de la preview MP4",
                )

            try:
                result = render_preview(
                    output_dir / f"{basename}_preview.mp4",
                    media,
                    analysis.timeline,
                    zooms,
                    work_dir=work_dir / "preview",
                    height=export.preview_height,
                    crf=export.preview_crf,
                    preset=export.preview_preset,
                    audio_bitrate=export.preview_audio_bitrate,
                    threads=export.preview_threads,
                    max_keyframes=settings.zoom.max_keyframes,
                    keyframe_tolerance=settings.zoom.keyframe_tolerance,
                    focus_x=settings.zoom.focus_x,
                    focus_y=settings.zoom.focus_y,
                    on_progress=preview_progress,
                )
                outputs["preview"] = result.path
            except AutoRushError as exc:
                warnings.append(f"Preview non generee : {exc.message}")
                log.error("Preview non generee : %s", exc.message)
            advance("preview")

        reporter.done()

    finally:
        if temporary_work:
            shutil.rmtree(work_dir, ignore_errors=True)

    elapsed = time.time() - started
    result = PipelineResult(
        media=media,
        transcript=transcript,
        analysis=analysis,
        zooms=zooms,
        outputs=outputs,
        elapsed=elapsed,
        output_dir=output_dir,
        warnings=warnings,
    )
    log.info("Traitement termine en %s | %s", human_duration(elapsed), result.summary)
    return result
