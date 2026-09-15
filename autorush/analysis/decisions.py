"""Assemblage des decisions de montage.

Ce module est le chef d'orchestre de l'analyse. Il applique l'ordre suivant :

1. decoupage en enonces ;
2. detection des hesitations ;
3. detection des reprises de phrase ;
4. detection des fragments orphelins (qui a besoin du resultat de l'etape 3) ;
5. filet de securite global (part maximale de parole supprimable) ;
6. traitement des blancs, qui absorbe tout ce qui precede ;
7. construction de la timeline.

Regle d'or : une decision dont la confiance est inferieure au seuil n'est pas
appliquee. Elle devient un **signalement** dans le rapport et le segment reste
dans le montage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.analysis.disfluency import DisfluencyFinding, detect_disfluencies
from autorush.analysis.fragments import FragmentFinding, detect_fragments
from autorush.analysis.retakes import RetakeAttempt, RetakeGroup, detect_retakes
from autorush.analysis.silences import GapDecision, plan_silences
from autorush.analysis.silences import summarize as summarize_gaps
from autorush.analysis.utterances import Utterance, build_utterances
from autorush.config import Settings
from autorush.editing.timeline import Timeline, build_timeline
from autorush.logging_setup import get_logger
from autorush.media.audio import AudioProfile
from autorush.transcription.base import Transcript
from autorush.utils import format_timecode, human_duration

log = get_logger("analysis.decisions")

#: libelles lisibles des origines de suppression
SOURCE_LABELS: dict[str, str] = {
    "filler": "hesitation",
    "filler_phrase": "locution d'hesitation",
    "stutter": "bafouillage",
    "abandoned": "mot abandonne",
    "retake": "mauvaise prise",
    "marker": "correction orale",
    "fragment": "fragment orphelin",
    "silence": "silence",
}


@dataclass
class Removal:
    """Un segment de parole retire du montage."""

    start: float
    end: float
    source: str
    confidence: float
    reason: str
    text: str = ""
    applied: bool = True
    utterance_index: int = -1
    word_indices: list[int] = field(default_factory=list)
    kept_instead: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "timecode": format_timecode(self.start),
            "source": self.source,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "text": self.text,
            "kept_instead": self.kept_instead,
            "applied": self.applied,
            "utterance_index": self.utterance_index,
        }


@dataclass
class Flag:
    """Un point a verifier a la main."""

    start: float
    end: float
    category: str
    confidence: float
    reason: str
    text: str = ""
    suggestion: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "timecode": format_timecode(self.start),
            "category": self.category,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "text": self.text,
            "suggestion": self.suggestion,
        }


@dataclass
class AnalysisResult:
    """Tout ce que l'analyse a produit."""

    transcript: Transcript
    utterances: list[Utterance] = field(default_factory=list)
    disfluencies: list[DisfluencyFinding] = field(default_factory=list)
    retake_groups: list[RetakeGroup] = field(default_factory=list)
    lone_markers: list[RetakeAttempt] = field(default_factory=list)
    fragments: list[FragmentFinding] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    gaps: list[GapDecision] = field(default_factory=list)
    timeline: Timeline = field(default_factory=Timeline)
    removed_word_indices: set[int] = field(default_factory=set)
    #: mots retires pour une raison anodine (tic de parole) : une coupe qui ne
    #: retire que ceux-la ne casse pas la phrase.
    benign_word_indices: set[int] = field(default_factory=set)
    seams: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    @property
    def applied_removals(self) -> list[Removal]:
        return [r for r in self.removals if r.applied]

    def removals_by_source(self, source: str) -> list[Removal]:
        return [r for r in self.removals if r.applied and r.source == source]

    def flags_by_category(self, category: str) -> list[Flag]:
        return [f for f in self.flags if f.category == category]

    def as_dict(self) -> dict:
        return {
            "stats": self.stats,
            "timeline": self.timeline.as_dict(),
            "removals": [r.as_dict() for r in self.removals],
            "flags": [f.as_dict() for f in self.flags],
            "seams": [w.as_dict() for w in self.seams],
            "retakes": [g.as_dict() for g in self.retake_groups],
            "disfluencies": [d.as_dict() for d in self.disfluencies],
            "fragments": [f.as_dict() for f in self.fragments],
            "gaps": [g.as_dict() for g in self.gaps],
        }


