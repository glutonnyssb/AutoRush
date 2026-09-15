"""Nettoyage des hesitations et des bafouillages.

Quatre familles sont traitees, de la plus sure a la plus delicate :

1. ``filler``      - tics sonores ("euh", "uh") : suppression quasi certaine ;
2. ``filler_phrase`` - locutions d'hesitation ("enfin je veux dire") ;
3. ``stutter``     - repetition immediate d'un ou plusieurs mots ("je je") ;
4. ``abandoned``   - debut de mot abandonne ("Mal- Malgre").

Les repetitions d'intensite ("tres tres fort") et les doublons grammaticaux
sont explicitement proteges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.analysis.lexicon import (
    FILLER_PHRASES,
    GRAMMATICAL_DOUBLES,
    INTENSIFIERS,
    is_common_short_word,
    is_hard_filler,
    is_soft_filler,
    word_stem,
)
from autorush.analysis.utterances import Utterance
from autorush.config import DisfluencySettings
from autorush.transcription.base import Transcript, Word

#: gap maximal entre deux mots d'une meme repetition detectable
MAX_STUTTER_GAP_FACTOR = 1.0
#: longueur maximale (en mots) d'un groupe repete detecte
MAX_REPEATED_GROUP = 3


@dataclass
class DisfluencyFinding:
    """Une hesitation reperee dans la transcription."""

    kind: str
    #: index des mots a supprimer (dans ``Transcript.words``)
    word_indices: list[int]
    start: float
    end: float
    confidence: float
    reason: str
    text: str
    #: texte conserve a la place (pour le rapport)
    kept_text: str = ""
    utterance_index: int = -1
    tags: set[str] = field(default_factory=set)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "text": self.text,
            "kept_text": self.kept_text,
            "word_indices": list(self.word_indices),
            "utterance_index": self.utterance_index,
        }


# --------------------------------------------------------------------------- #
def _finding(
    kind: str,
    words: list[Word],
    confidence: float,
    reason: str,
    kept_text: str = "",
    utterance_index: int = -1,
) -> DisfluencyFinding:
    return DisfluencyFinding(
        kind=kind,
        word_indices=[w.index for w in words],
        start=words[0].start,
        end=words[-1].end,
        confidence=confidence,
        reason=reason,
        text=" ".join(w.clean for w in words),
        kept_text=kept_text,
        utterance_index=utterance_index,
    )


# --------------------------------------------------------------------------- #
# 1 & 2. tics sonores et locutions
# --------------------------------------------------------------------------- #
def _detect_fillers(
    utterance: Utterance, settings: DisfluencySettings
) -> list[DisfluencyFinding]:
    findings: list[DisfluencyFinding] = []
    words = utterance.words
    consumed: set[int] = set()

    # locutions d'hesitation (avant les mots isoles : elles les englobent)
    tokens = [w.norm for w in words]
    for phrase in FILLER_PHRASES:
        length = len(phrase)
        if length < 2 or length > len(tokens):
            continue
        for start in range(len(tokens) - length + 1):
            if any((start + k) in consumed for k in range(length)):
                continue
            if tuple(tokens[start : start + length]) != phrase:
                continue
            group = words[start : start + length]
            # on ne vide jamais completement un enonce porteur de sens
            remaining = [
                w
                for i, w in enumerate(words)
                if not (start <= i < start + length) and w.norm
            ]
            if not remaining:
                continue
            findings.append(
                _finding(
                    "filler_phrase",
                    group,
                    0.80,
                    f"locution d'hesitation « {' '.join(phrase)} »",
                    utterance_index=utterance.index,
                )
            )
            consumed.update(range(start, start + length))

    # tics sonores isoles
    for position, word in enumerate(words):
        if position in consumed:
            continue
        token = word.norm
        if not token:
            continue
        hard = is_hard_filler(token)
        soft = is_soft_filler(token) or is_soft_filler(word_stem(token))
        if not hard and not (soft and settings.remove_soft_fillers):
            continue
        if word.duration > settings.filler_max_duration:
            # un "euh" de 1,5 s est probablement un mot mal transcrit
            continue
        if hard:
            confidence = 0.95 if len(token) <= 4 else 0.88
            reason = f"tic sonore « {word.clean} »"
        else:
            # tic de langage : exige un contexte d'hesitation
            neighbour_filler = any(
                is_hard_filler(words[j].norm)
                for j in (position - 1, position + 1)
                if 0 <= j < len(words)
            )
            gap_before = (
                word.start - words[position - 1].end if position > 0 else utterance.gap_before
            )
            if not neighbour_filler and gap_before < 0.20:
                continue
            confidence = 0.64
            reason = f"tic de langage « {word.clean} »"
        findings.append(
            _finding("filler", [word], confidence, reason, utterance_index=utterance.index)
        )
        consumed.add(position)
    return findings


# --------------------------------------------------------------------------- #
# 3. repetitions immediates
# --------------------------------------------------------------------------- #
def _is_protected_repetition(tokens: list[str], settings: DisfluencySettings) -> bool:
    """La repetition est-elle volontaire ou grammaticale ?"""
    if len(tokens) != 1:
        return False
    token = tokens[0]
    if settings.protect_intensifiers and token in INTENSIFIERS:
        return True
    return token in GRAMMATICAL_DOUBLES


def _detect_stutters(
    utterance: Utterance,
    settings: DisfluencySettings,
    filler_indices: set[int],
) -> list[DisfluencyFinding]:
    """Detecte ``je je``, ``je pense je pense``, en ignorant les tics intercales."""
    findings: list[DisfluencyFinding] = []
    # on travaille sur la suite de mots utiles (hors tics deja supprimes)
    useful = [
        (position, word)
        for position, word in enumerate(utterance.words)
        if word.norm and word.index not in filler_indices
    ]
    if len(useful) < 2:
        return findings

    tokens = [w.norm for _, w in useful]
    consumed: set[int] = set()

    for group_size in range(MAX_REPEATED_GROUP, 0, -1):
        index = 0
        while index + 2 * group_size <= len(useful):
            if any(i in consumed for i in range(index, index + 2 * group_size)):
                index += 1
                continue
            first = tokens[index : index + group_size]
            second = tokens[index + group_size : index + 2 * group_size]
            if first != second:
                index += 1
                continue
            if _is_protected_repetition(first, settings):
                index += 1
                continue
            # le bafouillage est immediat : blanc court entre les deux groupes
            gap = useful[index + group_size][1].start - useful[index + group_size - 1][1].end
            if gap > settings.repetition_max_gap * MAX_STUTTER_GAP_FACTOR:
                index += 1
                continue

            # combien de fois le groupe est-il repete ?
            repeats = 2
            while index + (repeats + 1) * group_size <= len(useful) and tokens[
                index + repeats * group_size : index + (repeats + 1) * group_size
            ] == first:
                repeats += 1

            total = repeats * group_size
            if settings.keep_last_repetition:
                removed_slice = range(index, index + total - group_size)
                kept_slice = range(index + total - group_size, index + total)
            else:
                removed_slice = range(index + group_size, index + total)
                kept_slice = range(index, index + group_size)
            removed_words = [useful[i][1] for i in removed_slice]
            if not removed_words:
                index += 1
                continue
            if len(removed_words) > settings.max_consecutive_removed:
                removed_words = removed_words[-settings.max_consecutive_removed :]
            kept_text = " ".join(useful[i][1].clean for i in kept_slice)
            confidence = 0.92 if group_size == 1 else 0.86
            findings.append(
                _finding(
                    "stutter",
                    removed_words,
                    confidence,
                    f"repetition immediate de « {' '.join(first)} » "
                    f"({repeats} fois)",
                    kept_text=kept_text,
                    utterance_index=utterance.index,
                )
            )
            consumed.update(range(index, index + total))
            index += total
    return findings


# --------------------------------------------------------------------------- #
# 4. debuts de mots abandonnes
# --------------------------------------------------------------------------- #
def _detect_abandoned(
    utterance: Utterance,
    settings: DisfluencySettings,
    already: set[int],
) -> list[DisfluencyFinding]:
    findings: list[DisfluencyFinding] = []
    words = utterance.words
    for position in range(len(words) - 1):
        current = words[position]
        following = words[position + 1]
        if current.index in already or following.index in already:
            continue
        stem = word_stem(current.norm)
        target = following.norm
        if len(stem) < settings.abandoned_min_prefix:
            continue
        if not target.startswith(stem) or len(target) <= len(stem):
            continue
        if current.duration > settings.abandoned_max_duration:
            continue
        gap = max(0.0, following.start - current.end)
        truncated = current.is_truncated
        if truncated:
            if gap > 0.60:
                continue
            confidence = 0.93
            reason = f"mot abandonne « {current.clean} » avant « {following.clean} »"
        else:
            # sans marque de troncature, on exige des conditions tres strictes
            if gap > 0.25:
                continue
            if len(stem) > 5 or is_common_short_word(stem):
                continue
            if len(target) < len(stem) + 2:
                continue
            confidence = 0.66
            reason = (
                f"amorce « {current.clean} » reprise par « {following.clean} »"
            )
        findings.append(
            _finding(
                "abandoned",
                [current],
                confidence,
                reason,
                kept_text=following.clean,
                utterance_index=utterance.index,
            )
        )
    return findings


# --------------------------------------------------------------------------- #
def detect_disfluencies(
    transcript: Transcript,
    utterances: list[Utterance],
    settings: DisfluencySettings,
) -> list[DisfluencyFinding]:
    """Analyse chaque enonce et retourne les hesitations reperees.

    Les resultats ne sont pas encore appliques : ``decisions.py`` decide de la
    suppression effective en fonction de la confiance.
    """
    findings: list[DisfluencyFinding] = []
    del transcript  # l'analyse est purement locale a chaque enonce

    for utterance in utterances:
        local: list[DisfluencyFinding] = []

        if settings.remove_fillers:
            local.extend(_detect_fillers(utterance, settings))
        filler_indices = {i for f in local for i in f.word_indices}

        if settings.remove_stutters:
            local.extend(_detect_stutters(utterance, settings, filler_indices))
        taken = {i for f in local for i in f.word_indices}

        if settings.remove_abandoned_words:
            local.extend(_detect_abandoned(utterance, settings, taken))

        # securite : on ne vide jamais entierement un enonce porteur de contenu
        removed = {i for f in local for i in f.word_indices}
        remaining = [w for w in utterance.words if w.index not in removed and w.norm]
        if not remaining and utterance.content:
            local = [f for f in local if f.kind != "stutter"]

        findings.extend(local)

    findings.sort(key=lambda f: f.start)
    return findings
