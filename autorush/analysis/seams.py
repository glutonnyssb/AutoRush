"""Controle qualite des raccords.

Apres le montage, AutoRush relit la suite des mots conserves et cherche les
endroits ou le raccord risque de sonner faux :

* un marqueur de correction a survecu ("... Non. Et pourtant ...") ;
* une phrase repart sur un mot de liaison orphelin ("... et. Ensuite ...") ;
* un plan est tres court, ou deux coupes sont collees ;
* un mot de contenu est repete de part et d'autre d'une coupe ;
* une phrase a perdu sa fin (coupee sur un determinant, un auxiliaire...).

Rien n'est supprime ici : ce module ne produit que des avertissements, destines
au rapport et a la relecture humaine.
"""

from __future__ import annotations

from dataclasses import dataclass

from autorush.analysis.lexicon import (
    content_words,
    is_dangling,
    is_standalone_marker,
)
from autorush.analysis.utterances import Utterance
from autorush.config import SeamSettings
from autorush.editing.timeline import Timeline
from autorush.utils import format_timecode, normalize_text

#: mots qui ne peuvent pas commencer une phrase
CANNOT_START = frozenset(
    {
        "que", "qui", "dont", "donc", "car", "ni", "or", "ou", "et", "mais",
        "d'un", "d'une", "qu'", "l'", "y", "en",
        "that", "which", "whom", "nor", "and", "but",
    }
)

SEVERITY_LABELS = {
    "haute": "a verifier en priorite",
    "moyenne": "a jeter un oeil",
    "basse": "signalement mineur",
}


@dataclass
class SeamWarning:
    """Un raccord suspect."""

    #: position dans le montage final
    timeline_time: float
    #: position dans le rush d'origine
    source_time: float
    category: str
    severity: str
    reason: str
    before: str = ""
    after: str = ""
    shot_index: int = -1

    def as_dict(self) -> dict:
        return {
            "timeline_time": round(self.timeline_time, 3),
            "timecode": format_timecode(self.timeline_time),
            "source_time": round(self.source_time, 3),
            "source_timecode": format_timecode(self.source_time),
            "category": self.category,
            "severity": self.severity,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "shot_index": self.shot_index,
        }


def _tail(text: str, words: int = 6) -> str:
    parts = text.split()
    return " ".join(parts[-words:])


def _head(text: str, words: int = 6) -> str:
    parts = text.split()
    return " ".join(parts[:words])


