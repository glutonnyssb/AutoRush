"""Fragments orphelins : petits morceaux de phrase laisses entre deux prises.

Exemples typiques ::

    "pas extremement forts."      (fin d'une tentative ratee)
    "redoutable."                 (mot isole entre deux prises)
    "Et donc..."                  (amorce abandonnee)

Un fragment n'est supprime que s'il est **court**, **incomplet** et **entoure
d'indices** : voisin immediat d'une zone deja supprimee, ou encadre par deux
zones supprimees. Si son contenu n'est pas couvert par les phrases voisines
conservees, la confiance chute et le fragment est garde puis signale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.analysis.lexicon import content_words
from autorush.analysis.utterances import Utterance
from autorush.config import FragmentSettings
from autorush.utils import clamp

BASE_CONFIDENCE = 0.45
BONUS_ABANDONED = 0.18
BONUS_ADJACENT_REMOVED = 0.12
BONUS_BETWEEN_REMOVED = 0.10
BONUS_VERY_SHORT = 0.10
BONUS_COVERED = 0.14
BONUS_MARKER_NEIGHBOUR = 0.08
PENALTY_UNIQUE_CONTENT = 0.22
PENALTY_COMPLETE = 0.18


@dataclass
class FragmentFinding:
    """Un fragment reperé."""

    utterance_index: int
    start: float
    end: float
    text: str
    confidence: float
    reason: str
    #: mots de contenu que le fragment est seul a porter
    unique_content: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        return {
            "utterance_index": self.utterance_index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "unique_content": list(self.unique_content),
        }


def _neighbour_kept(
    utterances: list[Utterance], position: int, removed: set[int], direction: int
) -> Utterance | None:
    """Premier enonce conserve avant (``direction=-1``) ou apres (``+1``)."""
    cursor = position + direction
    while 0 <= cursor < len(utterances):
        if utterances[cursor].index not in removed:
            return utterances[cursor]
        cursor += direction
    return None


def detect_fragments(
    utterances: list[Utterance],
    removed_utterances: set[int],
    settings: FragmentSettings,
) -> list[FragmentFinding]:
    """Cherche les fragments orphelins parmi les enonces encore conserves.

    ``removed_utterances`` contient les index des enonces deja supprimes par la
    detection de reprises : ils fournissent le contexte.
    """
    if not settings.enabled or not utterances:
        return []

    findings: list[FragmentFinding] = []

    for position, utterance in enumerate(utterances):
        if utterance.index in removed_utterances:
            continue
        if utterance.duration > settings.max_duration:
            continue
        if utterance.token_count > settings.max_tokens:
            continue
        if not utterance.tokens:
            continue

        previous_removed = position > 0 and utterances[position - 1].index in removed_utterances
        next_removed = (
            position + 1 < len(utterances)
            and utterances[position + 1].index in removed_utterances
        )
        marker_neighbour = (
            position > 0 and utterances[position - 1].is_marker_only
        ) or (
            position + 1 < len(utterances) and utterances[position + 1].is_marker_only
        )

        if settings.require_context and not (
            previous_removed or next_removed or marker_neighbour
        ):
            continue

        reasons: list[str] = []
        confidence = BASE_CONFIDENCE

        if utterance.is_abandoned:
            confidence += BONUS_ABANDONED
            reasons.append("fragment laisse en suspens")
        elif utterance.is_complete:
            confidence -= PENALTY_COMPLETE
            reasons.append("prudence : le fragment forme une phrase complete")

        if previous_removed and next_removed:
            confidence += BONUS_BETWEEN_REMOVED + BONUS_ADJACENT_REMOVED
            reasons.append("encadre par deux zones supprimees")
        elif previous_removed or next_removed:
            confidence += BONUS_ADJACENT_REMOVED
            reasons.append("colle a une mauvaise prise supprimee")

        if marker_neighbour and not (previous_removed or next_removed):
            confidence += BONUS_MARKER_NEIGHBOUR
            reasons.append("voisin d'un marqueur de correction")

        if utterance.token_count <= 3:
            confidence += BONUS_VERY_SHORT

        # le contenu du fragment est-il repris par une phrase voisine gardee ?
        fragment_content = set(content_words(utterance.tokens))
        neighbours: list[Utterance] = []
        for direction in (-1, 1):
            neighbour = _neighbour_kept(utterances, position, removed_utterances, direction)
            if neighbour is not None:
                neighbours.append(neighbour)
        neighbour_content: set[str] = set()
        for neighbour in neighbours:
            neighbour_content.update(content_words(neighbour.tokens))

        unique = sorted(fragment_content - neighbour_content)
        if not fragment_content:
            confidence += BONUS_COVERED
            reasons.append("aucun mot de contenu")
        elif not unique:
            confidence += BONUS_COVERED
            reasons.append("contenu deja present dans les phrases voisines")
        elif len(unique) >= 2:
            confidence -= PENALTY_UNIQUE_CONTENT
            reasons.append(
                "prudence : "
                + ", ".join(unique[:4])
                + " n'apparai(ssen)t pas dans les phrases voisines"
            )

        findings.append(
            FragmentFinding(
                utterance_index=utterance.index,
                start=utterance.start,
                end=utterance.end,
                text=utterance.text,
                confidence=clamp(confidence, 0.0, 1.0),
                reason="; ".join(reasons) or "fragment court",
                unique_content=unique,
            )
        )

    return findings
