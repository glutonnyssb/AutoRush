"""Detection des reprises de phrase.

Le probleme
-----------
Une personne commence une phrase, se rate, hesite, recommence, puis finit par
dire la bonne version. Il faut supprimer les tentatives ratees **et** la phrase
de correction, sans jamais toucher a une phrase valide.

La methode
----------
1. On parcourt les enonces en cherchant une **bonne version** ``B`` : un enonce
   complet, qui se termine proprement.
2. On remonte en arriere depuis ``B`` tant que les enonces ressemblent a des
   tentatives ratees : phrase laissee en suspens, mot de liaison final, enonce
   court, marqueur de correction, ou enonce simplement tres similaire a ``B``.
   On s'arrete des qu'on rencontre une phrase complete **et** dissemblable :
   c'est la frontiere du groupe.
3. Le groupe n'est retenu que s'il possede un **ancrage** : soit un marqueur de
   correction explicite ("je recommence"), soit une tentative reellement
   similaire a ``B``. Sans ancrage, rien n'est supprime.
4. Chaque tentative recoit une confiance. En dessous du seuil, elle est
   **conservee** et simplement signalee dans le rapport.

Garde-fous
----------
* une tentative n'est retenue que si elle montre un **redemarrage** : premiers
  mots litteralement identiques, ou charpente commune avec ``B`` (meme attaque
  ou meme chute, voir ``similarity.py``). Du vocabulaire partage ne suffit
  jamais : deux phrases voisines d'un rush parlent forcement du meme sujet ;
* une phrase complete voit sa confiance chuter, d'autant plus que le
  redemarrage n'est pas prouve ;
* si supprimer une tentative ferait perdre la majeure partie de son
  information, la confiance chute (protection contre la perte d'information) ;
* un marqueur isole ("Non.") entre deux bonnes phrases n'est jamais supprime :
  il est signale comme raccord suspect (voir ``seams.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autorush.analysis.similarity import SimilarityResult, token_similarity
from autorush.analysis.utterances import Utterance
from autorush.config import RetakeSettings
from autorush.utils import clamp

#: seuil de similarite abaisse quand un marqueur ET un redemarrage litteral
#: sont presents : la preuve vient alors du contexte, plus du vocabulaire.
RESTART_GATE = 0.30

#: Une phrase porteuse de contenu ne peut jamais etre remplacee par un
#: enonce squelettique ("Et.", "Non.", "Mais bon."). Meme tres ressemblants,
#: ces deux enonces ne sont pas deux tentatives de la meme phrase : la
#: seconde ne dit rien.
PROTECTED_CONTENT_WORDS = 5
THIN_REPLACEMENT_WORDS = 1

#: Une tentative qui reproduit l'attaque a l'identique sans rien ajouter
#: s'est arretee en route. Une vraie correction, elle, refait son attaque
#: ("Et pourtant faut savoir que..." repris en "Mais pourtant en fait...")
#: et a le droit d'etre plus courte. Mesure sur rush reel : une troncature
#: a une attaque commune de 1.00, une reformulation plafonne a 0.73.
TRUNCATION_OPENING = 0.95

#: bonus de confiance
BONUS_STRONG_MARKER = 0.16
BONUS_WEAK_MARKER = 0.08
BONUS_RESTART_BASE = 0.10
BONUS_RESTART_PER_TOKEN = 0.045
BONUS_RESTART_CAP = 0.26
#: redemarrage prouve par la seule charpente (sans marqueur) : preuve plus
#: faible qu'un marqueur explicite, donc bonus plus mesure.
BONUS_RESTART_STRUCTURE = 0.10
BONUS_ABANDONED = 0.10
BONUS_BETTER_VERSION = 0.06
BONUS_SHORT_ATTEMPT = 0.05

#: penalites de confiance
PENALTY_COMPLETE_NO_MARKER = 0.20
PENALTY_COMPLETE_WITH_RESTART = 0.12
PENALTY_INFORMATION_LOSS = 0.25
PENALTY_LONG_ATTEMPT = 0.10

#: plancher de confiance accorde a une tentative *abandonnee* situee dans un
#: groupe ou la personne a explicitement annonce qu'elle recommencait.
STRONG_MARKER_FLOOR = 0.78
#: confiance accordee a la phrase de correction elle-meme
MARKER_CONFIDENCE_STRONG = 0.92
MARKER_CONFIDENCE_WEAK = 0.70
#: confiance d'un marqueur fort isole ("je recommence" seul)
LONE_STRONG_MARKER_CONFIDENCE = 0.88
#: longueur maximale d'un enonce reduit a un mot de liaison en suspens
LONE_CONNECTOR_TOKENS = 2
#: confiance de sa suppression : il ne porte aucun sens
LONE_CONNECTOR_CONFIDENCE = 0.85


@dataclass
class RetakeAttempt:
    """Une tentative (ou une phrase de correction) a supprimer ou signaler."""

    utterance_index: int
    start: float
    end: float
    text: str
    #: ``attempt`` = tentative ratee, ``marker`` = phrase de correction
    role: str = "attempt"
    confidence: float = 0.0
    reason: str = ""
    similarity: SimilarityResult | None = None
    #: mots de contenu qui disparaissent avec cette tentative
    lost_content: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict:
        data = {
            "utterance_index": self.utterance_index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "text": self.text,
            "role": self.role,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "lost_content": list(self.lost_content),
        }
        if self.similarity is not None:
            data["similarity"] = self.similarity.as_dict()
        return data


@dataclass
class RetakeGroup:
    """Un ensemble de tentatives et la version finalement conservee."""

    index: int
    kept_utterance_index: int
    kept_text: str
    kept_start: float
    kept_end: float
    attempts: list[RetakeAttempt] = field(default_factory=list)
    has_strong_marker: bool = False
    has_weak_marker: bool = False
    anchor: str = ""

    @property
    def start(self) -> float:
        return min((a.start for a in self.attempts), default=self.kept_start)

    @property
    def end(self) -> float:
        return self.kept_end

    @property
    def best_confidence(self) -> float:
        return max((a.confidence for a in self.attempts), default=0.0)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "kept_utterance_index": self.kept_utterance_index,
            "kept_text": self.kept_text,
            "kept_start": round(self.kept_start, 3),
            "kept_end": round(self.kept_end, 3),
            "has_strong_marker": self.has_strong_marker,
            "has_weak_marker": self.has_weak_marker,
            "anchor": self.anchor,
            "attempts": [a.as_dict() for a in self.attempts],
        }


# --------------------------------------------------------------------------- #
def _passes_gate(
    similarity: SimilarityResult,
    marker_between: bool,
    settings: RetakeSettings,
) -> bool:
    """La tentative ressemble-t-elle assez a la bonne version ?"""
    threshold = (
        settings.similarity_with_marker if marker_between else settings.similarity_without_marker
    )
    # Garde-fou : sans redemarrage visible, du vocabulaire commun ne prouve
    # rien. Deux phrases voisines d'un meme rush partagent toujours le
    # vocabulaire du sujet ("Voici le classement complet de cette saison." et
    # "Le classement a beaucoup bouge en fin de saison." partagent la moitie
    # de leurs mots de contenu sans etre deux tentatives de la meme phrase).
    if not (similarity.restart_prefix or similarity.restart_structure):
        return False
    shared_ok = (
        len(similarity.shared_content) >= settings.min_shared_content_words
        or similarity.restart_prefix
    )
    if not shared_ok:
        return False
    if similarity.score >= threshold:
        return True
    # redemarrage litteral apres un marqueur : la preuve est contextuelle
    if marker_between and similarity.restart_prefix and similarity.score >= RESTART_GATE:
        return True
    # prefixe tres long (la personne a redit mot pour mot le debut de sa phrase)
    if (
        similarity.prefix_tokens >= 4
        and similarity.prefix >= 0.70
        and similarity.score >= threshold * 0.75
    ):
        return True
    return False


def _kept_is_weaker(
    utterance: Utterance, kept: Utterance, similarity: SimilarityResult
) -> bool:
    """La version dite "conservee" est-elle en fait la moins bonne des deux ?

    Rien ne garantit que la derniere tentative soit la bonne : une personne
    qui se reprend s'arrete souvent en plan avant de repartir, et le moteur
    de transcription tronque volontiers son dernier segment. Supprimer la
    phrase complete au profit de cette amorce ferait perdre le propos.

    Deux situations :

    * la version conservee est visiblement abandonnee alors que la tentative
      allait au bout ;
    * la version conservee reproduit l'attaque a l'identique, n'ajoute rien
      et dit moins : elle s'est arretee en route. Une reformulation, qui
      refait son attaque, garde en revanche le droit d'etre plus courte.
    """
    if kept.is_abandoned and not utterance.is_abandoned:
        return True
    return (
        utterance.is_complete
        and not similarity.gained_content
        and len(kept.content) < len(utterance.content)
        and similarity.opening >= TRUNCATION_OPENING
    )


def _replacement_too_thin(utterance: Utterance, kept: Utterance) -> bool:
    """La version conservee est-elle trop maigre pour remplacer la tentative ?"""
    return (
        len(utterance.content) >= PROTECTED_CONTENT_WORDS
        and len(kept.content) <= THIN_REPLACEMENT_WORDS
    )


def _is_candidate_attempt(
    utterance: Utterance,
    similarity: SimilarityResult,
    marker_between: bool,
    settings: RetakeSettings,
) -> tuple[bool, str]:
    """Cet enonce peut-il etre une tentative ratee de ``B`` ?"""
    if utterance.is_abandoned:
        return True, "phrase laissee en suspens"
    if utterance.token_count < settings.complete_utterance_tokens and not utterance.is_complete:
        return True, "enonce court et incomplet"
    if _passes_gate(similarity, marker_between, settings):
        return True, "formulation tres proche de la version conservee"
    return False, ""


def _attempt_confidence(
    utterance: Utterance,
    kept: Utterance,
    similarity: SimilarityResult,
    group_strong: bool,
    group_weak: bool,
    settings: RetakeSettings,
) -> tuple[float, list[str], list[str]]:
    """Calcule la confiance de suppression et explique le calcul."""
    reasons: list[str] = []
    score = similarity.score

    # Preuve d'un redemarrage : soit les premiers mots sont litteralement
    # identiques, soit la charpente est commune (meme attaque ou meme chute).
    # Le second cas couvre "Au debut du Ultimate, c'etait plutot les
    # Etats-Unis..." / "Au debut, c'etait vraiment les Etats-Unis..." : aucun
    # prefixe exact, mais la meme phrase est visiblement reprise.
    restarted = similarity.restart_prefix or similarity.restart_structure
    restart_with_marker = similarity.restart_prefix and (group_strong or group_weak)

    if group_strong:
        score += BONUS_STRONG_MARKER
        reasons.append("marqueur explicite de reprise")
    elif group_weak:
        score += BONUS_WEAK_MARKER
        reasons.append("marqueur de correction")

    if restart_with_marker:
        bonus = min(
            BONUS_RESTART_CAP,
            BONUS_RESTART_BASE + BONUS_RESTART_PER_TOKEN * similarity.prefix_tokens,
        )
        score += bonus
        reasons.append(
            f"redemarrage litteral sur {similarity.prefix_tokens} mots identiques"
        )
    elif similarity.restart_structure:
        score += BONUS_RESTART_STRUCTURE
        reasons.append("meme phrase redemarree (attaque ou chute identique)")

    if utterance.is_abandoned:
        score += BONUS_ABANDONED
        reasons.append("tentative visiblement abandonnee")

    if kept.is_complete and kept.token_count >= utterance.token_count:
        score += BONUS_BETTER_VERSION
        reasons.append("version conservee plus complete")

    if len(utterance.content) < 4:
        score += BONUS_SHORT_ATTEMPT

    # -- penalites -------------------------------------------------------- #
    if utterance.is_complete and not group_strong:
        penalty = PENALTY_COMPLETE_WITH_RESTART if restarted else PENALTY_COMPLETE_NO_MARKER
        score -= penalty
        reasons.append("prudence : la tentative est une phrase complete")

    # perte d'information : quand un redemarrage litteral est prouve, la fin de
    # la phrase est precisement ce que la personne corrige -> on ne compte que
    # les mots perdus situes dans le prefixe commun.
    if restart_with_marker and similarity.restart_prefix:
        lost = similarity.lost_inside_prefix
        excessive_loss = bool(lost)
    else:
        lost = similarity.lost_content
        excessive_loss = similarity.loss_ratio > settings.max_information_loss_ratio
    if excessive_loss:
        score -= PENALTY_INFORMATION_LOSS
        reasons.append(
            "prudence : "
            + ", ".join(lost[:4])
            + (" ..." if len(lost) > 4 else "")
            + " n'existe(nt) pas dans la version conservee"
        )

    if utterance.duration > settings.long_attempt_duration and not group_strong:
        score -= PENALTY_LONG_ATTEMPT
        reasons.append("prudence : tentative longue sans marqueur explicite")

    # -- plancher des groupes explicites ---------------------------------- #
    if group_strong and utterance.is_abandoned:
        score = max(score, STRONG_MARKER_FLOOR)

    return clamp(score, 0.0, 1.0), reasons, list(lost)


# --------------------------------------------------------------------------- #
def detect_retakes(
    utterances: list[Utterance], settings: RetakeSettings
) -> tuple[list[RetakeGroup], list[RetakeAttempt]]:
    """Retourne les groupes de reprises et les marqueurs forts isoles."""
    if not settings.enabled or not utterances:
        return [], []

    groups: list[RetakeGroup] = []
    in_group: set[int] = set()

    for position, kept in enumerate(utterances):
        if kept.is_marker_only:
            continue
        # la version conservee doit ressembler a une phrase valable
        if not (kept.is_complete or kept.token_count >= settings.complete_utterance_tokens):
            continue

        collected: list[tuple[Utterance, SimilarityResult, str]] = []
        markers: list[Utterance] = []
        group_strong = False
        group_weak = False
        distance = 0
        cursor = position - 1

        while cursor >= 0 and distance < settings.max_utterance_distance:
            candidate = utterances[cursor]
            if kept.start - candidate.end > settings.search_window:
                break
            if candidate.index in in_group:
                break

            if candidate.is_marker_only:
                markers.append(candidate)
                if candidate.has_strong_marker:
                    group_strong = True
                else:
                    group_weak = True
                cursor -= 1
                distance += 1
                continue

            if _replacement_too_thin(candidate, kept):
                break
            similarity = token_similarity(
                candidate.tokens, kept.tokens, a_interrupted=candidate.is_abandoned
            )
            if _kept_is_weaker(candidate, kept, similarity):
                break
            marker_between = group_strong or group_weak
            ok, why = _is_candidate_attempt(candidate, similarity, marker_between, settings)
            if not ok:
                break
            collected.append((candidate, similarity, why))
            cursor -= 1
            distance += 1

        if not collected:
            continue

        # -- ancrage : sans preuve, on ne touche a rien -------------------- #
        marker_between = group_strong or group_weak
        similar_anchor = any(
            _passes_gate(sim, marker_between, settings) for _, sim, _ in collected
        )
        if group_strong:
            anchor = "marqueur explicite"
        elif similar_anchor:
            anchor = "similarite forte"
        elif group_weak and any(u.is_abandoned for u, _, _ in collected):
            # un marqueur faible + une tentative abandonnee : indice reel mais
            # insuffisant seul ; la confiance fera le tri.
            anchor = "marqueur faible + tentative abandonnee"
        else:
            continue

        group = RetakeGroup(
            index=len(groups),
            kept_utterance_index=kept.index,
            kept_text=kept.text,
            kept_start=kept.start,
            kept_end=kept.end,
            has_strong_marker=group_strong,
            has_weak_marker=group_weak,
            anchor=anchor,
        )

        for candidate, similarity, why in collected:
            confidence, reasons, lost = _attempt_confidence(
                candidate, kept, similarity, group_strong, group_weak, settings
            )
            group.attempts.append(
                RetakeAttempt(
                    utterance_index=candidate.index,
                    start=candidate.start,
                    end=candidate.end,
                    text=candidate.text,
                    role="attempt",
                    confidence=confidence,
                    reason="; ".join([why] + reasons),
                    similarity=similarity,
                    lost_content=lost,
                )
            )

        for marker in markers:
            confidence = (
                MARKER_CONFIDENCE_STRONG
                if marker.has_strong_marker
                else MARKER_CONFIDENCE_WEAK
            )
            group.attempts.append(
                RetakeAttempt(
                    utterance_index=marker.index,
                    start=marker.start,
                    end=marker.end,
                    text=marker.text,
                    role="marker",
                    confidence=confidence,
                    reason="phrase de correction orale",
                )
            )

        group.attempts.sort(key=lambda a: a.start)
        groups.append(group)
        in_group.update(a.utterance_index for a in group.attempts)

    # -- chaines descendantes --------------------------------------------- #
    # La passe precedente cherche, pour chaque enonce, une meilleure version
    # *plus recente*. Elle ne voit donc rien quand la personne s'essouffle :
    # une ouverture ratee ou chaque tentative est plus courte que la
    # precedente. La meilleure version est alors la premiere, et ce sont les
    # suivantes qu'il faut retirer.
    for position, best in enumerate(utterances):
        if best.index in in_group or best.is_marker_only:
            continue
        if not best.is_complete or best.is_abandoned:
            continue
        collected = []
        distance = 0
        cursor = position + 1
        while cursor < len(utterances) and distance < settings.max_utterance_distance:
            candidate = utterances[cursor]
            if candidate.index in in_group or candidate.is_marker_only:
                break
            if candidate.start - best.end > settings.search_window:
                break
            similarity = token_similarity(
                best.tokens, candidate.tokens, a_interrupted=candidate.is_abandoned
            )
            # on ne retire une tentative posterieure que si elle est
            # visiblement la plus faible des deux
            if not _kept_is_weaker(best, candidate, similarity):
                break
            if not _passes_gate(similarity, False, settings):
                break
            collected.append((candidate, similarity))
            cursor += 1
            distance += 1

        if not collected:
            continue

        group = RetakeGroup(
            index=len(groups),
            kept_utterance_index=best.index,
            kept_text=best.text,
            kept_start=best.start,
            kept_end=best.end,
            anchor="version la plus complete conservee",
        )
        for candidate, similarity in collected:
            confidence, reasons, lost = _attempt_confidence(
                candidate, best, similarity, False, False, settings
            )
            group.attempts.append(
                RetakeAttempt(
                    utterance_index=candidate.index,
                    start=candidate.start,
                    end=candidate.end,
                    text=candidate.text,
                    role="attempt",
                    confidence=confidence,
                    reason="; ".join(
                        ["tentative plus faible que la version precedente", *reasons]
                    ),
                    similarity=similarity,
                    lost_content=lost,
                )
            )
        group.attempts.sort(key=lambda a: a.start)
        groups.append(group)
        in_group.update(a.utterance_index for a in group.attempts)

    # -- connecteurs restes en plan --------------------------------------- #
    # "et", "mais", "donc" seuls, laisses en suspens avant un redemarrage :
    # aucun contenu, aucune phrase. Ils n'ont pas besoin d'une version de
    # reference pour etre retires, il n'y a rien a comparer.
    for utterance in utterances:
        if utterance.index in in_group or utterance.is_marker_only:
            continue
        if not utterance.is_abandoned:
            continue
        if utterance.token_count > LONE_CONNECTOR_TOKENS or utterance.content:
            continue
        if not utterance.ends_dangling and not utterance.ends_suspension:
            continue
        groups.append(
            RetakeGroup(
                index=len(groups),
                kept_utterance_index=utterance.index,
                kept_text="",
                kept_start=utterance.end,
                kept_end=utterance.end,
                anchor="connecteur reste en plan",
                attempts=[
                    RetakeAttempt(
                        utterance_index=utterance.index,
                        start=utterance.start,
                        end=utterance.end,
                        text=utterance.text,
                        role="attempt",
                        confidence=LONE_CONNECTOR_CONFIDENCE,
                        reason="mot de liaison laisse en plan, sans contenu",
                    )
                ],
            )
        )
        in_group.add(utterance.index)

    # -- marqueurs forts isoles ------------------------------------------- #
    lone_markers: list[RetakeAttempt] = []
    for utterance in utterances:
        if utterance.index in in_group:
            continue
        if utterance.is_marker_only and utterance.has_strong_marker:
            lone_markers.append(
                RetakeAttempt(
                    utterance_index=utterance.index,
                    start=utterance.start,
                    end=utterance.end,
                    text=utterance.text,
                    role="marker",
                    confidence=LONE_STRONG_MARKER_CONFIDENCE,
                    reason="annonce explicite de reprise, sans contenu utile",
                )
            )

    return groups, lone_markers
