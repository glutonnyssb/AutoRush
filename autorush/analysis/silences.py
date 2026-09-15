"""Traitement des blancs entre les mots.

Un *blanc* est l'intervalle entre la fin d'un mot conserve et le debut du mot
conserve suivant. Il peut contenir :

* du vrai silence ;
* une respiration ;
* des mots supprimes par l'analyse (hesitation, mauvaise prise).

Chaque blanc est classe puis reduit a une duree cible. C'est ici que se joue la
difference entre un montage "naturel" et un montage "tres dynamique" : les
durees cibles viennent du preset de style.

Le resultat est une liste d'intervalles a retirer du rush. Comme les mots
supprimes se trouvent toujours *dans* un blanc au sens ci-dessus, cette seule
liste suffit a decrire tout le montage.
"""

from __future__ import annotations

from dataclasses import dataclass

from autorush.config import SilenceSettings
from autorush.media.audio import AudioProfile
from autorush.transcription.base import Transcript, Word

#: repartition du blanc conserve : un peu plus apres le mot qu'avant le suivant
KEEP_SPLIT_AFTER = 0.55

#: duree en dessous de laquelle un blanc sans coupe n'est pas consigne
RECORD_THRESHOLD = 0.12

#: libelles utilises dans le rapport
KIND_LABELS: dict[str, str] = {
    "respiration": "respiration",
    "pause_naturelle": "pause naturelle",
    "pause_phrase": "pause de fin de phrase",
    "pause_virgule": "pause de virgule",
    "hesitation": "hesitation",
    "pause_longue": "pause trop longue",
    "mauvaise_prise": "silence de mauvaise prise",
    "bord": "bord du rush",
}


@dataclass
class GapDecision:
    """Decision prise sur un blanc."""

    index: int
    #: bornes du blanc dans le rush d'origine
    start: float
    end: float
    kind: str
    #: duree finalement conservee
    kept: float
    #: intervalle retire (``None`` si le blanc est garde tel quel)
    removal: tuple[float, float] | None
    reason: str
    #: le blanc contient-il de la parole supprimee ?
    contains_removed_speech: bool = False
    energy: float = -1.0
    before_word: int = -1
    after_word: int = -1

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def removed(self) -> float:
        if self.removal is None:
            return 0.0
        return max(0.0, self.removal[1] - self.removal[0])

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "kind": self.kind,
            "label": KIND_LABELS.get(self.kind, self.kind),
            "kept": round(self.kept, 3),
            "removed": round(self.removed, 3),
            "removal": (
                [round(self.removal[0], 3), round(self.removal[1], 3)]
                if self.removal
                else None
            ),
            "reason": self.reason,
            "contains_removed_speech": self.contains_removed_speech,
            "energy": round(self.energy, 3) if self.energy >= 0 else None,
        }


def _target_for_gap(
    previous: Word | None,
    duration: float,
    settings: SilenceSettings,
    profile: AudioProfile | None,
    start: float,
    end: float,
) -> tuple[float, str, str, float]:
    """Retourne ``(duree_cible, type, explication, energie)``."""
    energy = -1.0
    if profile is not None:
        energy = profile.energy(start, end)

    # 1. respiration audible : on la garde (quitte a la raccourcir un peu)
    if (
        profile is not None
        and duration <= settings.breath_max_duration
        and profile.is_breath(
            start, end, settings.breath_energy_ratio, settings.breath_max_energy
        )
    ):
        target = max(settings.target_gap, min(duration, settings.breath_keep))
        return target, "respiration", "respiration audible conservee", energy

    # 2. blanc court : on n'y touche pas, sinon le montage devient robotique
    if duration <= settings.keep_below:
        return duration, "respiration", "blanc court, conserve tel quel", energy

    # 3. pause longue : on coupe franchement mais on laisse respirer
    if duration >= settings.long_pause_threshold:
        return (
            settings.long_pause_target,
            "pause_longue",
            "pause trop longue, resserree",
            energy,
        )

    # 4. ponctuation : la pause porte du sens
    if previous is not None and previous.ends_sentence:
        return (
            settings.sentence_pause_target,
            "pause_phrase",
            "pause de fin de phrase, conservee en partie",
            energy,
        )
    if previous is not None and previous.ends_clause:
        return (
            settings.comma_pause_target,
            "pause_virgule",
            "pause de virgule, resserree",
            energy,
        )

    # 5. blanc moyen sans ponctuation : hesitation
    return settings.target_gap, "hesitation", "blanc d'hesitation, resserre", energy


