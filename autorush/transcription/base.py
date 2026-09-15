"""Modele de donnees de la transcription.

La granularite de reference d'AutoRush est le **mot horodate** : toutes les
decisions de montage (silences, hesitations, reprises) se ramenent a des
intervalles de mots. Les ``Segment`` issus du moteur ne servent qu'a porter la
ponctuation et la langue detectee.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.utils import (
    ends_with_suspension,
    ends_with_terminal_punct,
    normalize_word,
)


@dataclass
class Word:
    """Un mot horodate."""

    text: str
    start: float
    end: float
    probability: float = 1.0
    #: langue du segment d'origine (``fr``, ``en``, ...)
    language: str = ""
    #: position dans ``Transcript.words`` (rempli par ``Transcript.reindex``)
    index: int = -1
    #: index du segment d'origine
    segment_index: int = -1

    # -- proprietes derivees ------------------------------------------- #
    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def norm(self) -> str:
        """Forme normalisee (minuscule, sans accent, sans ponctuation)."""
        return normalize_word(self.text)

    @property
    def clean(self) -> str:
        """Texte affichable, sans espaces parasites."""
        return (self.text or "").strip()

    @property
    def ends_sentence(self) -> bool:
        return ends_with_terminal_punct(self.text)

    @property
    def ends_clause(self) -> bool:
        stripped = self.clean.rstrip("\"'»)]")
        return bool(stripped) and stripped[-1] in ",;:"

    @property
    def ends_suspension(self) -> bool:
        return ends_with_suspension(self.text)

    @property
    def is_truncated(self) -> bool:
        """Mot coupe net : ``Mal-`` ou ``Mal_``."""
        stripped = self.clean.rstrip("\"'»)]….")
        return bool(stripped) and stripped[-1] in "-_" and len(stripped) > 1

    def shifted(self, offset: float) -> Word:
        return Word(
            text=self.text,
            start=self.start + offset,
            end=self.end + offset,
            probability=self.probability,
            language=self.language,
            index=self.index,
            segment_index=self.segment_index,
        )


@dataclass
class Segment:
    """Segment renvoye par le moteur de transcription (phrase approximative)."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    language: str = ""
    #: probabilite moyenne / score de confiance du moteur
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    index: int = -1

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Transcript:
    """Transcription complete d'un rush."""

    words: list[Word] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    #: langue dominante detectee
    language: str = ""
    #: langues rencontrees, par ordre de duree decroissante
    languages: list[str] = field(default_factory=list)
    #: duree du media (secondes)
    duration: float = 0.0
    #: nom du modele utilise
    model: str = ""
    #: metadonnees libres (device, temps de calcul...)
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def reindex(self) -> Transcript:
        """Trie les mots, corrige les horodatages incoherents, renumerote."""
        self.words = [w for w in self.words if w.text and w.text.strip()]
        self.words.sort(key=lambda w: (w.start, w.end))

        previous_end = 0.0
        for position, word in enumerate(self.words):
            if word.end < word.start:
                word.end = word.start
            # un mot sans duree est illisible pour le moteur de coupe :
            # on lui donne une duree plancher proportionnelle a sa longueur.
            if word.duration < 0.012:
                word.end = word.start + max(0.05, 0.035 * max(1, len(word.norm)))
            # pas de recouvrement : whisper produit parfois des mots qui se
            # chevauchent de quelques millisecondes.
            if word.start < previous_end:
                word.start = previous_end
                if word.end < word.start:
                    word.end = word.start + 0.02
            previous_end = word.end
            word.index = position

        for position, segment in enumerate(self.segments):
            segment.index = position

        if self.words:
            self.duration = max(self.duration, self.words[-1].end)
        return self

    # ------------------------------------------------------------------ #
    @property
    def text(self) -> str:
        return " ".join(w.clean for w in self.words).strip()

    @property
    def speech_duration(self) -> float:
        """Duree cumulee des mots (hors blancs)."""
        return sum(w.duration for w in self.words)

    def word_count(self) -> int:
        return len(self.words)

    def words_between(self, start: float, end: float) -> list[Word]:
        """Mots dont le centre tombe dans [start, end]."""
        result = []
        for word in self.words:
            center = 0.5 * (word.start + word.end)
            if start <= center <= end:
                result.append(word)
        return result

    def slice_text(self, start_index: int, end_index: int) -> str:
        """Texte des mots ``[start_index, end_index]`` inclus."""
        chunk = self.words[max(0, start_index) : end_index + 1]
        return " ".join(w.clean for w in chunk).strip()

    def is_empty(self) -> bool:
        return not self.words