# --------------------------------------------------------------------------- #
def _utterance_by_index(utterances: list[Utterance]) -> dict[int, Utterance]:
    return {u.index: u for u in utterances}


def _speech_duration_of(utterance: Utterance) -> float:
    return sum(w.duration for w in utterance.words)


def analyze(
    transcript: Transcript,
    settings: Settings,
    profile: AudioProfile | None = None,
    media_duration: float = 0.0,
) -> AnalysisResult:
    """Analyse complete d'une transcription et construction de la timeline."""
    result = AnalysisResult(transcript=transcript)
    if transcript.is_empty():
        result.timeline = Timeline(shots=[], source_duration=media_duration)
        result.stats = {"words": 0, "note": "transcription vide"}
        return result

    # ------------------------------------------------------------------ #
    # 1. enonces
    # ------------------------------------------------------------------ #
    utterances = build_utterances(transcript)
    result.utterances = utterances
    by_index = _utterance_by_index(utterances)
    log.info("%d enonces detectes", len(utterances))

    keep_everything = settings.silences_only or settings.dry_run_decisions

    # ------------------------------------------------------------------ #
    # 2. hesitations
    # ------------------------------------------------------------------ #
    if not settings.silences_only:
        result.disfluencies = detect_disfluencies(transcript, utterances, settings.disfluency)
    for finding in result.disfluencies:
        applied = (
            not keep_everything
            and finding.confidence >= settings.disfluency.min_delete_confidence
        )
        result.removals.append(
            Removal(
                start=finding.start,
                end=finding.end,
                source=finding.kind,
                confidence=finding.confidence,
                reason=finding.reason,
                text=finding.text,
                applied=applied,
                utterance_index=finding.utterance_index,
                word_indices=list(finding.word_indices),
                kept_instead=finding.kept_text,
            )
        )
        if not applied:
            result.flags.append(
                Flag(
                    start=finding.start,
                    end=finding.end,
                    category="hesitation_douteuse",
                    confidence=finding.confidence,
                    reason=finding.reason,
                    text=finding.text,
                    suggestion="conserve par prudence : a verifier a l'oreille",
                )
            )

    # ------------------------------------------------------------------ #
    # 3. reprises de phrase
    # ------------------------------------------------------------------ #
    if not settings.silences_only:
        groups, lone_markers = detect_retakes(utterances, settings.retake)
        result.retake_groups = groups
        result.lone_markers = lone_markers

        for group in groups:
            for attempt in group.attempts:
                applied = (
                    not keep_everything
                    and attempt.confidence >= settings.retake.min_delete_confidence
                )
                source = "marker" if attempt.role == "marker" else "retake"
                result.removals.append(
                    Removal(
                        start=attempt.start,
                        end=attempt.end,
                        source=source,
                        confidence=attempt.confidence,
                        reason=attempt.reason,
                        text=attempt.text,
                        applied=applied,
                        utterance_index=attempt.utterance_index,
                        word_indices=[
                            w.index for w in by_index[attempt.utterance_index].words
                        ],
                        kept_instead=group.kept_text,
                    )
                )
                if not applied:
                    result.flags.append(
                        Flag(
                            start=attempt.start,
                            end=attempt.end,
                            category="reprise_possible",
                            confidence=attempt.confidence,
                            reason=attempt.reason,
                            text=attempt.text,
                            suggestion=(
                                "ressemble a une mauvaise prise de : "
                                f"« {group.kept_text[:90]} »"
                            ),
                        )
                    )

        for marker in lone_markers:
            applied = (
                not keep_everything
                and marker.confidence >= settings.retake.min_delete_confidence
            )
            result.removals.append(
                Removal(
                    start=marker.start,
                    end=marker.end,
                    source="marker",
                    confidence=marker.confidence,
                    reason=marker.reason,
                    text=marker.text,
                    applied=applied,
                    utterance_index=marker.utterance_index,
                    word_indices=[w.index for w in by_index[marker.utterance_index].words],
                )
            )

    # ------------------------------------------------------------------ #
    # 4. fragments orphelins
    # ------------------------------------------------------------------ #
    removed_utterances = {
        r.utterance_index
        for r in result.removals
        if r.applied and r.source in {"retake", "marker"} and r.utterance_index >= 0
    }
    if not settings.silences_only:
        result.fragments = detect_fragments(
            utterances, removed_utterances, settings.fragment
        )
    for fragment in result.fragments:
        applied = (
            not keep_everything
            and fragment.confidence >= settings.fragment.min_delete_confidence
        )
        result.removals.append(
            Removal(
                start=fragment.start,
                end=fragment.end,
                source="fragment",
                confidence=fragment.confidence,
                reason=fragment.reason,
                text=fragment.text,
                applied=applied,
                utterance_index=fragment.utterance_index,
                word_indices=[w.index for w in by_index[fragment.utterance_index].words],
            )
        )
        if not applied:
            result.flags.append(
                Flag(
                    start=fragment.start,
                    end=fragment.end,
                    category="fragment_douteux",
                    confidence=fragment.confidence,
                    reason=fragment.reason,
                    text=fragment.text,
                    suggestion="fragment conserve : a supprimer a la main si inutile",
                )
            )

    # ------------------------------------------------------------------ #
    # 5. filet de securite : part maximale de parole supprimee
    # ------------------------------------------------------------------ #
    _apply_speech_cap(result, settings)

    # ------------------------------------------------------------------ #
    # 6. blancs
    # ------------------------------------------------------------------ #
    removed_words: set[int] = set()
    for removal in result.removals:
        if removal.applied:
            removed_words.update(removal.word_indices)
    result.removed_word_indices = removed_words
    result.benign_word_indices = {
        index
        for removal in result.removals
        if removal.applied
        and removal.source in {"filler", "filler_phrase", "stutter", "abandoned"}
        for index in removal.word_indices
    }

    result.gaps = plan_silences(
        transcript,
        removed_words,
        settings.silence,
        profile=profile,
        media_duration=media_duration,
    )

    intervals = [gap.removal for gap in result.gaps if gap.removal is not None]
    cut_reasons = {
        round(gap.removal[1], 3): gap.kind for gap in result.gaps if gap.removal
    }

    # ------------------------------------------------------------------ #
    # 7. timeline
    # ------------------------------------------------------------------ #
    result.timeline = build_timeline(
        transcript,
        intervals,
        settings.silence,
        source_duration=max(media_duration, transcript.duration),
        profile=profile,
        cut_reasons=cut_reasons,
    )

    result.stats = _build_stats(result, settings)
    log.info(
        "Montage : %s -> %s (%d plans, %d coupes)",
        human_duration(result.timeline.source_duration),
        human_duration(result.timeline.duration),
        len(result.timeline.shots),
        result.timeline.cut_count,
    )
    return result


