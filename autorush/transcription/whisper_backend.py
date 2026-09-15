"""Transcription par ``faster-whisper``.

``faster-whisper`` est une reimplementation de Whisper sur CTranslate2 : elle
donne les **horodatages par mot**, condition indispensable pour couper au bon
endroit, et tourne correctement sur processeur comme sur carte NVIDIA.

Langues
-------
* ``fr`` / ``en`` / ... : langue imposee, le plus rapide et le plus fiable ;
* ``auto`` : la langue est detectee une fois au debut ;
* ``multi`` : le rush est decoupe en blocs, la langue est detectee **par bloc**,
  puis chaque bloc est transcrit dans sa langue. C'est le mode a utiliser sur
  une video qui melange francais et anglais.
"""

from __future__ import annotations

import time
from pathlib import Path

from autorush.config import TranscriptionSettings
from autorush.errors import DependencyMissingError, TranscriptionError
from autorush.logging_setup import get_logger
from autorush.transcription.base import Segment, Transcript, Word
from autorush.transcription.cache import TranscriptCache, cache_key
from autorush.transcription.io import link_transcript

log = get_logger("transcription.whisper")

WHISPER_HINT = (
    "Installez le moteur de transcription :\n"
    "    pip install faster-whisper\n"
    "Le modele est telecharge automatiquement au premier lancement."
)

#: recouvrement entre deux blocs en mode multilingue (secondes)
CHUNK_OVERLAP = 0.6
#: mots detectes dans la zone de recouvrement et ignores
OVERLAP_MARGIN = 0.25


def _import_whisper():
    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415
    except ImportError as exc:
        raise DependencyMissingError(
            "Le moteur de transcription (faster-whisper) n'est pas installe.",
            WHISPER_HINT,
        ) from exc
    return WhisperModel


def resolve_device(requested: str = "auto") -> tuple[str, str]:
    """Retourne ``(device, compute_type)`` adaptes a la machine."""
    requested = (requested or "auto").lower()
    if requested == "cpu":
        return "cpu", "int8"
    if requested in ("cuda", "gpu"):
        return "cuda", "float16"

    try:
        import ctranslate2  # noqa: PLC0415

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:  # pragma: no cover - pas de CUDA, cas normal
        pass
    return "cpu", "int8"


