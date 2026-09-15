"""Lexiques francais / anglais utilises par l'analyse.

Toutes les entrees sont ecrites sous forme **normalisee** : minuscules, sans
accents, apostrophe droite (voir ``autorush.utils.normalize_word``).

Le lexique est volontairement explicite plutot que statistique : sur un rush
facecam, la liste des tics de parole est courte et stable, et une regle lisible
est plus facile a corriger qu'un modele opaque.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# 1. Tics de parole purs (supprimables seuls)
# --------------------------------------------------------------------------- #
#: Hesitations sonores : jamais porteuses de sens.
FILLERS_HARD: frozenset[str] = frozenset(
    {
        # francais - uniquement des sons d'hesitation sans ambiguite.
        # "eu" est volontairement absent : c'est le participe passe d'avoir
        # ("il y a eu"), le supprimer casserait la phrase.
        "euh", "euhh", "euuh", "euuuh", "heu", "heuh", "hum", "humm",
        "hmm", "hmmm", "hm", "mmh", "mh", "mmm", "pff", "pfff", "pfiou",
        # anglais
        "uh", "uhh", "uhhh", "uhm", "um", "umm", "ummm", "er", "err",
        "erm", "mm", "mhm",
    }
)

#: Tics de langage : supprimables uniquement en contexte d'hesitation
#: (juste avant/apres un blanc, ou entoures d'autres tics).
FILLERS_SOFT: frozenset[str] = frozenset(
    {
        # francais - tics de langage et interjections. Ils colorent le propos
        # ("Ah !", "hein ?") : on ne les retire qu'en mode tres dynamique.
        "bon", "voila", "quoi", "genre", "disons", "beh", "bah", "baah",
        "ben", "mouais", "tss", "ah", "ahh", "eh", "hein", "oh", "ohh",
        # anglais
        "like", "well", "right", "okay", "ok", "basically", "huh",
        "anyway", "yeah",
    }
)

#: Expressions multi-mots d'hesitation (sequences normalisees).
FILLER_PHRASES: tuple[tuple[str, ...], ...] = (
    ("enfin", "je", "veux", "dire"),
    ("enfin", "bref"),
    ("comment", "dire"),
    ("comment", "dire", "ca"),
    ("je", "sais", "pas"),
    ("tu", "vois", "ce", "que", "je", "veux", "dire"),
    ("i", "mean"),
    ("you", "know"),
    ("how", "do", "you", "say"),
    ("what", "was", "it"),
)

# --------------------------------------------------------------------------- #
# 2. Repetitions volontaires a proteger
# --------------------------------------------------------------------------- #
#: "tres tres fort", "very very good" : la repetition est un intensifieur.
INTENSIFIERS: frozenset[str] = frozenset(
    {
        # francais
        "tres", "trop", "super", "hyper", "bien", "vraiment", "grave",
        "beaucoup", "tellement", "plein", "loin", "long", "longtemps",
        "petit", "doucement", "vite", "encore", "jamais", "toujours",
        "non", "oui", "si", "plus", "moins",
        # anglais
        "very", "so", "much", "really", "way", "far", "big",
        "small", "slow", "fast", "again", "never", "always", "no", "yes",
    }
)

#: Mots dont la repetition immediate est grammaticale en francais.
GRAMMATICAL_DOUBLES: frozenset[str] = frozenset(
    {
        "que", "qui", "si", "ne", "de", "a", "en", "y", "l'on", "on",
        "nous", "vous", "ils", "elles", "lui", "leur", "meme", "tout",
        "tous", "the", "that", "to", "of", "in", "it", "had", "that's",
    }
)

#: Mots courts frequents qui sont aussi le prefixe d'un mot plus long.
#: Sans marque de troncature explicite ("Mal-"), on refuse de les considerer
#: comme un debut de mot abandonne : "car cardiaque" n'est pas un bafouillage.
COMMON_SHORT_WORDS: frozenset[str] = frozenset(
    {
        "car", "par", "pour", "sur", "sous", "mal", "bon", "bien", "son",
        "mon", "ton", "ces", "des", "les", "est", "sont", "tout", "tous",
        "peu", "plus", "moins", "fin", "part", "port", "mer", "air",
        "point", "corps", "cours", "court", "fort", "long", "mot", "main",
        "pas", "gens", "coup", "temps", "rien", "tres", "pres", "vrai",
        "the", "for", "one", "out", "art", "man", "can", "ran",
        "run", "in", "on", "an", "at", "as", "to", "be", "so", "no",
        "not", "and", "are", "war", "win", "old", "new", "own", "end",
    }
)

# --------------------------------------------------------------------------- #
# 3. Marqueurs de correction orale
# --------------------------------------------------------------------------- #
#: Marqueurs FORTS : la personne annonce explicitement qu'elle refait sa prise.
#: Chaque entree est une expression reguliere appliquee au texte normalise.
STRONG_MARKER_PATTERNS: tuple[str, ...] = (
    # francais
    r"\bje\s+(?:re)?recommence\b",
    r"\bon\s+recommence\b",
    r"\bje\s+refais\b",
    r"\bje\s+la\s+refais\b",
    r"\bje\s+le\s+refais\b",
    r"\bje\s+reprends\b",
    r"\bje\s+reformule\b",
    r"\bje\s+me\s+reprends\b",
    r"\bje\s+me\s+suis\s+trompee?\b",
    r"\bje\s+me\s+trompe\b",
    r"\bc'?est\s+pas\s+(?:ca|bon|clair)\b",
    r"\bce\s+n'?est\s+pas\s+ca\b",
    r"\bnon\s+pas\s+ca\b",
    r"\bnon\s+c'?est\s+pas\s+ca\b",
    r"\bje\s+vais\s+pas\s+dire\s+ca\b",
    r"\bje\s+ne\s+vais\s+pas\s+dire\s+ca\b",
    r"\bje\s+voulais\s+dire\b",
    r"\bje\s+recommence\s+la\s+phrase\b",
    r"\bon\s+la\s+refait\b",
    r"\battends?\s+je\s+(?:recommence|refais|reprends|reformule)\b",
    r"\bpardon\s+je\s+(?:recommence|refais|reprends)\b",
    r"\bmerde\s+je\s+(?:recommence|refais)\b",
    r"\bcoupe\s+(?:ca|la)\b",
    r"\bon\s+coupe\b",
    r"\bje\s+coupe\b",
    r"\bje\s+recadre\b",
    r"\bzero\s+je\s+refais\b",
    # anglais
    r"\bl(?:et)?\s*me\s+(?:start|try)\s+(?:over|again)\b",
    r"\blet'?s\s+(?:start|try)\s+(?:over|again)\b",
    r"\bstart(?:ing)?\s+over\b",
    r"\btake\s+(?:two|2|three|3|it)\s+again\b",
    r"\bi'?ll\s+(?:redo|say)\s+that\s+again\b",
    r"\bi\s+mean(?:t)?\s+to\s+say\b",
    r"\bscratch\s+that\b",
    r"\bcut\s+that\b",
    r"\bthat'?s\s+not\s+(?:it|right|what\s+i\s+meant)\b",
    r"\bi\s+messed\s+(?:that\s+)?up\b",
    r"\blet\s+me\s+rephrase\b",
    r"\bredo\b",
)

#: Marqueurs FAIBLES : indices d'hesitation ou de correction, jamais suffisants
#: a eux seuls pour supprimer une phrase complete.
WEAK_MARKER_PATTERNS: tuple[str, ...] = (
    r"\bnon\b",
    r"\battends?\b",
    r"\bpardon\b",
    r"\benfin\b",
    r"\bplutot\b",
    r"\bou\s+plutot\b",
    r"\bdisons\b",
    r"\bbref\b",
    r"\bzut\b",
    r"\bmince\b",
    r"\bmerde\b",
    r"\bputain\b",
    r"\bnan\b",
    r"\bno\b",
    r"\bwait\b",
    r"\bsorry\b",
    r"\bactually\b",
    r"\brather\b",
    r"\bi\s+mean\b",
    r"\bnope\b",
    r"\bhold\s+on\b",
    r"\banyway\b",
)

#: Enonces qui, seuls, constituent un marqueur fort (phrase entiere).
STANDALONE_MARKERS: frozenset[str] = frozenset(
    {
        "non", "nan", "non non", "non non non", "attends", "attend",
        "attendez", "pardon", "zut", "mince", "merde", "putain",
        "c'est pas ca", "pas ca", "non pas ca", "bref", "enfin",
        "no", "nope", "wait", "sorry", "hold on", "scratch that",
        "cut", "cut that", "again", "one more time", "encore une fois",
        "on refait", "je recommence", "je refais",
    }
)

STRONG_MARKER_RE = re.compile("|".join(STRONG_MARKER_PATTERNS))
WEAK_MARKER_RE = re.compile("|".join(WEAK_MARKER_PATTERNS))

# --------------------------------------------------------------------------- #
# 4. Mots de liaison / grammaire
# --------------------------------------------------------------------------- #
#: Mots qui ne peuvent pas terminer une phrase : si un enonce finit par l'un
#: d'eux, il est presque certainement abandonne.
DANGLING_CONNECTORS: frozenset[str] = frozenset(
    {
        # francais
        "et", "ou", "mais", "donc", "car", "que", "qui", "quoi", "dont",
        "parce", "puisque", "comme", "si", "quand", "lorsque",
        "pour", "par", "dans", "sur", "sous", "avec", "sans", "chez",
        "vers", "de", "du", "des", "la", "le", "les", "un", "une", "au",
        "aux", "a", "en", "ce", "cet", "cette", "ces", "mon", "ma", "mes",
        "son", "sa", "ses", "notre", "nos", "leur", "leurs", "plus",
        "tres", "trop", "assez", "plutot", "vraiment", "tellement",
        "etait", "etaient", "est", "sont", "ete", "avait", "avaient",
        "j'ai", "c'est", "il", "elle", "ils", "elles", "on", "nous",
        "vous", "je", "tu", "d'un", "d'une", "l'", "qu'",
        # anglais
        "and", "or", "but", "so", "because", "that", "which", "who",
        "the", "an", "of", "to", "in", "at", "for", "with",
        "without", "was", "were", "is", "are", "been", "had", "have",
        "i", "he", "she", "they", "we", "you", "it", "my", "his", "her",
        "their", "our", "very", "really", "quite", "rather",
    }
)

#: Mots fonctionnels ignores dans le calcul de similarite (mots de contenu).
STOPWORDS: frozenset[str] = frozenset(
    {
        # francais
        "le", "la", "les", "un", "une", "des", "du", "de", "d'", "l'",
        "et", "ou", "mais", "donc", "or", "ni", "car", "que", "qui",
        "quoi", "dont", "ce", "cet", "cette", "ces", "se", "sa", "son",
        "ses", "mon", "ma", "mes", "ton", "ta", "tes", "notre", "nos",
        "votre", "vos", "leur", "leurs", "je", "j'", "tu", "il", "elle",
        "on", "nous", "vous", "ils", "elles", "me", "te", "lui", "y",
        "en", "au", "aux", "a", "dans", "sur", "sous", "pour", "par",
        "avec", "sans", "chez", "vers", "entre", "est", "sont", "etait",
        "etaient", "ete", "etre", "ai", "as", "avons", "avez", "ont",
        "eu", "eue", "eus", "aura", "auront", "serai", "sera", "seront",
        "avait", "avaient", "avoir", "fait", "faire", "plus", "moins",
        "pas", "ne", "n'", "si", "comme", "tout", "tous", "toute",
        "toutes", "meme", "aussi", "alors", "bien", "tres", "deja",
        "encore", "quand", "comment", "pourquoi", "oui", "non", "c'est",
        "il y a", "cela", "ca", "la-bas", "ici", "y a",
        # anglais
        "the", "an", "and", "but", "so", "of", "to", "in",
        "at", "for", "with", "without", "by", "from", "is",
        "are", "was", "were", "be", "been", "being", "have", "has",
        "had", "do", "does", "did", "i", "you", "he", "she", "it", "we",
        "they", "him", "her", "them", "my", "your", "his", "our",
        "their", "this", "that", "these", "those", "not", "no", "yes",
        "very", "just", "also", "then", "than", "there", "here", "what",
        "when", "why", "how", "all", "some", "any", "more", "most",
    }
)

#: Mots qui signalent une idee neuve (bons candidats pour un zoom).
IDEA_SHIFT_WORDS: frozenset[str] = frozenset(
    {
        # francais
        "mais", "donc", "alors", "ensuite", "apres", "finalement",
        "bref", "cependant", "pourtant", "neanmoins", "resultat",
        "conclusion", "premierement", "deuxiemement", "troisiemement",
        "d'abord", "enfin", "surtout", "attention", "franchement",
        "honnetement", "clairement", "evidemment", "concretement",
        "voila", "maintenant", "aujourd'hui", "imaginez", "regardez",
        "ecoutez", "sauf", "sinon", "parce", "puisque", "grace",
        # anglais
        "but", "so", "then", "next", "finally", "however", "although",
        "anyway", "result", "first", "second", "third",
        "especially", "honestly", "clearly", "obviously", "actually",
        "now", "today", "imagine", "look", "listen", "except",
        "otherwise", "because", "since", "meanwhile", "suddenly",
    }
)

#: Mots d'insistance : une phrase qui les contient merite souvent un zoom.
EMPHASIS_WORDS: frozenset[str] = frozenset(
    {
        # francais
        "incroyable", "enorme", "fou", "folle", "dingue", "excellent",
        "excellents", "excellente", "parfait", "meilleur", "meilleure",
        "pire", "jamais", "toujours", "enfin", "enormement", "vraiment",
        "carrement", "grave", "redoutable", "impressionnant", "record",
        "historique", "important", "essentiel", "cle", "crucial",
        "attention", "surtout", "extremement", "absolument",
        "totalement", "completement", "premiere", "unique", "seul",
        # anglais
        "incredible", "insane", "crazy", "huge", "massive", "amazing",
        "perfect", "best", "worst", "never", "always", "really",
        "absolutely", "totally", "completely", "extremely", "key",
        "essential", "only", "first",
        "historic", "unbelievable", "wild",
    }
)


# --------------------------------------------------------------------------- #
# Aides
# --------------------------------------------------------------------------- #
def is_hard_filler(token: str) -> bool:
    return token in FILLERS_HARD


def is_soft_filler(token: str) -> bool:
    return token in FILLERS_SOFT


def is_filler(token: str) -> bool:
    return token in FILLERS_HARD or token in FILLERS_SOFT


def is_content_word(token: str) -> bool:
    """Un mot de contenu porte du sens : ni vide, ni fonctionnel, ni tic."""
    if not token or len(token) < 2:
        return False
    if token in STOPWORDS or token in FILLERS_HARD:
        return False
    return True


def content_words(tokens: list[str]) -> list[str]:
    return [t for t in tokens if is_content_word(t)]


def has_strong_marker(text_normalized: str) -> bool:
    return bool(STRONG_MARKER_RE.search(text_normalized))


def has_weak_marker(text_normalized: str) -> bool:
    return bool(WEAK_MARKER_RE.search(text_normalized))


def strong_marker_hits(text_normalized: str) -> list[str]:
    return [m.group(0) for m in STRONG_MARKER_RE.finditer(text_normalized)]


def weak_marker_hits(text_normalized: str) -> list[str]:
    return [m.group(0) for m in WEAK_MARKER_RE.finditer(text_normalized)]


def is_standalone_marker(text_normalized: str) -> bool:
    """L'enonce entier est-il un marqueur (``Non, je recommence.``) ?"""
    cleaned = " ".join(text_normalized.split())
    if not cleaned:
        return False
    if cleaned in STANDALONE_MARKERS:
        return True
    # "non je recommence" : un marqueur fort + uniquement des mots vides autour
    if has_strong_marker(cleaned):
        residue = STRONG_MARKER_RE.sub(" ", cleaned).split()
        leftovers = [t for t in residue if is_content_word(t)]
        return len(leftovers) == 0
    # "non non" / "non, attends" : uniquement des marqueurs faibles
    tokens = cleaned.split()
    if len(tokens) <= 4:
        non_marker = [
            t
            for t in tokens
            if not WEAK_MARKER_RE.fullmatch(t) and t not in FILLERS_HARD and t not in STOPWORDS
        ]
        if not non_marker and any(WEAK_MARKER_RE.fullmatch(t) for t in tokens):
            return True
    return False


def is_dangling(token: str) -> bool:
    return token in DANGLING_CONNECTORS


def word_stem(token: str) -> str:
    """Retire la marque de troncature d'un mot : ``mal-`` -> ``mal``."""
    return (token or "").rstrip("-_'")


def is_common_short_word(token: str) -> bool:
    return token in COMMON_SHORT_WORDS or token in STOPWORDS
