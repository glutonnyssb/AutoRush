"""Mesure de similarite entre deux tentatives de phrase.

Aucun modele externe n'est utilise. Trois mesures suffisent, a condition de
comparer les mots **a la bonne echelle** : un mot rare pese plus qu'un mot
passe-partout, et une terminaison flechie ne doit pas casser l'appariement.

=================  =========================================================
Mesure             Ce qu'elle detecte
=================  =========================================================
``align``          quelle part de la tentative se retrouve dans la reprise,
                   dans l'ordre (sous-suite commune ponderee)
``containment``    quelle part de l'*information* de la tentative survit
``structure``      la personne a-t-elle redemarre pareil, ou abouti a la
                   meme chute ? (``max(opening, tail)``)
=================  =========================================================

Pourquoi ces trois-la
---------------------
Une reprise de phrase n'est pas une paraphrase : la personne redit la meme
chose. Le vocabulaire commun est donc massif, mais il arrive presque jamais
au meme endroit :

* ``Au debut du Ultimate, c'etait plutot les Etats-Unis...``
* ``Au debut, c'etait vraiment les Etats-Unis...``

Ces deux enonces ne partagent que deux mots en prefixe litteral, mais ils
partagent toute leur charpente. ``structure`` la voit, un prefixe exact non.

Symetriquement, deux phrases **differentes** d'un meme rush partagent le
vocabulaire du sujet (``joueurs``, ``niveau``, ``tournoi``, ``un petit peu``).
Ponderer les mots passe-partout plus faiblement que les mots informatifs est
ce qui separe une vraie reprise d'un simple voisinage thematique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from autorush.analysis.lexicon import (
    content_words,
    is_common_short_word,
    is_content_word,
)

#: ponderation des trois mesures (somme = 1)
W_ALIGN = 0.25
W_CONTAINMENT = 0.45
W_STRUCTURE = 0.30

#: fenetres comparees pour la charpente (en tokens)
OPENING_WINDOW = 8
TAIL_WINDOW = 6

#: poids d'un token selon son pouvoir discriminant
WEIGHT_CONTENT = 1.0
WEIGHT_GENERIC = 0.35
WEIGHT_FUNCTION = 0.15

#: longueur minimale du prefixe commun pour parler de "redemarrage litteral"
MIN_RESTART_PREFIX = 3
#: part minimale du plus court enonce couverte par le prefixe commun
MIN_RESTART_RATIO = 0.42

#: charpente commune au-dela de laquelle on parle de "meme phrase redemarree",
#: meme sans prefixe litteralement identique
MIN_RESTART_STRUCTURE = 0.60
#: mots de contenu communs exiges en plus de la charpente
MIN_RESTART_SHARED = 2

#: degre d'appariement de deux tokens
MATCH_EXACT = 1.0
MATCH_STEM = 0.92
MATCH_PREFIX = 0.80
#: longueur minimale d'un radical apres troncature du suffixe
MIN_STEM_LENGTH = 4
#: appariement par racine commune : longueur et part minimales
MIN_CHAR_PREFIX = 4
MIN_CHAR_PREFIX_RATIO = 0.70

#: suffixes flexionnels francais, testes du plus long au plus court
INFLECTIONS: tuple[str, ...] = (
    "eraient", "erions", "assent", "aient", "erait", "erons", "eront",
    "ement", "ations", "ation", "aires", "ables", "ances", "ences",
    "ions", "iez", "ais", "ait", "ant", "ent", "ons", "ez",
    "er", "ir", "es", "s", "x", "e",
)


@dataclass
class SimilarityResult:
    """Detail de la comparaison entre une tentative ``a`` et une reprise ``b``."""

    score: float = 0.0
    #: part de ``a`` retrouvee dans ``b``, dans l'ordre
    align: float = 0.0
    #: part de l'information de ``a`` presente dans ``b``
    containment: float = 0.0
    #: charpente commune : ``max(opening, tail)``
    structure: float = 0.0
    #: debut d'enonce commun
    opening: float = 0.0
    #: fin d'enonce commune
    tail: float = 0.0
    #: vocabulaire commun, insensible a l'ordre (diagnostic uniquement)
    jaccard: float = 0.0
    #: mesure de sequence brute (diagnostic uniquement)
    sequence: float = 0.0
    #: prefixe litteralement commun, en part du plus court enonce
    prefix: float = 0.0
    #: longueur (en tokens) du prefixe litteralement commun
    prefix_tokens: int = 0
    #: ``a`` et ``b`` commencent-ils par les memes mots ?
    restart_prefix: bool = False
    #: ``a`` et ``b`` sont-ils visiblement la meme phrase redemarree ?
    restart_structure: bool = False
    shared_content: list[str] = field(default_factory=list)
    lost_content: list[str] = field(default_factory=list)
    gained_content: list[str] = field(default_factory=list)
    #: mots de contenu de ``a`` perdus qui se trouvent dans le prefixe commun
    lost_inside_prefix: list[str] = field(default_factory=list)
    #: part de l'information de ``a`` qui disparait (0 = rien ne se perd)
    loss_ratio: float = 0.0

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "align": round(self.align, 4),
            "containment": round(self.containment, 4),
            "structure": round(self.structure, 4),
            "opening": round(self.opening, 4),
            "tail": round(self.tail, 4),
            "jaccard": round(self.jaccard, 4),
            "sequence": round(self.sequence, 4),
            "prefix": round(self.prefix, 4),
            "prefix_tokens": self.prefix_tokens,
            "restart_prefix": self.restart_prefix,
            "restart_structure": self.restart_structure,
            "loss_ratio": round(self.loss_ratio, 4),
            "shared_content": list(self.shared_content),
            "lost_content": list(self.lost_content),
        }


# --------------------------------------------------------------------------- #
# Appariement de deux mots
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8192)
def inflection_stem(token: str) -> str:
    """Retire une terminaison flechie : ``roulaient`` -> ``roul``.

    Le radical doit rester assez long pour ne pas rapprocher n'importe quoi :
    ``cas`` reste ``cas`` (sinon il rejoindrait ``ca``).
    """
    for suffix in INFLECTIONS:
        if token.endswith(suffix) and len(token) - len(suffix) >= MIN_STEM_LENGTH:
            return token[: -len(suffix)]
    return token


def _char_prefix(a: str, b: str) -> int:
    count = 0
    for char_a, char_b in zip(a, b, strict=False):
        if char_a != char_b:
            break
        count += 1
    return count


@lru_cache(maxsize=65536)
def token_match(a: str, b: str) -> float:
    """Degre d'appariement de deux tokens normalises, dans [0, 1].

    ``persos`` / ``personnages`` et ``jouer`` / ``jouaient`` doivent
    s'apparier : la transcription ne redit jamais deux fois exactement
    les memes formes.
    """
    if a == b:
        return MATCH_EXACT
    if inflection_stem(a) == inflection_stem(b):
        return MATCH_STEM
    shared = _char_prefix(a, b)
    shortest = min(len(a), len(b))
    if (
        shared >= MIN_CHAR_PREFIX
        and shortest
        and shared >= MIN_CHAR_PREFIX_RATIO * shortest
    ):
        return MATCH_PREFIX
    return 0.0


@lru_cache(maxsize=8192)
def token_weight(token: str) -> float:
    """Pouvoir discriminant d'un token.

    Partager ``Etats-Unis`` prouve une reprise ; partager ``un petit peu``
    ne prouve rien : les deux phrases parlent simplement du meme sujet.
    """
    if not is_content_word(token):
        return WEIGHT_FUNCTION
    if is_common_short_word(token):
        return WEIGHT_GENERIC
    return WEIGHT_CONTENT


def _total_weight(tokens: list[str]) -> float:
    return sum(token_weight(t) for t in tokens)


def _matches_any(word: str, candidates: list[str]) -> bool:
    return any(token_match(word, other) > 0.0 for other in candidates)


# --------------------------------------------------------------------------- #
# Mesures
# --------------------------------------------------------------------------- #
def _weighted_lcs(a: list[str], b: list[str]) -> float:
    """Poids de ``a`` apparie par la plus longue sous-suite commune.

    L'appariement est flou (``token_match``) et chaque mot compte pour son
    poids : l'ordre est respecte, les insertions sont gratuites.
    """
    if not a or not b:
        return 0.0
    columns = len(b)
    previous = [0.0] * (columns + 1)
    for token_a in a:
        weight = token_weight(token_a)
        current = [0.0] * (columns + 1)
        for j, token_b in enumerate(b):
            match = token_match(token_a, token_b)
            diagonal = previous[j] + match * weight if match else 0.0
            current[j + 1] = max(previous[j + 1], current[j], diagonal)
        previous = current
    return previous[columns]


def _ratio(matched: float, reference: float) -> float:
    return matched / reference if reference > 0 else 0.0


def _window_similarity(a: list[str], b: list[str]) -> float:
    """Part commune de deux extremites d'enonce."""
    if not a or not b:
        return 0.0
    reference = min(_total_weight(a), _total_weight(b))
    return _ratio(_weighted_lcs(a, b), reference)


