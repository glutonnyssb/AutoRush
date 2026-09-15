"""Timeline de montage : conversion des intervalles conserves en plans.

Un *plan* (``Shot``) est un morceau continu du rush d'origine qui se retrouve
tel quel dans le montage. La timeline est la suite des plans, bout a bout.

C'est aussi ici qu'on applique les garde-fous de rythme :

* aucun plan plus court que ``min_shot_duration`` (sinon le montage hache) ;
* les plans muets en fin de rush sont ecartes ;
* chaque plan connait les mots qu'il contient, ce qui permet ensuite de placer
  les zooms sur les phrases importantes et d'auditer les raccords.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.config import SilenceSettings
from autorush.media.audio import AudioProfile
from autorush.transcription.base import Transcript, Word
from autorush.utils import format_timecode, merge_intervals, subtract_intervals


@dataclass
class Shot:
    """Un plan du montage."""

    index: int
    #: bornes dans le rush d'origine
    source_start: float
    source_end: float
    #: bornes dans le montage final
    timeline_start: float = 0.0
    words: list[Word] = field(default_factory=list)
    #: raison de la coupe qui precede ce plan
    cut_reason: str = ""
    #: energie moyenne du plan (0-1), -1 si inconnue
    energy: float = -1.0
    #: force de l'attaque au debut du plan (0-1)
    onset: float = -1.0
    tags: set[str] = field(default_factory=set)

    @property
    def duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def timeline_end(self) -> float:
        return self.timeline_start + self.duration

    @property
    def text(self) -> str:
        return " ".join(w.clean for w in self.words).strip()

    @property
    def has_speech(self) -> bool:
        return bool(self.words)

    @property
    def starts_sentence(self) -> bool:
        """Le plan commence-t-il un nouvel enonce ?"""
        return "debut_phrase" in self.tags

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "source_start": round(self.source_start, 4),
            "source_end": round(self.source_end, 4),
            "timeline_start": round(self.timeline_start, 4),
            "timeline_end": round(self.timeline_end, 4),
            "duration": round(self.duration, 4),
            "timecode": format_timecode(self.timeline_start),
            "energy": round(self.energy, 3) if self.energy >= 0 else None,
            "onset": round(self.onset, 3) if self.onset >= 0 else None,
            "cut_reason": self.cut_reason,
            "word_count": len(self.words),
            "text": self.text,
            "tags": sorted(self.tags),
        }


@dataclass
class Timeline:
    """Le montage complet."""

    shots: list[Shot] = field(default_factory=list)
    source_duration: float = 0.0

    @property
    def duration(self) -> float:
        return sum(shot.duration for shot in self.shots)

    @property
    def cut_count(self) -> int:
        return max(0, len(self.shots) - 1)

    def keep_intervals(self) -> list[tuple[float, float]]:
        return [(shot.source_start, shot.source_end) for shot in self.shots]

    def removed_duration(self) -> float:
        return max(0.0, self.source_duration - self.duration)

    def source_to_timeline(self, time: float) -> float | None:
        """Convertit un instant du rush en instant du montage (``None`` si coupe)."""
        for shot in self.shots:
            if shot.source_start <= time <= shot.source_end:
                return shot.timeline_start + (time - shot.source_start)
        return None

    def timeline_to_source(self, time: float) -> float | None:
        for shot in self.shots:
            if shot.timeline_start <= time <= shot.timeline_end:
                return shot.source_start + (time - shot.timeline_start)
        return None

    def shot_at_timeline(self, time: float) -> Shot | None:
        for shot in self.shots:
            if shot.timeline_start <= time < shot.timeline_end:
                return shot
        return self.shots[-1] if self.shots else None

    def as_dict(self) -> dict:
        return {
            "source_duration": round(self.source_duration, 3),
            "duration": round(self.duration, 3),
            "shots": [shot.as_dict() for shot in self.shots],
            "cut_count": self.cut_count,
        }


# --------------------------------------------------------------------------- #
def _enforce_min_duration(
    intervals: list[tuple[float, float]], minimum: float, source_duration: float
) -> list[tuple[float, float]]:
    """Supprime les micro-plans en les fusionnant ou en les ecartant.

    Un plan trop court est d'abord etendu (on recupere du silence autour), puis,
    si c'est impossible, fusionne avec le plan voisin le plus proche.
    """
    if minimum <= 0 or not intervals:
        return intervals

    result = [list(pair) for pair in intervals]
    changed = True
    guard = 0
    while changed and guard < 60:
        changed = False
        guard += 1
        for position, (start, end) in enumerate(result):
            if end - start >= minimum:
                continue
            missing = minimum - (end - start)
            # limites imposees par les voisins
            lower = result[position - 1][1] if position > 0 else 0.0
            upper = (
                result[position + 1][0] if position + 1 < len(result) else source_duration
            )
            room_before = max(0.0, start - lower)
            room_after = max(0.0, upper - end)
            if room_before + room_after >= missing:
                take_before = min(room_before, missing * 0.5)
                take_after = min(room_after, missing - take_before)
                take_before = min(room_before, missing - take_after)
                result[position][0] = start - take_before
                result[position][1] = end + take_after
                changed = True
                break
            # pas assez de place : on fusionne avec le voisin le plus proche
            if position > 0 and (
                position + 1 >= len(result)
                or (start - result[position - 1][1]) <= (result[position + 1][0] - end)
            ):
                result[position - 1][1] = end
                result.pop(position)
            elif position + 1 < len(result):
                result[position + 1][0] = start
                result.pop(position)
            else:
                result.pop(position)
            changed = True
            break

    return [(a, b) for a, b in result if b - a > 0.0]


def build_timeline(
    transcript: Transcript,
    removals: list[tuple[float, float]],
    settings: SilenceSettings,
    source_duration: float = 0.0,
    profile: AudioProfile | None = None,
    cut_reasons: dict[float, str] | None = None,
) -> Timeline:
    """Construit la timeline a partir des intervalles retires."""
    total = max(source_duration, transcript.duration)
    if total <= 0:
        return Timeline(shots=[], source_duration=0.0)

    keep = subtract_intervals([(0.0, total)], merge_intervals(removals))
    keep = [pair for pair in keep if pair[1] - pair[0] > 0.0]
    keep = _enforce_min_duration(keep, settings.min_shot_duration, total)
    keep = merge_intervals(keep, tolerance=0.001)

    timeline = Timeline(source_duration=total)
    cursor = 0.0
    word_cursor = 0
    words = transcript.words

    for index, (start, end) in enumerate(keep):
        shot = Shot(
            index=index,
            source_start=start,
            source_end=end,
            timeline_start=cursor,
        )
        # mots dont le centre tombe dans le plan (les mots sont tries)
        while word_cursor < len(words) and _center(words[word_cursor]) < start:
            word_cursor += 1
        scan = word_cursor
        while scan < len(words) and _center(words[scan]) <= end:
            shot.words.append(words[scan])
            scan += 1

        if profile is not None:
            shot.energy = profile.energy(start, end)
            shot.onset = profile.onset_strength(start)

        if cut_reasons:
            shot.cut_reason = cut_reasons.get(round(start, 3), "")

        timeline.shots.append(shot)
        cursor += shot.duration

    _tag_shots(timeline, transcript)
    return timeline


def _center(word: Word) -> float:
    return 0.5 * (word.start + word.end)


def _tag_shots(timeline: Timeline, transcript: Transcript) -> None:
    """Pose des etiquettes utiles au placement des zooms."""
    del transcript
    for position, shot in enumerate(timeline.shots):
        if not shot.words:
            shot.tags.add("muet")
            continue
        first = shot.words[0]
        if position == 0:
            shot.tags.add("debut_phrase")
        else:
            previous = timeline.shots[position - 1]
            if not previous.words:
                shot.tags.add("debut_phrase")
            else:
                last_previous = previous.words[-1]
                if last_previous.ends_sentence or last_previous.ends_suspension:
                    shot.tags.add("debut_phrase")
                elif first.index != last_previous.index + 1:
                    # des mots ont disparu entre les deux plans
                    shot.tags.add("apres_coupe")
        if shot.words[-1].ends_sentence:
            shot.tags.add("fin_phrase")
        if shot.duration >= 6.0:
            shot.tags.add("plan_long")