class WhisperBackend:
    """Moteur de transcription."""

    def __init__(self, settings: TranscriptionSettings) -> None:
        self.settings = settings
        self._model = None
        self._device = ""
        self._compute_type = ""

    # ------------------------------------------------------------------ #
    @property
    def model(self):
        """Charge le modele a la premiere utilisation."""
        if self._model is None:
            WhisperModel = _import_whisper()
            device, compute = resolve_device(self.settings.device)
            if self.settings.compute_type != "auto":
                compute = self.settings.compute_type
            kwargs: dict = {"device": device, "compute_type": compute}
            if self.settings.cpu_threads > 0:
                kwargs["cpu_threads"] = self.settings.cpu_threads
            log.info(
                "Chargement du modele %s (%s / %s)...",
                self.settings.model, device, compute,
            )
            started = time.time()
            try:
                self._model = WhisperModel(self.settings.model, **kwargs)
            except Exception as exc:
                raise TranscriptionError(
                    f"Impossible de charger le modele « {self.settings.model} ».",
                    f"{exc}\n\nEssayez un modele plus petit (medium, small) ou "
                    "verifiez votre connexion : le modele est telecharge au "
                    "premier lancement.",
                ) from exc
            self._device, self._compute_type = device, compute
            log.info("Modele charge en %.1f s", time.time() - started)
        return self._model

    # ------------------------------------------------------------------ #
    def _transcribe_options(self, language: str | None) -> dict:
        settings = self.settings
        options: dict = {
            "beam_size": settings.beam_size,
            "word_timestamps": settings.word_timestamps,
            "condition_on_previous_text": settings.condition_on_previous_text,
            "temperature": list(settings.temperature),
            "vad_filter": settings.vad_filter,
        }
        if settings.vad_filter:
            options["vad_parameters"] = {
                "min_silence_duration_ms": settings.vad_min_silence_ms
            }
        if language:
            options["language"] = language
        if settings.initial_prompt:
            options["initial_prompt"] = settings.initial_prompt
        return options

    # ------------------------------------------------------------------ #
    def transcribe(
        self,
        audio_path: str | Path,
        media_path: str | Path | None = None,
        duration: float = 0.0,
        on_progress=None,
    ) -> Transcript:
        """Transcrit un fichier audio et retourne une ``Transcript``."""
        audio_path = Path(audio_path)
        cache = TranscriptCache()
        key = ""
        if self.settings.use_cache:
            reference = Path(media_path) if media_path else audio_path
            key = cache_key(reference, self.settings)
            cached = cache.load(key)
            if cached is not None:
                if on_progress:
                    on_progress(1.0)
                return cached

        mode = (self.settings.language or "auto").lower()
        started = time.time()
        if mode == "multi":
            transcript = self._transcribe_multilingual(audio_path, duration, on_progress)
        else:
            language = None if mode in ("auto", "", "none") else mode
            transcript = self._transcribe_single(
                audio_path, language, duration, on_progress
            )

        transcript.model = self.settings.model
        transcript.meta.update(
            {
                "device": self._device,
                "compute_type": self._compute_type,
                "elapsed_seconds": round(time.time() - started, 2),
                "language_mode": mode,
            }
        )
        if duration > 0:
            transcript.duration = max(transcript.duration, duration)

        if not transcript.words:
            raise TranscriptionError(
                "Aucune parole n'a ete reconnue dans ce rush.",
                "Verifiez que la piste audio contient bien de la voix, et que "
                "le volume n'est pas nul.",
            )

        log.info(
            "Transcription : %d mots, %d segments, langue(s) %s, %.1f s de calcul",
            len(transcript.words), len(transcript.segments),
            ", ".join(transcript.languages) or transcript.language,
            transcript.meta.get("elapsed_seconds", 0.0),
        )
        if self.settings.use_cache and key:
            cache.save(key, transcript)
        return transcript

    # ------------------------------------------------------------------ #
    def _transcribe_single(
        self,
        audio_path: Path,
        language: str | None,
        duration: float,
        on_progress=None,
    ) -> Transcript:
        options = self._transcribe_options(language)
        # faster-whisper >= 1.1 sait gerer le changement de langue en cours de
        # route : on active l'option si elle existe.
        if language is None:
            options.setdefault("multilingual", True)

        try:
            segments, info = self._run(audio_path, options)
        except TypeError:
            options.pop("multilingual", None)
            segments, info = self._run(audio_path, options)

        transcript = Transcript()
        transcript.language = getattr(info, "language", "") or (language or "")
        total = duration or float(getattr(info, "duration", 0.0) or 0.0)

        for raw in segments:
            transcript.segments.append(self._convert_segment(raw, transcript.language))
            if on_progress and total > 0:
                on_progress(min(0.999, float(raw.end) / total))

        link_transcript(transcript)
        if on_progress:
            on_progress(1.0)
        return transcript

    def _run(self, audio_path: Path, options: dict):
        try:
            return self.model.transcribe(str(audio_path), **options)
        except TypeError:
            raise
        except Exception as exc:
            raise TranscriptionError(
                "La transcription a echoue.", str(exc)
            ) from exc

    # ------------------------------------------------------------------ #
    def _transcribe_multilingual(
        self, audio_path: Path, duration: float, on_progress=None
    ) -> Transcript:
        """Transcription bloc par bloc, avec detection de langue par bloc."""
        chunk = max(10.0, float(self.settings.multilang_chunk))
        total = duration
        if total <= 0:
            try:
                import soundfile as sf  # noqa: PLC0415

                with sf.SoundFile(str(audio_path)) as handle:
                    total = len(handle) / float(handle.samplerate)
            except Exception:  # pragma: no cover
                total = 0.0
        if total <= 0:
            log.warning("Duree inconnue : repli sur une transcription en une passe.")
            return self._transcribe_single(audio_path, None, duration, on_progress)

        boundaries: list[tuple[float, float]] = []
        cursor = 0.0
        while cursor < total:
            end = min(total, cursor + chunk)
            boundaries.append((cursor, end))
            cursor = end

        transcript = Transcript()
        language_durations: dict[str, float] = {}

        for position, (start, end) in enumerate(boundaries):
            window_start = max(0.0, start - (CHUNK_OVERLAP if position else 0.0))
            options = self._transcribe_options(None)
            options["clip_timestamps"] = [window_start, min(total, end + CHUNK_OVERLAP)]
            try:
                segments, info = self._run(audio_path, options)
            except TypeError:
                # ``clip_timestamps`` absent de cette version : repli global
                log.warning(
                    "Cette version de faster-whisper ne gere pas le decoupage : "
                    "repli sur une transcription en une passe."
                )
                return self._transcribe_single(audio_path, None, duration, on_progress)

            language = getattr(info, "language", "") or ""
            for raw in segments:
                converted = self._convert_segment(raw, language)
                # on ignore ce qui appartient au bloc precedent
                converted.words = [
                    word
                    for word in converted.words
                    if word.start >= start - OVERLAP_MARGIN
                    and word.start < end + OVERLAP_MARGIN
                ]
                if not converted.words:
                    continue
                converted.start = converted.words[0].start
                converted.end = converted.words[-1].end
                converted.text = " ".join(w.text for w in converted.words)
                transcript.segments.append(converted)
                language_durations[language] = language_durations.get(
                    language, 0.0
                ) + converted.duration

            if on_progress:
                on_progress(min(0.999, (position + 1) / len(boundaries)))

        transcript.languages = [
            code
            for code, _ in sorted(language_durations.items(), key=lambda kv: -kv[1])
            if code
        ]
        transcript.language = transcript.languages[0] if transcript.languages else ""
        link_transcript(transcript)
        if on_progress:
            on_progress(1.0)
        return transcript

    # ------------------------------------------------------------------ #
    @staticmethod
    def _convert_segment(raw, language: str) -> Segment:
        """Convertit un segment faster-whisper en ``Segment`` AutoRush."""
        words: list[Word] = []
        for raw_word in getattr(raw, "words", None) or []:
            text = getattr(raw_word, "word", "") or ""
            if not text.strip():
                continue
            words.append(
                Word(
                    text=text.strip(),
                    start=float(getattr(raw_word, "start", 0.0) or 0.0),
                    end=float(getattr(raw_word, "end", 0.0) or 0.0),
                    probability=float(getattr(raw_word, "probability", 1.0) or 1.0),
                    language=language,
                )
            )
        return Segment(
            start=float(getattr(raw, "start", 0.0) or 0.0),
            end=float(getattr(raw, "end", 0.0) or 0.0),
            text=str(getattr(raw, "text", "") or "").strip(),
            words=words,
            language=language,
            avg_logprob=float(getattr(raw, "avg_logprob", 0.0) or 0.0),
            no_speech_prob=float(getattr(raw, "no_speech_prob", 0.0) or 0.0),
        )


def transcribe_file(
    audio_path: str | Path,
    settings: TranscriptionSettings,
    media_path: str | Path | None = None,
    duration: float = 0.0,
    on_progress=None,
) -> Transcript:
    """Raccourci : cree un moteur et transcrit."""
    backend = WhisperBackend(settings)
    return backend.transcribe(
        audio_path, media_path=media_path, duration=duration, on_progress=on_progress
    )
