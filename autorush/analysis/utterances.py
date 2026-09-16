"""Decoupage de la transcription en *enonces* (tentatives de phrase).

Un enonce est l'unite de decision pour la detection de reprises. Il est delimite
par :

* une ponctuation forte (``.``, ``!``, ``?``) ;
* des points de suspension (``...``) - typiques d'une phrase abandonnee ;
* un blanc suffisamment long entre deux mots ;
* un marqueur de correction isole, qui forme toujours son propre enonce.

Le decoupage ne supprime rien : il structure seulement la matiere.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.analysis.lexicon import (
    content_words,
    has_strong_marker,
    has_weak_marker,
    is_dangling,
    is_standalone_marker,
    strong_marker_hits,
    weak_marker_hits,
)
from autorush.analysis.similarity import token_match, token_similarity
from autorush.transcription.base import Transcript, Word
from autorush.utils import normalize_text

#: blanc au-dela duquel on considere que la phrase a change
DEFAULT_BREAK_GAP = 0.62
#: blanc au-dela duquel on coupe meme sans ponctuation ni changement de sens
HARD_BREAK_GAP = 1.15
#: nombre maximal de mots dans un enonce (securite sur les longs monologues)
MAX_WORDS = 48
#: mots minimum de chaque cote pour scinder un enonce sur un redemarrage.
#: En dessous, on est sur une repetition d'insistance ("tres tres fort"),
#: pas sur deux tentatives de la meme phrase. Trois suffisent : "Et meme si
#: au debut, et meme si..." doit se scinder, et sa reprise ne fait que trois
#: mots.
MIN_RESTART_TAKE = 3


@dataclass
class Utterance:
    """Une tentative de phrase."""

    index: int
    words: list[Word]
    #: blanc (secondes) avant le premier mot
    gap_before: float = 0.0
    #: blanc (secondes) apres le dernier mot
    gap_after: float = 0.0
    #: raison du decoupage ("ponctuation", "suspension", "blanc", "marqueur")
    break_reason: str = ""
    tags: set[str] = field(default_factory=set)

    # -- bornes --------------------------------------------------------- #
    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def first_index(self) -> int:
        return self.words[0].index if self.words else -1

    @property
    def last_index(self) -> int:
        return self.words[-1].index if self.words else -1

    # -- texte ---------------------------------------------------------- #
    @property
    def text(self) -> str:
        return " ".join(w.clean for w in self.words).strip()

    @property
    def norm(self) -> str:
        return normalize_text(self.text)

    @property
    def tokens(self) -> list[str]:
        return [w.norm for w in self.words if w.norm]

    @property
    def content(self) -> list[str]:
        return content_words(self.tokens)

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    @property
    def language(self) -> str:
        counts: dict[str, int] = {}
        for word in self.words:
            if word.language:
                counts[word.language] = counts.get(word.language, 0) + 1
        if not counts:
            return ""
        return max(counts.items(), key=lambda kv: kv[1])[0]

    # -- marques de fin ------------------------------------------------- #
    @property
    def ends_terminal(self) -> bool:
        return bool(self.words) and self.words[-1].ends_sentence

    @property
    def ends_suspension(self) -> bool:
        return bool(self.words) and self.words[-1].ends_suspension

    @property
    def ends_dangling(self) -> bool:
        """Se termine sur un mot de liaison : phrase presque surement coupee.

        La ponctuation forte tranche : ``ca passait pas trop.`` est une phrase
        finie, meme si ``trop`` peut par ailleurs annoncer une suite.
        """
        if not self.tokens:
            return False
        if self.ends_terminal:
            return False
        return is_dangling(self.tokens[-1])

    @property
    def ends_truncated(self) -> bool:
        return bool(self.words) and self.words[-1].is_truncated

    # -- marqueurs ------------------------------------------------------ #
    @property
    def has_strong_marker(self) -> bool:
        return has_strong_marker(self.norm)

    @property
    def has_weak_marker(self) -> bool:
        return has_weak_marker(self.norm)

    @property
    def is_marker_only(self) -> bool:
        """L'enonce ne sert qu'a annoncer une correction."""
        return is_standalone_marker(self.norm)

    def marker_hits(self) -> list[str]:
        return strong_marker_hits(self.norm) + weak_marker_hits(self.norm)

    # -- completude ----------------------------------------------------- #
    @property
    def is_complete(self) -> bool:
        """Heuristique : l'enonce ressemble-t-il a une phrase finie ?

        Une phrase finie se termine par une ponctuation forte, ne finit pas sur
        un mot de liaison, n'est pas tronquee et contient assez de matiere.
        """
        if not self.words:
            return False
        if self.ends_truncated or self.ends_dangling or self.ends_suspension:
            return False
        if len(self.content) < 2:
            return False
        return self.ends_terminal

    @property
    def is_abandoned(self) -> bool:
        """L'enonce porte les marques d'une phrase laissee en plan."""
        if not self.words:
            return True
        return bool(
            self.ends_truncated
            or self.ends_dangling
            or (self.ends_suspension and not self.ends_terminal)
        )

    def completeness(self) -> float:
        """Score de completude dans [0, 1] (utile pour choisir la meilleure prise)."""
        score = 0.0
        if self.ends_terminal:
            score += 0.42
        if not self.ends_dangling:
            score += 0.18
        if not self.ends_truncated:
            score += 0.10
        if not self.ends_suspension:
            score += 0.10
        content_len = len(self.content)
        score += min(0.20, 0.028 * content_len)
        if self.is_marker_only:
            score -= 0.45
        return max(0.0, min(1.0, score))

    def word_range(self) -> tuple[int, int]:
        return (self.first_index, self.last_index)

    def interval(self) -> tuple[float, float]:
        return (self.start, self.end)


def _should_break(previous: Word, current: Word, gap: float, break_gap: float) -> str:
    """Retourne la raison du decoupage entre deux mots, ou ``""``."""
    if previous.ends_sentence:
        return "ponctuation"
    if previous.ends_suspension:
        return "suspension"
    if gap >= HARD_BREAK_GAP:
        return "blanc_long"
    if gap >= break_gap:
        return "blanc"
    if previous.is_truncated and gap >= 0.12:
        return "mot_tronque"
    return ""


def build_utterances(
    transcript: Transcript,
    break_gap: float = DEFAULT_BREAK_GAP,
    isolate_markers: bool = True,
) -> list[Utterance]:
    """Decoupe la transcription en enonces."""
    if transcript.is_empty():
        return []

    groups: list[list[Word]] = []
    reasons: list[str] = []
    current: list[Word] = []
    current_reason = "debut"

    for position, word in enumerate(transcript.words):
        if not current:
            current = [word]
            continue
        previous = current[-1]
        gap = max(0.0, word.start - previous.end)
        reason = _should_break(previous, word, gap, break_gap)
        if not reason and len(current) >= MAX_WORDS:
            reason = "longueur"
        if reason:
            groups.append(current)
            reasons.append(current_reason)
            current_reason = reason
            current = [word]
        else:
            current.append(word)
        del position
    if current:
        groups.append(current)
        reasons.append(current_reason)

    if isolate_markers:
        groups, reasons = _split_marker_groups(groups, reasons)
    groups, reasons = _split_restart_groups(groups, reasons)

    utterances: list[Utterance] = []
    for i, words in enumerate(groups):
        utterance = Utterance(index=i, words=list(words), break_reason=reasons[i])
        utterances.append(utterance)

    # blancs entre enonces ; pour le premier, c'est le silence de tete du rush
    for i, utterance in enumerate(utterances):
        if i > 0:
            utterance.gap_before = max(0.0, utterance.start - utterances[i - 1].end)
        else:
            utterance.gap_before = max(0.0, utterance.start)
        if i + 1 < len(utterances):
            utterance.gap_after = max(0.0, utterances[i + 1].start - utterance.end)
    return utterances


def _split_marker_groups(
    groups: list[list[Word]], reasons: list[str]
) -> tuple[list[list[Word]], list[str]]:
    """Isole les marqueurs de correction en tete d'enonce.

    ``Non, je recommence. Les joueurs...`` est deja coupe par la ponctuation,
    mais ``non je recommence les joueurs japonais etaient`` (sans ponctuation)
    doit etre scinde pour que le marqueur ne contamine pas la bonne prise.
    """
    out_groups: list[list[Word]] = []
    out_reasons: list[str] = []
    for words, reason in zip(groups, reasons, strict=False):
        split_at = _marker_split_point(words)
        if split_at is None:
            out_groups.append(words)
            out_reasons.append(reason)
            continue
        out_groups.append(words[:split_at])
        out_reasons.append(reason)
        out_groups.append(words[split_at:])
        out_reasons.append("marqueur")
    return out_groups, out_reasons


def _marker_split_point(words: list[Word]) -> int | None:
    """Trouve la fin d'un marqueur place en tete de groupe."""
    if len(words) < 4:
        return None
    # on cherche le plus long prefixe (<= 6 mots) qui est un marqueur complet
    for length in range(min(6, len(words) - 2), 1, -1):
        prefix = normalize_text(" ".join(w.clean for w in words[:length]))
        if is_standalone_marker(prefix) and has_strong_marker(prefix):
            return length
    return None


