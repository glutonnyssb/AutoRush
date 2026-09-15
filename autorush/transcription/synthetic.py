"""Construction de transcriptions synthetiques.

Sert a deux choses :

* les tests automatises (aucun besoin de ffmpeg ni de modele Whisper) ;
* la commande ``autorush demo`` qui fabrique un rush de demonstration.

Le generateur imite la sortie d'un moteur ASR : un mot par entree, horodate,
avec un blanc explicite entre les phrases.
"""

from __future__ import annotations

from dataclasses import dataclass

from autorush.transcription.base import Segment, Transcript, Word
from autorush.transcription.io import link_transcript

#: duree attribuee a un mot, en secondes par caractere (+ base)
CHAR_DURATION = 0.052
BASE_DURATION = 0.115
#: micro-blanc entre deux mots d'une meme phrase
INTRA_WORD_GAP = 0.035


@dataclass
class ScriptLine:
    """Une ligne de script : le texte, le blanc qui la precede, la langue."""

    text: str
    gap_before: float = 0.6
    language: str = "fr"


def _word_duration(token: str) -> float:
    return BASE_DURATION + CHAR_DURATION * max(1, len(token))


def build_transcript(
    lines: list[ScriptLine] | list[tuple[str, float]] | list[str],
    start_at: float = 0.5,
    language: str = "fr",
    tail_silence: float = 0.8,
) -> Transcript:
    """Construit une ``Transcript`` a partir d'un script.

    ``lines`` accepte trois formes : ``ScriptLine``, ``(texte, blanc_avant)`` ou
    simplement ``texte`` (blanc par defaut).
    """
    normalized: list[ScriptLine] = []
    for item in lines:
        if isinstance(item, ScriptLine):
            normalized.append(item)
        elif isinstance(item, tuple):
            text = item[0]
            gap = float(item[1]) if len(item) > 1 else 0.6
            lang = str(item[2]) if len(item) > 2 else language
            normalized.append(ScriptLine(text=text, gap_before=gap, language=lang))
        else:
            normalized.append(ScriptLine(text=str(item), language=language))

    transcript = Transcript(language=language, model="synthetic")
    cursor = float(start_at)

    for line in normalized:
        tokens = line.text.split()
        if not tokens:
            cursor += line.gap_before
            continue
        cursor += line.gap_before
        segment_start = cursor
        words: list[Word] = []
        for position, token in enumerate(tokens):
            duration = _word_duration(token)
            word = Word(
                text=token,
                start=round(cursor, 4),
                end=round(cursor + duration, 4),
                probability=0.95,
                language=line.language,
            )
            words.append(word)
            cursor += duration
            if position < len(tokens) - 1:
                cursor += INTRA_WORD_GAP
        transcript.segments.append(
            Segment(
                start=round(segment_start, 4),
                end=round(cursor, 4),
                text=line.text,
                words=words,
                language=line.language,
                avg_logprob=-0.25,
            )
        )

    transcript.duration = round(cursor + tail_silence, 4)
    link_transcript(transcript)
    transcript.languages = sorted({line.language for line in normalized if line.language})
    return transcript


# --------------------------------------------------------------------------- #
# Rush de demonstration (reprend les exemples du cahier des charges)
# --------------------------------------------------------------------------- #
DEMO_SCRIPT: list[ScriptLine] = [
    ScriptLine("Salut a tous et bienvenue dans cette nouvelle video.", gap_before=0.6),
    ScriptLine("Euh aujourd'hui on va parler du tournoi.", gap_before=0.45),
    ScriptLine("Je je pense que c'etait la meilleure edition depuis longtemps.", gap_before=0.5),
    ScriptLine("Et euh du coup on va voir pourquoi.", gap_before=0.4),
    ScriptLine("Mal- Malgre ca, il y a eu quelques problemes d'organisation.", gap_before=0.55),
    ScriptLine("C'etait tres tres fort niveau niveau de jeu.", gap_before=0.5),
    ScriptLine("Les joueurs japonais etaient plutot...", gap_before=0.9),
    ScriptLine("Enfin...", gap_before=0.7),
    ScriptLine("Ils etaient plutot bons...", gap_before=0.8),
    ScriptLine("Non, je recommence.", gap_before=1.4),
    ScriptLine(
        "Les joueurs japonais etaient vraiment excellents cette annee.", gap_before=1.1
    ),
    ScriptLine("On a eu Acola, Miya, Asimo...", gap_before=0.7),
    ScriptLine("Tous ces joueurs etaient...", gap_before=0.8),
    ScriptLine("Raru...", gap_before=0.9),
    ScriptLine("Non, je recommence.", gap_before=1.5),
    ScriptLine(
        "On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru.", gap_before=1.2
    ),
    ScriptLine("Ces joueurs etaient pas extremement forts.", gap_before=0.8),
    ScriptLine("Attends.", gap_before=1.3),
    ScriptLine("Ces joueurs etaient absolument redoutables en poule.", gap_before=1.1),
    ScriptLine("Le niveau global a explose cette saison.", gap_before=0.7),
    ScriptLine(
        "Et c'est exactement pour ca que la finale a ete aussi intense.", gap_before=0.5
    ),
    ScriptLine("Bon, on va passer a la suite du classement.", gap_before=2.6),
    ScriptLine("The bracket was actually insane this year.", gap_before=0.6, language="en"),
    ScriptLine("Franchement, personne n'avait predit ce resultat.", gap_before=0.5),
    ScriptLine("Merci d'avoir regarde, on se retrouve tres vite.", gap_before=0.9),
]


def build_demo_transcript() -> Transcript:
    """Transcription de demonstration couvrant tous les cas du cahier des charges."""
    transcript = build_transcript(DEMO_SCRIPT, start_at=0.8, language="fr")
    transcript.languages = ["fr", "en"]
    transcript.model = "synthetic-demo"
    transcript.meta["source"] = "demo"
    return transcript
