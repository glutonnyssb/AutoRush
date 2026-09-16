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

#: Ancres de promotion du score. Une ancre est une preuve suffisante a elle
#: seule que la personne a redemarre la meme phrase ; le score est alors
#: releve au palier correspondant.
ANCHOR_STRONG = 0.60
ALIGN_STRONG = 0.72
ANCHOR_MEDIUM = 0.35
#: mesure d'appui exigee en plus d'une ancre moyenne
SUPPORT_MEDIUM = 0.50
#: paliers atteints par promotion
PROMOTED_STRONG = 0.90
PROMOTED_MEDIUM = 0.72
#: une amorce interrompue est promue des ce niveau : sa brievete l'empeche
#: mecaniquement de ressembler a la version complete
INTERRUPTED_CORE = 0.40

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

#: seuil de ressemblance de deux mots compares caractere par caractere
FUZZY_RATIO = 0.86
#: longueur minimale pour tenter la comparaison floue (sinon trop permissif)
FUZZY_MIN_LENGTH = 5

#: Tics retires AVANT comparaison. Ils n'appartiennent pas a la phrase : la
#: reprise en ajoute ou en retire librement, et les compter fait chuter la
#: ressemblance de deux tentatives par ailleurs identiques.
COMPARISON_FILLERS: frozenset[str] = frozenset(
    {
        "euh", "euhh", "heu", "heuh", "hum", "humm", "hmm", "hm", "mmh",
        "bah", "ben", "beh", "bon", "voila", "quoi", "genre", "disons",
        "uh", "uhm", "um", "er", "erm", "like", "well", "basically",
    }
)

#: Expressions entieres retirees avant comparaison (sequences normalisees).
FILLER_SEQUENCES: tuple[tuple[str, ...], ...] = (
    ("du", "coup"),
    ("en", "fait"),
    ("je", "pense", "que"),
    ("je", "veux", "dire"),
    ("si", "tu", "veux"),
    ("tu", "vois"),
    ("on", "va", "dire"),
    ("i", "mean"),
    ("you", "know"),
)

#: Equivalences orales : deux facons de dire la meme chose. La personne qui
#: se reprend reformule, et ces couples reviennent sans cesse a l'oral.
ORAL_EQUIVALENTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("pas", "mal", "de"), ("quelques",)),
    (("pas", "mal"), ("quelques",)),
    (("un", "certain", "nombre", "de"), ("quelques",)),
    (("plus", "precisement"), ("plutot",)),
    (("plus", "exactement"), ("plutot",)),
    (("c", "est", "a", "dire"), ("plutot",)),
    (("lorsque",), ("quand",)),
    (("lorsqu",), ("quand",)),
    (("des", "que"), ("quand",)),
)

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
    #: contenu commun, insensible a l'ordre des mots
    bag: float = 0.0
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
            "bag": round(self.bag, 4),
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
def _drop_sequences(
    tokens: list[str], sequences: tuple[tuple[str, ...], ...]
) -> list[str]:
    """Retire chaque occurrence des sequences donnees."""
    out: list[str] = []
    position = 0
    while position < len(tokens):
        for sequence in sequences:
            end = position + len(sequence)
            if tuple(tokens[position:end]) == sequence:
                position = end
                break
        else:
            out.append(tokens[position])
            position += 1
    return out


def _apply_equivalents(tokens: list[str]) -> list[str]:
    """Remplace chaque expression par sa forme de reference."""
    out: list[str] = []
    position = 0
    while position < len(tokens):
        for source, target in ORAL_EQUIVALENTS:
            end = position + len(source)
            if tuple(tokens[position:end]) == source:
                out.extend(target)
                position = end
                break
        else:
            out.append(tokens[position])
            position += 1
    return out


def normalize_oral(tokens: list[str]) -> list[str]:
    """Met deux tentatives sur la meme echelle avant de les comparer.

    Une reprise orale n'est pas une copie : la personne change ses tics, ses
    connecteurs et ses tournures. On retire donc ce qui n'appartient pas a la
    phrase (``euh``, ``en fait``, ``du coup``) et on ramene les tournures
    equivalentes a une forme unique (``pas mal de`` et ``quelques`` disent la
    meme chose). Ce qui reste est la phrase elle-meme.
    """
    cleaned = _drop_sequences(tokens, FILLER_SEQUENCES)
    cleaned = [t for t in cleaned if t and t not in COMPARISON_FILLERS]
    cleaned = _apply_equivalents(cleaned)
    return cleaned or [t for t in tokens if t]


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
    # comparaison floue : deux transcriptions du meme mot different souvent
    # ailleurs qu'a la fin ("competition" / "competetion")
    if shortest >= FUZZY_MIN_LENGTH:
        ratio = SequenceMatcher(a=a, b=b, autojunk=False).ratio()
        if ratio >= FUZZY_RATIO:
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