def plan_silences(
    transcript: Transcript,
    removed_word_indices: set[int],
    settings: SilenceSettings,
    profile: AudioProfile | None = None,
    media_duration: float = 0.0,
) -> list[GapDecision]:
    """Calcule la liste des intervalles a retirer.

    ``removed_word_indices`` contient les mots deja condamnes par l'analyse de
    la parole (hesitations, reprises, fragments).
    """
    words = transcript.words
    if not words:
        return []

    kept_words = [w for w in words if w.index not in removed_word_indices]
    if not kept_words:
        return []

    total_duration = max(media_duration, transcript.duration, words[-1].end)
    decisions: list[GapDecision] = []

    # ------------------------------------------------------------------ #
    # bord de tete
    # ------------------------------------------------------------------ #
    first = kept_words[0]
    head_end = first.start
    if head_end > settings.edge_silence + settings.min_removal:
        free_before = first.start - (
            words[first.index - 1].end if first.index > 0 else 0.0
        )
        keep_in = min(settings.edge_silence, max(0.0, free_before))
        cut_end = max(0.0, first.start - keep_in)
        if cut_end > settings.min_removal:
            decisions.append(
                GapDecision(
                    index=len(decisions),
                    start=0.0,
                    end=first.start,
                    kind="bord",
                    kept=keep_in,
                    removal=(0.0, cut_end),
                    reason="silence de debut de rush",
                    contains_removed_speech=first.index > 0,
                    after_word=first.index,
                )
            )

    # ------------------------------------------------------------------ #
    # blancs internes
    # ------------------------------------------------------------------ #
    for position in range(len(kept_words) - 1):
        left = kept_words[position]
        right = kept_words[position + 1]
        start = left.end
        end = right.start
        duration = end - start
        contains_removed = right.index != left.index + 1

        if duration <= 0.0:
            continue

        # marge disponible de chaque cote avant de toucher a de la parole
        next_any = words[left.index + 1] if left.index + 1 < len(words) else right
        previous_any = words[right.index - 1] if right.index > 0 else left
        free_after = max(0.0, min(next_any.start, end) - start)
        free_before = max(0.0, end - max(previous_any.end, start))

        if contains_removed:
            # le blanc englobe une mauvaise prise : on ne garde que les marges
            keep_out = min(settings.pad_out, free_after)
            keep_in = min(settings.pad_in, free_before)
            kept = keep_out + keep_in
            kind = "mauvaise_prise"
            reason = "blanc issu d'une suppression de parole"
            energy = profile.energy(start, end) if profile is not None else -1.0
        else:
            target, kind, reason, energy = _target_for_gap(
                left, duration, settings, profile, start, end
            )
            floor = min(duration, settings.pad_in + settings.pad_out)
            kept = min(duration, max(target, floor))
            keep_out = min(free_after, kept * KEEP_SPLIT_AFTER)
            keep_in = min(free_before, kept - keep_out)
            # si un cote n'offre pas la place, on reporte sur l'autre
            keep_out = min(free_after, kept - keep_in)

        cut_start = start + keep_out
        cut_end = end - keep_in
        removal: tuple[float, float] | None = None
        if cut_end - cut_start > 0.0:
            enough = (cut_end - cut_start) >= settings.min_removal
            if enough or contains_removed:
                removal = (cut_start, cut_end)

        if removal is None:
            kept = duration

        decisions.append(
            GapDecision(
                index=len(decisions),
                start=start,
                end=end,
                kind=kind,
                kept=kept,
                removal=removal,
                reason=reason,
                contains_removed_speech=contains_removed,
                energy=energy,
                before_word=left.index,
                after_word=right.index,
            )
        )

    # ------------------------------------------------------------------ #
    # bord de queue
    # ------------------------------------------------------------------ #
    last = kept_words[-1]
    if total_duration - last.end > settings.edge_silence + settings.min_removal:
        free_after = (
            words[last.index + 1].start - last.end
            if last.index + 1 < len(words)
            else total_duration - last.end
        )
        keep_out = min(settings.edge_silence, max(0.0, free_after))
        cut_start = last.end + keep_out
        if total_duration - cut_start > settings.min_removal:
            decisions.append(
                GapDecision(
                    index=len(decisions),
                    start=last.end,
                    end=total_duration,
                    kind="bord",
                    kept=keep_out,
                    removal=(cut_start, total_duration),
                    reason="silence de fin de rush",
                    contains_removed_speech=last.index + 1 < len(words),
                    before_word=last.index,
                )
            )

    # On ne conserve dans le rapport que les blancs reellement significatifs :
    # les micro-blancs entre deux mots d'une meme phrase n'interessent personne.
    return [
        decision
        for decision in decisions
        if decision.removal is not None
        or decision.duration >= RECORD_THRESHOLD
        or decision.contains_removed_speech
    ]


def summarize(decisions: list[GapDecision]) -> dict[str, dict[str, float]]:
    """Statistiques par type de blanc (pour le rapport)."""
    summary: dict[str, dict[str, float]] = {}
    for decision in decisions:
        bucket = summary.setdefault(
            decision.kind, {"count": 0, "removed": 0.0, "duration": 0.0}
        )
        bucket["count"] += 1
        bucket["removed"] += decision.removed
        bucket["duration"] += decision.duration
    for bucket in summary.values():
        bucket["removed"] = round(bucket["removed"], 3)
        bucket["duration"] = round(bucket["duration"], 3)
    return summary