def check_seams(
    timeline: Timeline,
    utterances: list[Utterance],
    removed_word_indices: set[int],
    settings: SeamSettings,
    benign_removed: set[int] | None = None,
) -> list[SeamWarning]:
    """Analyse tous les raccords du montage.

    ``benign_removed`` liste les mots retires pour une raison anodine
    (hesitation, bafouillage). Une coupe qui ne retire que ces mots ne casse
    pas la phrase : "Et euh du coup" -> "Et du coup" se lit parfaitement, il ne
    faut donc pas la signaler.
    """
    if not settings.enabled or not timeline.shots:
        return []
    benign = benign_removed or set()

    warnings: list[SeamWarning] = []

    # ------------------------------------------------------------------ #
    # 1. marqueurs de correction restes dans le montage
    # ------------------------------------------------------------------ #
    if settings.flag_surviving_markers:
        for utterance in utterances:
            if not utterance.words:
                continue
            if all(w.index in removed_word_indices for w in utterance.words):
                continue
            if not utterance.is_marker_only:
                continue
            timeline_time = timeline.source_to_timeline(utterance.start)
            if timeline_time is None:
                continue
            warnings.append(
                SeamWarning(
                    timeline_time=timeline_time,
                    source_time=utterance.start,
                    category="correction_conservee",
                    severity="haute",
                    reason=(
                        "une phrase de correction orale est restee dans le montage : "
                        f"« {utterance.text} »"
                    ),
                    before=utterance.text,
                )
            )

    # ------------------------------------------------------------------ #
    # 2. raccords entre plans
    # ------------------------------------------------------------------ #
    for position in range(1, len(timeline.shots)):
        previous = timeline.shots[position - 1]
        current = timeline.shots[position]
        junction = current.timeline_start

        previous_words = [w for w in previous.words if w.index not in removed_word_indices]
        current_words = [w for w in current.words if w.index not in removed_word_indices]
        if not previous_words or not current_words:
            continue

        last = previous_words[-1]
        first = current_words[0]
        removed_between = set(range(last.index + 1, first.index))
        cut_in_sentence = bool(removed_between)
        # coupe anodine : seuls des tics de parole ont disparu
        benign_cut = bool(removed_between) and removed_between.issubset(benign)
        if benign_cut:
            cut_in_sentence = False

        # 2a. phrase interrompue sur un mot de liaison
        if settings.flag_dangling_connectors and cut_in_sentence:
            if is_dangling(last.norm) and not last.ends_sentence:
                warnings.append(
                    SeamWarning(
                        timeline_time=junction,
                        source_time=current.source_start,
                        category="liaison_orpheline",
                        severity="moyenne",
                        reason=(
                            f"le plan precedent se termine sur « {last.clean} », "
                            "la phrase semble coupee en deux"
                        ),
                        before=_tail(previous.text),
                        after=_head(current.text),
                        shot_index=current.index,
                    )
                )
            elif first.norm in CANNOT_START and not last.ends_sentence:
                warnings.append(
                    SeamWarning(
                        timeline_time=junction,
                        source_time=current.source_start,
                        category="reprise_bancale",
                        severity="moyenne",
                        reason=(
                            f"le plan suivant commence sur « {first.clean} », "
                            "qui ne peut pas demarrer une phrase"
                        ),
                        before=_tail(previous.text),
                        after=_head(current.text),
                        shot_index=current.index,
                    )
                )

        # 2b. mot de contenu repete de part et d'autre de la coupe
        tail_content = content_words([w.norm for w in previous_words[-4:]])
        head_content = content_words([w.norm for w in current_words[:4]])
        duplicated = [w for w in tail_content if w in head_content]
        if duplicated and cut_in_sentence:
            warnings.append(
                SeamWarning(
                    timeline_time=junction,
                    source_time=current.source_start,
                    category="repetition_raccord",
                    severity="basse",
                    reason=(
                        "« " + ", ".join(sorted(set(duplicated)))
                        + " » se repete juste avant et juste apres la coupe"
                    ),
                    before=_tail(previous.text),
                    after=_head(current.text),
                    shot_index=current.index,
                )
            )

        # 2c. deux coupes trop rapprochees
        if previous.duration < settings.min_cut_spacing_warn and position >= 2:
            warnings.append(
                SeamWarning(
                    timeline_time=previous.timeline_start,
                    source_time=previous.source_start,
                    category="coupes_rapprochees",
                    severity="basse",
                    reason=(
                        f"deux coupes separees de {previous.duration:.2f} s : "
                        "le montage peut sembler hache"
                    ),
                    before=_tail(previous.text, 4),
                    after=_head(current.text, 4),
                    shot_index=previous.index,
                )
            )

    # ------------------------------------------------------------------ #
    # 3. plans tres courts
    # ------------------------------------------------------------------ #
    for shot in timeline.shots:
        if shot.duration < settings.min_shot_warn:
            warnings.append(
                SeamWarning(
                    timeline_time=shot.timeline_start,
                    source_time=shot.source_start,
                    category="plan_court",
                    severity="basse",
                    reason=f"plan de {shot.duration:.2f} s seulement",
                    before=shot.text[:70],
                    shot_index=shot.index,
                )
            )

    # ------------------------------------------------------------------ #
    # 4. enonce conserve qui n'a plus de sens (tout son contenu a disparu)
    # ------------------------------------------------------------------ #
    for utterance in utterances:
        kept = [w for w in utterance.words if w.index not in removed_word_indices]
        if not kept or len(kept) == len(utterance.words):
            continue
        kept_text = normalize_text(" ".join(w.clean for w in kept))
        if not content_words(kept_text.split()) and not is_standalone_marker(kept_text):
            timeline_time = timeline.source_to_timeline(kept[0].start)
            if timeline_time is None:
                continue
            warnings.append(
                SeamWarning
                (
                    timeline_time=timeline_time,
                    source_time=kept[0].start,
                    category="reste_vide",
                    severity="moyenne",
                    reason=(
                        "il ne reste que des mots fonctionnels de cet enonce : "
                        f"« {' '.join(w.clean for w in kept)} »"
                    ),
                    before=utterance.text,
                )
            )

    warnings.sort(key=lambda w: (w.timeline_time, w.category))
    return warnings