# --------------------------------------------------------------------------- #
def _apply_speech_cap(result: AnalysisResult, settings: Settings) -> None:
    """Limite la part de parole supprimee par les reprises et les fragments.

    Si l'analyse veut retirer plus que la part autorisee, les decisions les
    moins sures sont annulees jusqu'a revenir sous la limite. On prefere un
    montage trop long a un montage qui perd du contenu.
    """
    by_index = _utterance_by_index(result.utterances)
    total_speech = result.transcript.speech_duration
    if total_speech <= 0:
        return

    if total_speech < settings.retake.cap_min_speech_duration:
        # Sur un extrait tres court, une seule reprise depasse n'importe quel
        # pourcentage : le filet n'aurait aucun sens.
        return

    candidates = [
        r
        for r in result.removals
        if r.applied and r.source in {"retake", "fragment", "marker"}
    ]
    if not candidates:
        return

    def speech_of(removal: Removal) -> float:
        utterance = by_index.get(removal.utterance_index)
        if utterance is None:
            return removal.duration
        return _speech_duration_of(utterance)

    removed_speech = sum(speech_of(r) for r in candidates)
    limit = settings.retake.max_removed_speech_ratio * total_speech
    if removed_speech <= limit:
        return

    # Les suppressions demandees explicitement par la personne ("je
    # recommence") ne sont jamais annulees : le filet ne protege que les
    # decisions incertaines.
    cancellable = [
        r for r in candidates if r.confidence < settings.retake.cap_exempt_confidence
    ]
    if not cancellable:
        log.info(
            "Beaucoup de parole supprimee (%.1f s), mais toutes les decisions "
            "sont explicites : le filet de securite n'intervient pas.",
            removed_speech,
        )
        return

    log.warning(
        "Filet de securite : %.1f s de parole visee pour une limite de %.1f s "
        "(%.0f %% du rush). Les decisions les moins sures sont annulees.",
        removed_speech, limit, 100 * settings.retake.max_removed_speech_ratio,
    )

    # on annule d'abord les decisions les moins sures
    for removal in sorted(cancellable, key=lambda r: (r.confidence, -r.duration)):
        if removed_speech <= limit:
            break
        removal.applied = False
        removed_speech -= speech_of(removal)
        result.flags.append(
            Flag(
                start=removal.start,
                end=removal.end,
                category="limite_securite",
                confidence=removal.confidence,
                reason=(
                    "annule par le filet de securite : trop de parole aurait ete "
                    "supprimee sur l'ensemble du rush"
                ),
                text=removal.text,
                suggestion="verifiez ce passage a la main",
            )
        )