def _bag_similarity(a_content: list[str], b_content: list[str]) -> float:
    """Appariement glouton du contenu, sans tenir compte de l'ordre.

    Une reformulation deplace les mots ("les joueurs japonais etaient forts"
    -> "ils etaient forts, les joueurs japonais"). Les mesures qui respectent
    l'ordre s'y cassent ; celle-ci non.
    """
    if not a_content:
        return 0.0
    disponibles = list(b_content)
    apparie = 0.0
    for word in a_content:
        best_index, best_match = -1, 0.0
        for index, other in enumerate(disponibles):
            degree = token_match(word, other)
            if degree > best_match:
                best_index, best_match = index, degree
        if best_index >= 0:
            apparie += best_match * token_weight(word)
            disponibles.pop(best_index)
    return _ratio(apparie, _total_weight(a_content))


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


def token_similarity(
    a_tokens: list[str],
    b_tokens: list[str],
    a_interrupted: bool = False,
) -> SimilarityResult:
    """Compare deux suites de tokens normalises.

    ``a_interrupted`` dit que la tentative ``a`` a ete laissee en plan
    (suspension, mot tronque). Une amorce coupee ressemble forcement peu a la
    version complete : la preuve vient alors du contexte, pas du vocabulaire.
    """
    result = SimilarityResult()
    if not a_tokens or not b_tokens:
        return result

    # Meme echelle avant de comparer : sans les tics ni les tournures
    # interchangeables, ce qui reste est la phrase elle-meme.
    a_tokens = normalize_oral(a_tokens)
    b_tokens = normalize_oral(b_tokens)
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

    # 3. sac de contenu : tolere les mots deplacees par la reformulation
    result.bag = _bag_similarity(a_content, b_content)

    # 4. charpente : meme redemarrage, ou meme chute
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

    result.score = _combine(result, a_content, b_content, a_interrupted)
    return result


def _combine(
    result: SimilarityResult,
    a_content: list[str],
    b_content: list[str],
    a_interrupted: bool,
) -> float:
    """Combine les mesures, en retenant la meilleure plutot qu'une moyenne.

    Une moyenne dilue : une reprise ou l'alignement vaut 0.9 mais la
    charpente 0.3 (la personne a garde sa phrase en changeant son attaque)
    tombe a un score mediocre, alors qu'une seule mesure elevee suffit a
    etablir la reprise. On retient donc la meilleure, puis on **promeut** le
    score quand une ancre solide confirme le redemarrage.

    Cette combinaison est volontairement optimiste. Ce qui protege les
    phrases valables n'est pas la prudence du score, mais les ancres exigees
    en amont (``restart_prefix`` / ``restart_structure``) et les garde-fous
    de ``retakes.py``.
    """
    core = max(result.align, result.bag, result.structure)

    # contenus identiques apres normalisation : c'est la meme phrase
    if a_content and b_content and result.containment >= 1.0 and not result.lost_content:
        if len(a_content) <= len(b_content):
            return 1.0

    # une amorce laissee en plan ne peut pas ressembler a la version complete
    if a_interrupted and core >= INTERRUPTED_CORE:
        return max(core, PROMOTED_STRONG)

    strong_anchor = result.structure >= ANCHOR_STRONG or result.align >= ALIGN_STRONG
    if strong_anchor:
        return max(core, PROMOTED_STRONG)

    # L'ancre moyenne est structurelle, jamais un simple recouvrement :
    # "Voici le classement complet de cette saison." et "Le classement a
    # beaucoup bouge en fin de saison." partagent la moitie de leur contenu
    # dans l'ordre sans etre deux tentatives de la meme phrase.
    if result.structure >= ANCHOR_MEDIUM and max(result.align, result.bag) >= SUPPORT_MEDIUM:
        return max(core, PROMOTED_MEDIUM)

    return core


def text_similarity(
    a_text: str, b_text: str, a_interrupted: bool = False
) -> SimilarityResult:
    """Version texte de ``token_similarity`` (normalisation incluse)."""
    from autorush.utils import tokenize

    return token_similarity(
        tokenize(a_text), tokenize(b_text), a_interrupted=a_interrupted
    )
