"""Interface en ligne de commande d'AutoRush.

    autorush rush.mp4 --style dynamique --preview
    autorush process rush.mp4 --zoom-intensity 70 --out C:\\Montages
    autorush doctor
    autorush demo
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from autorush.config import Settings, resolve_style, style_label
from autorush.errors import AutoRushError, CancelledError
from autorush.logging_setup import setup_logging
from autorush.pipeline import PipelineOptions, Progress, run_pipeline
from autorush.utils import format_timecode, human_duration
from autorush.version import APP_NAME, __version__

STYLE_CHOICES = ["naturel", "dynamique", "tres_dynamique"]


# --------------------------------------------------------------------------- #
# Affichage
# --------------------------------------------------------------------------- #
def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


class Console:
    """Sorties utilisateur (distinctes du journal technique)."""

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self.color = _supports_color()
        self._last_length = 0

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def write(self, text: str = "") -> None:
        if not self.quiet:
            print(text)

    def title(self, text: str) -> None:
        self.write()
        self.write(self._paint(text, "1"))
        self.write(self._paint("\u2500" * min(72, max(12, len(text))), "2"))

    def item(self, key: str, value: str) -> None:
        self.write(f"  {self._paint(key.ljust(26), '2')} {value}")

    def ok(self, text: str) -> None:
        self.write(f"  {self._paint('OK', '32;1')}   {text}")

    def warn(self, text: str) -> None:
        self.write(f"  {self._paint('!', '33;1')}    {text}")

    def fail(self, text: str) -> None:
        self.write(f"  {self._paint('X', '31;1')}    {text}")

    def progress(self, progress: Progress) -> None:
        if self.quiet:
            return
        width = 26
        filled = int(round(width * progress.fraction))
        bar = "\u2588" * filled + "\u00b7" * (width - filled)
        detail = progress.detail[:44]
        line = f"  [{bar}] {progress.percent:3d} %  {progress.label:<14} {detail}"
        padding = max(0, self._last_length - len(line))
        sys.stdout.write("\r" + line + " " * padding)
        sys.stdout.flush()
        self._last_length = len(line)

    def end_progress(self) -> None:
        if self.quiet:
            return
        sys.stdout.write("\r" + " " * self._last_length + "\r")
        sys.stdout.flush()
        self._last_length = 0


# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autorush",
        description=(
            f"{APP_NAME} \u2013 montage automatique de rushes facecam : silences, "
            "hesitations, reprises de phrases et zooms, exportes vers Premiere Pro."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  autorush rush.mp4\n"
            "  autorush rush.mp4 --style tres_dynamique --zoom-intensity 80 --preview\n"
            "  autorush rush.mov --silences-only --out D:\\Montages\n"
            "  autorush doctor\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    process = subparsers.add_parser(
        "process", help="traiter un rush (commande par defaut)"
    )
    _add_process_arguments(process)

    subparsers.add_parser("doctor", help="verifier l'installation (ffmpeg, moteur, GPU)")
    subparsers.add_parser("gui", help="ouvrir l'interface graphique")

    demo = subparsers.add_parser(
        "demo", help="produire un montage de demonstration (sans video)"
    )
    demo.add_argument("--out", type=Path, default=Path("demo_autorush"))
    demo.add_argument("--style", choices=STYLE_CHOICES, default="dynamique")
    demo.add_argument("--zoom-intensity", type=float, default=None)

    cache = subparsers.add_parser("cache", help="gerer le cache de transcription")
    cache.add_argument("--clear", action="store_true", help="vider le cache")

    return parser


#: sous-commandes reconnues ; tout le reste est considere comme un fichier
COMMANDS = frozenset({"process", "doctor", "gui", "demo", "cache"})


def normalize_argv(argv: list[str]) -> list[str]:
    """Permet d'ecrire ``autorush rush.mp4`` sans taper ``process``.

    On cherche le premier argument qui n'est pas une option : s'il ne designe
    pas une sous-commande connue, c'est un fichier, et on insere ``process``.
    """
    for token in argv:
        if token in ("-h", "--help", "--version"):
            return list(argv)
        if token.startswith("-"):
            continue
        return list(argv) if token in COMMANDS else ["process", *argv]
    return list(argv)


def _add_process_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="fichier video a monter (MP4, MOV, MKV...)",
    )
    group = parser.add_argument_group("montage")
    group.add_argument(
        "-s", "--style", choices=STYLE_CHOICES, default="dynamique",
        help="rythme du montage (defaut : dynamique)",
    )
    group.add_argument(
        "--silences-only", action="store_true",
        help="ne toucher qu'aux silences : aucune parole supprimee",
    )
    group.add_argument(
        "--keep-all", action="store_true",
        help="ne rien supprimer, seulement analyser et signaler",
    )
    group.add_argument(
        "--no-retakes", action="store_true", help="desactiver la detection de reprises"
    )
    group.add_argument(
        "--no-fragments", action="store_true", help="desactiver la suppression de fragments"
    )
    group.add_argument(
        "--no-disfluency", action="store_true", help="garder les hesitations"
    )
    group.add_argument(
        "--soft-fillers", action="store_true",
        help="retirer aussi les tics de langage (du coup, genre, like...)",
    )
    group.add_argument(
        "--min-confidence", type=float, default=None,
        help="confiance minimale pour supprimer de la parole (0-1)",
    )

    zoom = parser.add_argument_group("zooms")
    zoom.add_argument(
        "-z", "--zoom-intensity", type=float, default=None,
        help="intensite des zooms de 0 a 100 (defaut : selon le style)",
    )
    zoom.add_argument("--no-zoom", action="store_true", help="aucun zoom")
    zoom.add_argument(
        "--zoom-focus-y", type=float, default=None,
        help="hauteur du point de recadrage (0 = haut, 0.5 = centre)",
    )

    transcription = parser.add_argument_group("transcription")
    transcription.add_argument(
        "-l", "--lang", default="auto",
        help="fr, en, auto, ou multi pour une video bilingue (defaut : auto)",
    )
    transcription.add_argument(
        "-m", "--model", default="large-v3",
        help="modele Whisper : tiny, base, small, medium, large-v3 (defaut : large-v3)",
    )
    transcription.add_argument(
        "--device", choices=["auto", "cpu", "cuda"], default="auto"
    )
    transcription.add_argument(
        "--transcript", type=Path, default=None,
        help="reutiliser une transcription (.json ou .srt) au lieu de la recalculer",
    )
    transcription.add_argument(
        "--no-cache", action="store_true", help="ignorer le cache de transcription"
    )

    output = parser.add_argument_group("sorties")
    output.add_argument(
        "-o", "--out", type=Path, default=None,
        help="dossier de sortie (defaut : AutoRush_out a cote du rush)",
    )
    output.add_argument("--name", default="", help="prefixe des fichiers produits")
    output.add_argument(
        "-p", "--preview", action="store_true", help="generer une preview MP4"
    )
    output.add_argument("--preview-height", type=int, default=720)
    output.add_argument("--no-xml", action="store_true", help="ne pas ecrire le XML Premiere")
    output.add_argument("--no-edl", action="store_true")
    output.add_argument("--no-report", action="store_true")
    output.add_argument(
        "--no-alternate-xml", action="store_true",
        help="ne pas ecrire la variante de keyframes du XML",
    )
    output.add_argument(
        "--keyframe-time-base", choices=["source", "clip"], default="source",
        help="base de temps des keyframes du XML (voir docs/PREMIERE.md)",
    )
    output.add_argument("--no-crossfade", action="store_true", help="pas de fondu audio")
    output.add_argument("--fps", type=float, default=0.0, help="forcer la cadence de la sequence")

    misc = parser.add_argument_group("divers")
    misc.add_argument("--settings", type=Path, default=None, help="charger des reglages JSON")
    misc.add_argument("--save-settings", type=Path, default=None, help="enregistrer les reglages")
    misc.add_argument("--seed", type=int, default=None, help="graine de variete des zooms")
    misc.add_argument("--no-audio-analysis", action="store_true")
    misc.add_argument("--json", action="store_true", help="resume machine sur la sortie standard")
    misc.add_argument("--open", action="store_true", help="ouvrir le dossier de sortie a la fin")
    misc.add_argument("-q", "--quiet", action="store_true")
    misc.add_argument("-v", "--verbose", action="store_true")
    misc.add_argument("--log-file", type=Path, default=None)


# --------------------------------------------------------------------------- #
# Reglages
# --------------------------------------------------------------------------- #
def settings_from_args(args: argparse.Namespace) -> Settings:
    """Construit les reglages : preset de style puis surcharges explicites."""
    base = Settings.load(args.settings) if getattr(args, "settings", None) else Settings()
    settings = base.with_style(resolve_style(args.style))

    if getattr(args, "silences_only", False):
        settings.silences_only = True
    if getattr(args, "keep_all", False):
        settings.dry_run_decisions = True
    if getattr(args, "no_retakes", False):
        settings.retake.enabled = False
    if getattr(args, "no_fragments", False):
        settings.fragment.enabled = False
    if getattr(args, "no_disfluency", False):
        settings.disfluency.remove_fillers = False
        settings.disfluency.remove_stutters = False
        settings.disfluency.remove_abandoned_words = False
    if getattr(args, "soft_fillers", False):
        settings.disfluency.remove_soft_fillers = True
    if getattr(args, "min_confidence", None) is not None:
        value = max(0.0, min(1.0, args.min_confidence))
        settings.retake.min_delete_confidence = value
        settings.fragment.min_delete_confidence = value
        settings.disfluency.min_delete_confidence = value

    if getattr(args, "no_zoom", False):
        settings.zoom.enabled = False
    if getattr(args, "zoom_intensity", None) is not None:
        settings.zoom.intensity = max(0.0, min(100.0, args.zoom_intensity))
    if getattr(args, "zoom_focus_y", None) is not None:
        settings.zoom.focus_y = max(0.0, min(1.0, args.zoom_focus_y))

    settings.transcription.language = args.lang
    settings.transcription.model = args.model
    settings.transcription.device = args.device
    if getattr(args, "no_cache", False):
        settings.transcription.use_cache = False

    settings.export.write_preview = bool(getattr(args, "preview", False))
    settings.export.preview_height = int(getattr(args, "preview_height", 720) or 720)
    if getattr(args, "no_xml", False):
        settings.export.write_premiere_xml = False
    if getattr(args, "no_edl", False):
        settings.export.write_edl = False
    if getattr(args, "no_report", False):
        settings.export.write_report_html = False
        settings.export.write_report_markdown = False
    if getattr(args, "no_crossfade", False):
        settings.export.audio_crossfade = False
    settings.export.keyframe_time_base = getattr(args, "keyframe_time_base", "source")
    settings.export.fps = float(getattr(args, "fps", 0.0) or 0.0)

    if getattr(args, "seed", None) is not None:
        settings.seed = int(args.seed)
    return settings


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #
def command_process(args: argparse.Namespace) -> int:
    console = Console(quiet=args.quiet)
    settings = settings_from_args(args)

    if args.save_settings:
        settings.save(args.save_settings)
        console.write(f"Reglages enregistres : {args.save_settings}")

    options = PipelineOptions(
        input_path=Path(args.input),
        output_dir=args.out,
        settings=settings,
        transcript_path=args.transcript,
        make_preview=bool(args.preview),
        basename=args.name,
        audio_analysis=not args.no_audio_analysis,
        write_alternate_xml=not args.no_alternate_xml,
    )

    console.title(f"{APP_NAME} {__version__}")
    console.item("Rush", str(options.input_path))
    console.item("Style", style_label(settings.style))
    console.item(
        "Zooms",
        "desactives"
        if not settings.zoom.enabled
        else f"intensite {settings.zoom.intensity:.0f} / 100",
    )
    console.item("Preview MP4", "oui" if options.make_preview else "non")
    console.item("Sortie", str(options.resolved_output_dir()))
    console.write()

    try:
        result = run_pipeline(options, on_progress=console.progress)
    except CancelledError:
        console.end_progress()
        console.warn("Traitement annule.")
        return 130
    except AutoRushError as exc:
        console.end_progress()
        console.fail(exc.user_text())
        return 2
    console.end_progress()

    stats = result.analysis.stats
    console.title("Resultat")
    console.item("Duree d'origine", human_duration(stats["source_duration"]))
    console.item(
        "Duree montee",
        f"{human_duration(stats['final_duration'])} "
        f"({stats['compression_ratio'] * 100:.0f} % du rush)",
    )
    console.item("Plans / coupes", f"{stats['shots']} / {stats['cuts']}")
    counts = stats["counts"]
    console.item("Hesitations retirees", str(counts["hesitations"]))
    console.item(
        "Mauvaises prises retirees",
        f"{counts['reprises']} (dans {counts['retake_groups']} reprise(s))",
    )
    console.item("Corrections orales retirees", str(counts["corrections"]))
    console.item("Fragments retires", str(counts["fragments"]))
    console.item("Zooms", f"{len(result.zooms.events)} {result.zooms.count_by_kind()}")
    console.item("Duree du traitement", human_duration(result.elapsed))

    if result.zooms.events:
        console.title("Zooms places")
        for event in result.zooms.events:
            console.write(
                f"  {format_timecode(event.timeline_start)} \u2014 {event.label} "
                f"({event.start_scale:.0f} % \u2192 {event.end_scale:.0f} %)"
            )

    to_check = len(result.analysis.flags) + len(result.analysis.seams)
    if to_check:
        console.title(f"A verifier ({to_check})")
        for flag in sorted(result.analysis.flags, key=lambda f: f.start)[:12]:
            console.write(
                f"  {format_timecode(flag.start)} [{flag.category}] "
                f"\u00ab {flag.text[:64]} \u00bb"
            )
        for seam in result.analysis.seams[:12]:
            console.write(
                f"  {format_timecode(seam.timeline_time)} [{seam.category}] {seam.reason[:70]}"
            )
        console.write("  \u2192 detail complet dans le rapport HTML")

    console.title("Fichiers produits")
    for key, path in result.outputs.items():
        console.item(key, str(path))

    for warning in result.warnings:
        console.warn(warning)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "summary": result.summary,
                    "stats": stats,
                    "zooms": result.zooms.as_dict(),
                    "outputs": {k: str(v) for k, v in result.outputs.items()},
                    "elapsed": round(result.elapsed, 2),
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
                indent=1,
            )
        )

    if args.open:
        _open_folder(result.output_dir)
    return 0


def _open_folder(path: Path) -> None:  # pragma: no cover - dependant du systeme
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


def command_doctor(args: argparse.Namespace) -> int:
    console = Console(quiet=False)
    console.title(f"{APP_NAME} {__version__} \u2013 verification de l'installation")
    problems = 0

    console.write(f"  Python {sys.version.split()[0]} ({sys.platform})")

    from autorush.media.ffmpeg import find_ffmpeg, find_ffprobe, reset_cache

    reset_cache()
    ffmpeg = find_ffmpeg(required=False)
    if ffmpeg:
        console.ok(f"ffmpeg : {ffmpeg}")
    else:
        console.fail("ffmpeg introuvable \u2014 indispensable pour lire la video")
        problems += 1
    ffprobe = find_ffprobe(required=False)
    if ffprobe:
        console.ok(f"ffprobe : {ffprobe}")
    else:
        console.fail("ffprobe introuvable")
        problems += 1

    for module, package, label, required in (
        ("numpy", "numpy", "numpy (analyse audio)", True),
        ("soundfile", "soundfile", "soundfile (lecture WAV)", True),
        ("faster_whisper", "faster-whisper", "faster-whisper (transcription)", True),
        ("PySide6", "PySide6", "PySide6 (interface graphique)", False),
    ):
        try:
            __import__(module)
            console.ok(label)
        except ImportError:
            if required:
                console.fail(f"{label} \u2014 manquant : pip install {package}")
                problems += 1
            else:
                console.warn(f"{label} \u2014 absent (interface graphique indisponible)")

    from autorush.transcription.whisper_backend import resolve_device

    device, compute = resolve_device("auto")
    if device == "cuda":
        console.ok(f"Acceleration GPU disponible ({compute})")
    else:
        console.warn(
            "Pas de GPU NVIDIA detecte : la transcription tournera sur processeur "
            "(comptez environ le tiers de la duree du rush avec le modele medium)"
        )

    from autorush.transcription.cache import cache_root

    root = cache_root()
    entries = len(list(root.glob("*.json"))) if root.exists() else 0
    console.item("Cache de transcription", f"{root} ({entries} entree(s))")

    console.write()
    if problems:
        console.fail(f"{problems} probleme(s) a corriger avant de monter un rush.")
    else:
        console.ok("Tout est pret.")
    del args
    return 1 if problems else 0


def command_demo(args: argparse.Namespace) -> int:
    """Produit un montage de demonstration sans avoir besoin d'une video."""
    from autorush.analysis.decisions import analyze
    from autorush.analysis.seams import check_seams
    from autorush.export.fcp7xml import write_premiere_xml
    from autorush.export.json_edl import write_json_edl
    from autorush.export.report import write_html_report, write_markdown_report
    from autorush.media.ffmpeg import MediaInfo
    from autorush.transcription.synthetic import build_demo_transcript
    from autorush.zoom.planner import plan_zooms

    console = Console()
    settings = Settings.for_style(args.style)
    if args.zoom_intensity is not None:
        settings.zoom.intensity = max(0.0, min(100.0, args.zoom_intensity))

    transcript = build_demo_transcript()
    media = MediaInfo(
        path=Path("rush_de_demonstration.mp4").resolve(),
        duration=transcript.duration,
        fps=25.0,
        timebase=25,
        ntsc=False,
        width=1920,
        height=1080,
        video_codec="h264",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_codec="aac",
    )

    analysis = analyze(transcript, settings, None, media.duration)
    analysis.seams = check_seams(
        analysis.timeline,
        analysis.utterances,
        analysis.removed_word_indices,
        settings.seam,
        analysis.benign_word_indices,
    )
    zooms = plan_zooms(analysis.timeline, settings.zoom, settings.seed)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    outputs = {
        "premiere_xml": write_premiere_xml(
            out / "demo_premiere.xml", media, analysis.timeline, zooms,
            sequence_name="Demonstration AutoRush",
        ),
        "json": write_json_edl(
            out / "demo_montage.json", media, settings, analysis, zooms, analysis.seams
        ),
        "report_html": write_html_report(
            out / "demo_rapport.html", media, settings, analysis, zooms
        ),
        "report_md": write_markdown_report(
            out / "demo_rapport.md", media, settings, analysis, zooms
        ),
    }

    console.title(f"{APP_NAME} \u2013 demonstration ({style_label(settings.style)})")
    console.item("Duree simulee", human_duration(analysis.stats["source_duration"]))
    console.item("Duree montee", human_duration(analysis.stats["final_duration"]))
    console.item("Plans", str(analysis.stats["shots"]))
    console.item("Zooms", f"{len(zooms.events)} {zooms.count_by_kind()}")
    console.write()
    console.write("  Texte du montage final :")
    removed = analysis.removed_word_indices
    text = " ".join(
        " ".join(w.clean for w in shot.words if w.index not in removed)
        for shot in analysis.timeline.shots
    )
    for line in _wrap(text, 74):
        console.write(f"    {line}")
    console.write()
    for key, path in outputs.items():
        console.item(key, str(path))
    return 0


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def command_cache(args: argparse.Namespace) -> int:
    from autorush.transcription.cache import TranscriptCache, cache_root

    console = Console()
    cache = TranscriptCache()
    if args.clear:
        removed = cache.clear()
        console.ok(f"{removed} transcription(s) supprimee(s) du cache.")
        return 0
    root = cache_root()
    entries = sorted(root.glob("*.json")) if root.exists() else []
    console.item("Dossier", str(root))
    console.item("Entrees", str(len(entries)))
    total = sum(entry.stat().st_size for entry in entries)
    console.item("Taille", f"{total / 1_048_576:.1f} Mo")
    console.write("  (utilisez --clear pour vider)")
    return 0


def command_gui(args: argparse.Namespace) -> int:  # pragma: no cover - interface
    del args
    try:
        from autorush.gui.app import main as gui_main
    except ImportError as exc:
        print(
            "L'interface graphique demande PySide6 :\n    pip install PySide6\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 2
    return gui_main()


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not raw:
        parser.print_help()
        return 1
    args = parser.parse_args(normalize_argv(raw))

    level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
    if getattr(args, "quiet", False):
        level = logging.ERROR
    setup_logging(level, getattr(args, "log_file", None))

    command = getattr(args, "command", None)
    if command == "doctor":
        return command_doctor(args)
    if command == "gui":
        return command_gui(args)
    if command == "demo":
        return command_demo(args)
    if command == "cache":
        return command_cache(args)

    if not getattr(args, "input", None):
        # ``--save-settings`` seul est un usage legitime : produire un fichier
        # de reglages a editer, sans lancer de traitement.
        if getattr(args, "save_settings", None):
            settings = settings_from_args(args)
            path = settings.save(args.save_settings)
            Console(quiet=args.quiet).ok(f"Reglages enregistres : {path}")
            return 0
        parser.print_help()
        return 1
    return command_process(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