def _build_stats(result: AnalysisResult, settings: Settings) -> dict:
    """Chiffres du rapport."""
    timeline = result.timeline
    applied = result.applied_removals
    speech_sources = {"retake", "marker", "fragment"}
    disfluency_sources = {"filler", "filler_phrase", "stutter", "abandoned"}

    removed_speech = sum(r.duration for r in applied if r.source in speech_sources)
    removed_disfluency = sum(r.duration for r in applied if r.source in disfluency_sources)
    silence_removed = sum(
        gap.removed for gap in result.gaps if gap.kind not in {"mauvaise_prise"}
    )

    return {
        "style": settings.style,
        "words": result.transcript.word_count(),
        "utterances": len(result.utterances),
        "source_duration": round(timeline.source_duration, 3),
        "final_duration": round(timeline.duration, 3),
        "removed_duration": round(timeline.removed_duration(), 3),
        "compression_ratio": (
            round(timeline.duration / timeline.source_duration, 4)
            if timeline.source_duration > 0
            else 0.0
        ),
        "shots": len(timeline.shots),
        "cuts": timeline.cut_count,
        "removed_speech_duration": round(removed_speech, 3),
        "removed_disfluency_duration": round(removed_disfluency, 3),
        "removed_silence_duration": round(silence_removed, 3),
        "counts": {
            "hesitations": len([r for r in applied if r.source in disfluency_sources]),
            "reprises": len([r for r in applied if r.source == "retake"]),
            "corrections": len([r for r in applied if r.source == "marker"]),
            "fragments": len([r for r in applied if r.source == "fragment"]),
            "retake_groups": len(result.retake_groups),
            "flags": len(result.flags),
        },
        "gaps": summarize_gaps(result.gaps),
        "removed_word_count": len(result.removed_word_indices),
        "kept_word_count": result.transcript.word_count()
        - len(result.removed_word_indices),
    }
