"""Mesure de similarite entre deux tentatives de phrase.

Aucun modele externe n'est utilise : la comparaison combine quatre mesures
lexicales complementaires, dont chacune capture un aspect different d'une
reprise de phrase.

=================  =========================================================
Mesure             Ce qu'elle detecte
=================  =========================================================
``sequence``       reformulation partielle (ordre des mots conserve)
``prefix``         redemarrage litteral ("Les joueurs japonais etaient...")
``jaccard``        vocabulaire commun, insensible a l'ordre
``containment``    la nouvelle version couvre-t-elle l'ancienne ?
=================  =========================================================

``containment`` est la mesure decisive : une reprise reussie **contient** en
general l'information de la tentative ratee, plus la suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from autorush.analysis.lexicon import content_words

#: ponderation des quatre mesures (somme = 1)
W_SEQUENCE = 0.34
W_PREFIX = 0.24
W_JACCARD = 0.16
W_CONTAINMENT = 0.26

#: longueur minimale du prefixe commun pour parler de "redemarrage litteral"
MIN_RESTART_PREFIX = 3
#: part minimale du plus court enonce couverte par le prefixe commun
MIN_RESTART_RATIO = 0.42


@dataclass
class SimilarityResult:
    """Detail de la comparaison entre une tentative ``a`` et une reprise ``b``."""

    score: float = 0.0
    sequence: float = 0.0
    prefix: float = 0.0
    jaccard: float = 0.0
    containment: float = 0.0
    #: longueur (en tokens) du prefixe litteralement commun
    prefix_tokens: int = 0
    #: ``a`` et ``b`` commencent-ils par la meme phrase ?
    restart_prefix: bool = False
    shared_content: list[str] = field(default_factory=list)
    lost_content: list[str] = field(default_factory=list)
    gained_content: list[str] = field(default_factory=list)
    #: mots de contenu de ``a`` perdus qui se trouvent dans le prefixe commun
    lost_inside_prefix: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "sequence": round(self.sequence, 4),
            "prefix": round(self.prefix, 4),
            "jaccard": round(self.jaccard, 4),
            "containment": round(self.containment, 4),
            "prefix_tokens": self.prefix_tokens,
            "restart_prefix": self.restart_prefix,
            "shared_content": list(self.shared_content),
            "lost_content": list(self.lost_content),
        }


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
    # on autorise la suppression d'un mot dans a, puis dans b
    for skip_index in range(min(len(a), 6)):
        candidate = a[:skip_index] + a[skip_index + 1 :]
        best = max(best, common_prefix_length(candidate, b))
    for skip_index in range(min(len(b), 6)):
        candidate = b[:skip_index] + b[skip_index + 1 :]
        best = max(best, common_prefix_length(a, candidate))
    return best


def token_similarity(a_tokens: list[str], b_tokens: list[str]) -> SimilarityResult:
    """Compare deux suites de tokens normalises."""
    result = SimilarityResult()
    if not a_tokens or not b_tokens:
        return result

    # 1. similarite de sequence (ordre conserve)
    matcher = SequenceMatcher(a=a_tokens, b=b_tokens, autojunk=False)
    result.sequence = matcher.ratio()

    # 2. prefixe commun
    prefix_len = _fuzzy_prefix_length(a_tokens, b_tokens)
    result.prefix_tokens = prefix_len
    shorter = min(len(a_tokens), len(b_tokens))
    result.prefix = prefix_len / shorter if shorter else 0.0
    result.restart_prefix = (
        prefix_len >= MIN_RESTART_PREFIX and result.prefix >= MIN_RESTART_RATIO
    )

    # 3. vocabulaire de contenu
    a_content = content_words(a_tokens)
    b_content = content_words(b_tokens)
    set_a, set_b = set(a_content), set(b_content)
    if set_a or set_b:
        union = set_a | set_b
        intersection = set_a & set_b
        result.jaccard = len(intersection) / len(union) if union else 0.0
        result.containment = len(intersection) / len(set_a) if set_a else 0.0
        result.shared_content = sorted(intersection)
        result.lost_content = [w for w in dict.fromkeys(a_content) if w not in set_b]
        result.gained_content = [w for w in dict.fromkeys(b_content) if w not in set_a]
    else:
        # deux enonces sans mot de contenu : on se rabat sur la sequence
        result.containment = result.sequence

    prefix_tokens_set = set(a_tokens[:prefix_len])
    result.lost_inside_prefix = [w for w in result.lost_content if w in prefix_tokens_set]

    result.score = (
        W_SEQUENCE * result.sequence
        + W_PREFIX * result.prefix
        + W_JACCARD * result.jaccard
        + W_CONTAINMENT * result.containment
    )
    return result


def text_similarity(a_text: str, b_text: str) -> SimilarityResult:
    """Version texte de ``token_similarity`` (normalisation incluse)."""
    from autorush.utils import tokenize

    return token_similarity(tokenize(a_text), tokenize(b_text))