def common_prefix_length(a: list[str], b: list[str]) -> int:
    """Nombre de tokens identiques au debut des deux listes."""
    count = 0
    for token_a, token_b in zip(a, b, strict=False):
        if token_a != token_b:
            break
        count += 1
    return count


def _fuzzy_prefix_length(a: list[str], b: list[str], max_skips: int = 1) -> int:
    """Prefixe commun tolerant a ``max_skips`` mots parasites.

    ``Les joueurs euh japonais`` et ``Les joueurs japonais`` partagent un
    prefixe de 3 mots utiles : sans tolerance, on n'en verrait que 2.
    """
    best = common_prefix_length(a, b)
    if max_skips <= 0:
        return best
    for skip in range(min(len(a), 6)):
        best = max(best, common_prefix_length(a[:skip] + a[skip + 1 :], b))
    for skip in range(min(len(b), 6)):
        best = max(best, common_prefix_length(a, b[:skip] + b[skip + 1 :]))
    return best


def token_similarity(a_tokens: list[str], b_tokens: list[str]) -> SimilarityResult:
    """Compare deux suites de tokens normalises."""
    result = SimilarityResult()
    if not a_tokens or not b_tokens:
        return result

    # 1. alignement : quelle part de la tentative se retrouve dans la reprise
    result.align = _ratio(_weighted_lcs(a_tokens, b_tokens), _total_weight(a_tokens))

    # 2. containment : quelle part de l'information survit
    a_content = list(dict.fromkeys(content_words(a_tokens)))
    b_content = content_words(b_tokens)
    shared = [w for w in a_content if _matches_any(w, b_content)]
    lost = [w for w in a_content if w not in shared]
    result.shared_content = sorted(shared)
    result.lost_content = lost
    result.gained_content = [
        w for w in dict.fromkeys(b_content) if not _matches_any(w, a_content)
    ]
    # Un enonce sans aucun mot de contenu ("et euh le") ne contient rien a
    # retrouver : containment reste nul plutot que de se rabattre sur l'ordre
    # des mots vides. Ces fragments relevent du bafouillage, pas de la reprise.
    content_weight = _total_weight(a_content)
    result.containment = _ratio(_total_weight(shared), content_weight)
    result.loss_ratio = _ratio(_total_weight(lost), content_weight)

    # 3. charpente : meme redemarrage, ou meme chute
    result.opening = _window_similarity(
        a_tokens[:OPENING_WINDOW], b_tokens[:OPENING_WINDOW]
    )
    result.tail = _window_similarity(a_tokens[-TAIL_WINDOW:], b_tokens[-TAIL_WINDOW:])
    result.structure = max(result.opening, result.tail)

    # -- prefixe litteral (preuve la plus forte d'un redemarrage) ---------- #
    prefix_len = _fuzzy_prefix_length(a_tokens, b_tokens)
    result.prefix_tokens = prefix_len
    shorter = min(len(a_tokens), len(b_tokens))
    result.prefix = prefix_len / shorter if shorter else 0.0
    result.restart_prefix = (
        prefix_len >= MIN_RESTART_PREFIX and result.prefix >= MIN_RESTART_RATIO
    )
    result.restart_structure = (
        result.structure >= MIN_RESTART_STRUCTURE
        and len(shared) >= MIN_RESTART_SHARED
    )

    prefix_tokens_set = set(a_tokens[:prefix_len])
    result.lost_inside_prefix = [w for w in lost if w in prefix_tokens_set]

    # -- diagnostics ------------------------------------------------------- #
    result.sequence = SequenceMatcher(a=a_tokens, b=b_tokens, autojunk=False).ratio()
    set_a, set_b = set(a_content), set(b_content)
    union = set_a | set_b
    result.jaccard = len(set_a & set_b) / len(union) if union else 0.0

    result.score = (
        W_ALIGN * result.align
        + W_CONTAINMENT * result.containment
        + W_STRUCTURE * result.structure
    )
    return result


def text_similarity(a_text: str, b_text: str) -> SimilarityResult:
    """Version texte de ``token_similarity`` (normalisation incluse)."""
    from autorush.utils import tokenize

    return token_similarity(tokenize(a_text), tokenize(b_text))