def _restart_split_point(tokens: list[str]) -> int | None:
    """Trouve l'endroit ou un enonce recommence depuis son propre debut.

    Le moteur de transcription livre des segments de plusieurs secondes : une
    tentative ratee et sa reprise atterrissent regulierement dans le **meme**
    enonce, ou la detection de reprises ne peut pas les voir puisqu'elle
    compare les enonces entre eux.

    On cherche donc le mot a partir duquel la personne redit le debut de sa
    phrase, et on verifie la coupure avec la mesure de similarite : les deux
    moities doivent se ressembler comme deux tentatives, pas comme deux
    phrases voisines.
    """
    count = len(tokens)
    if count < 2 * MIN_RESTART_TAKE:
        return None
    opening = tokens[0]
    best_point: int | None = None
    best_score = 0.0
    second = tokens[1]
    for point in range(MIN_RESTART_TAKE, count - MIN_RESTART_TAKE + 1):
        # Une reprise redit l'attaque de la phrase. Le mot de liaison est
        # souvent le seul a changer ("Et pourtant..." repris en "Mais
        # pourtant...") : on accepte donc aussi un decalage d'un mot. Ce
        # filtre ne fait que proposer des candidats ; la similarite tranche.
        redit_attaque = token_match(tokens[point], opening) or (
            point + 1 < count and token_match(tokens[point + 1], second)
        )
        if not redit_attaque:
            continue
        similarity = token_similarity(tokens[:point], tokens[point:])
        if not (similarity.restart_prefix or similarity.restart_structure):
            continue
        if similarity.score > best_score:
            best_point, best_score = point, similarity.score
    return best_point


def _split_restart_groups(
    groups: list[list[Word]], reasons: list[str]
) -> tuple[list[list[Word]], list[str]]:
    """Scinde les enonces qui contiennent leur propre reprise."""
    out_groups: list[list[Word]] = []
    out_reasons: list[str] = []
    for words, reason in zip(groups, reasons, strict=False):
        # les indices doivent correspondre aux mots : on ne filtre rien, et on
        # renonce si un mot n'a pas de forme normalisee (ponctuation seule)
        tokens = [w.norm for w in words]
        point = _restart_split_point(tokens) if all(tokens) else None
        if point is None:
            out_groups.append(words)
            out_reasons.append(reason)
            continue
        out_groups.append(words[:point])
        out_reasons.append(reason)
        out_groups.append(words[point:])
        out_reasons.append("redemarrage")
    return out_groups, out_reasons


def utterance_at(utterances: list[Utterance], time: float) -> Utterance | None:
    for utterance in utterances:
        if utterance.start <= time <= utterance.end:
            return utterance
    return None
