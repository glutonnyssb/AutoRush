"""Reprises de phrases et corrections orales : le coeur d'AutoRush.

Les trois premiers tests reprennent mot pour mot les exemples du cahier des
charges. Les suivants verifient la regle non negociable : **ne jamais supprimer
une bonne phrase**.
"""

from __future__ import annotations

from conftest import final_text, make_transcript

from autorush.analysis.decisions import analyze
from autorush.analysis.retakes import detect_retakes
from autorush.analysis.similarity import text_similarity
from autorush.analysis.utterances import build_utterances
from autorush.config import Settings


def run(lines, style="dynamique"):
    transcript = make_transcript(lines)
    settings = Settings.for_style(style)
    return analyze(transcript, settings, None, transcript.duration)


def removed_texts(result):
    return [r.text for r in result.applied_removals if r.source in {"retake", "marker"}]


# --------------------------------------------------------------------------- #
# Cahier des charges, section 5
# --------------------------------------------------------------------------- #
def test_reprise_avec_marqueur_garde_la_derniere_version():
    result = run(
        [
            ("Les joueurs japonais etaient plutot...", 0.9),
            ("Enfin...", 0.7),
            ("Ils etaient plutot bons...", 0.8),
            ("Non, je recommence.", 1.4),
            ("Les joueurs japonais etaient vraiment excellents cette annee.", 1.1),
        ]
    )
    text = final_text(result)
    assert text == "Les joueurs japonais etaient vraiment excellents cette annee."


# --------------------------------------------------------------------------- #
# Cahier des charges, section 6
# --------------------------------------------------------------------------- #
def test_correction_orale_supprime_tentative_et_phrase_de_correction():
    result = run(
        [
            ("On a eu Acola, Miya, Asimo...", 0.7),
            ("Tous ces joueurs etaient...", 0.8),
            ("Raru...", 0.9),
            ("Non, je recommence.", 1.5),
            ("On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru.", 1.2),
        ]
    )
    text = final_text(result)
    assert text == "On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru."
    assert "je recommence" not in text.lower()


# --------------------------------------------------------------------------- #
# Cahier des charges, section 7
# --------------------------------------------------------------------------- #
def test_redemarrage_litteral_apres_marqueur_faible():
    result = run(
        [
            ("Ces joueurs etaient pas extremement forts.", 0.8),
            ("Attends.", 1.3),
            ("Ces joueurs etaient absolument redoutables en poule.", 1.1),
        ]
    )
    text = final_text(result)
    assert text == "Ces joueurs etaient absolument redoutables en poule."


# --------------------------------------------------------------------------- #
# Securite : aucune bonne phrase supprimee
# --------------------------------------------------------------------------- #
def test_deux_phrases_valables_ne_sont_jamais_confondues():
    lines = [
        ("Le niveau global a explose cette saison.", 0.7),
        ("Le niveau des equipes europeennes progresse aussi.", 0.7),
    ]
    result = run(lines)
    assert not removed_texts(result)
    assert "explose" in final_text(result)
    assert "europeennes" in final_text(result)


def test_un_mot_similaire_plus_tard_ne_supprime_rien():
    """Une phrase correcte n'est pas supprimee parce qu'un mot revient plus tard."""
    result = run(
        [
            ("Les joueurs japonais ont domine la competition.", 0.7),
            ("Le public etait vraiment nombreux ce week-end.", 0.7),
            ("Les joueurs coreens ont fini juste derriere.", 0.7),
        ]
    )
    assert not removed_texts(result)


def test_non_isole_entre_deux_bonnes_phrases_est_conserve_et_signale():
    """Cahier des charges section 8 : le « Non » reste, mais il est signale."""
    from autorush.analysis.seams import check_seams

    transcript = make_transcript(
        [
            ("Je pense que le niveau a beaucoup progresse.", 0.6),
            ("Non.", 1.0),
            ("Et pourtant les resultats ne suivent pas.", 1.0),
        ]
    )
    settings = Settings.for_style("dynamique")
    result = analyze(transcript, settings, None, transcript.duration)
    assert "Non." in final_text(result)
    warnings = check_seams(
        result.timeline,
        result.utterances,
        result.removed_word_indices,
        settings.seam,
        result.benign_word_indices,
    )
    categories = {w.category for w in warnings}
    assert "correction_conservee" in categories
    haute = [w for w in warnings if w.severity == "haute"]
    assert haute, "un marqueur conserve doit etre signale en priorite haute"


def test_marqueur_fort_isole_est_supprime():
    """« Je recommence » n'est jamais du contenu utile."""
    result = run(
        [
            ("Voici le classement complet de cette saison.", 0.7),
            ("Je recommence.", 1.0),
            ("Le classement a beaucoup bouge en fin de saison.", 1.0),
        ]
    )
    assert "je recommence" not in final_text(result).lower()
    assert "classement complet" in final_text(result)


def test_mode_silences_only_ne_touche_a_aucune_parole():
    transcript = make_transcript(
        [
            ("Les joueurs japonais etaient plutot...", 0.9),
            ("Non, je recommence.", 1.4),
            ("Les joueurs japonais etaient excellents cette annee.", 1.1),
        ]
    )
    settings = Settings.for_style("dynamique")
    settings.silences_only = True
    result = analyze(transcript, settings, None, transcript.duration)
    assert not result.removed_word_indices
    assert "je recommence" in final_text(result).lower()


def test_desactivation_des_reprises():
    transcript = make_transcript(
        [
            ("Les joueurs japonais etaient plutot...", 0.9),
            ("Non, je recommence.", 1.4),
            ("Les joueurs japonais etaient excellents cette annee.", 1.1),
        ]
    )
    settings = Settings.for_style("dynamique")
    settings.retake.enabled = False
    result = analyze(transcript, settings, None, transcript.duration)
    assert not result.retake_groups


def test_seuil_de_confiance_eleve_conserve_et_signale():
    transcript = make_transcript(
        [
            ("Ces joueurs etaient pas extremement forts.", 0.8),
            ("Attends.", 1.3),
            ("Ces joueurs etaient absolument redoutables en poule.", 1.1),
        ]
    )
    settings = Settings.for_style("dynamique")
    settings.retake.min_delete_confidence = 0.99
    result = analyze(transcript, settings, None, transcript.duration)
    assert "extremement" in final_text(result)
    assert [f for f in result.flags if f.category == "reprise_possible"]


def test_filet_de_securite_limite_la_parole_supprimee():
    """Le filet annule les decisions les moins sures quand elles s'accumulent."""
    lines = []
    for index in range(6):
        lines.append((f"La sequence numero {index} etait plutot...", 0.9))
        lines.append(("Non, je recommence.", 1.3))
        lines.append((f"La sequence numero {index} etait vraiment reussie.", 1.1))
    transcript = make_transcript(lines)
    settings = Settings.for_style("dynamique")
    settings.retake.max_removed_speech_ratio = 0.05
    settings.retake.cap_min_speech_duration = 0.0
    # rien n'est exempte : on veut observer le filet lui-meme
    settings.retake.cap_exempt_confidence = 1.01
    result = analyze(transcript, settings, None, transcript.duration)
    assert [f for f in result.flags if f.category == "limite_securite"]


def test_le_filet_nannule_jamais_une_consigne_explicite():
    """« Je recommence » doit etre respecte, meme sur un rush tres bafouille."""
    lines = []
    for index in range(6):
        lines.append((f"La sequence numero {index} etait plutot...", 0.9))
        lines.append(("Non, je recommence.", 1.3))
        lines.append((f"La sequence numero {index} etait vraiment reussie.", 1.1))
    transcript = make_transcript(lines)
    settings = Settings.for_style("dynamique")
    settings.retake.max_removed_speech_ratio = 0.01
    settings.retake.cap_min_speech_duration = 0.0
    result = analyze(transcript, settings, None, transcript.duration)
    assert not [f for f in result.flags if f.category == "limite_securite"]
    assert "je recommence" not in final_text(result).lower()


# --------------------------------------------------------------------------- #
# Similarite
# --------------------------------------------------------------------------- #
def test_similarite_detecte_le_redemarrage_litteral():
    result = text_similarity(
        "Les joueurs japonais etaient plutot",
        "Les joueurs japonais etaient vraiment excellents cette annee.",
    )
    assert result.restart_prefix
    assert result.prefix_tokens >= 4
    assert result.score > 0.55


def test_similarite_faible_entre_phrases_differentes():
    result = text_similarity(
        "Le niveau global a explose cette saison.",
        "Merci d'avoir regarde cette video jusqu'au bout.",
    )
    assert result.score < 0.20
    assert not result.restart_prefix


def test_containment_detecte_la_version_allongee():
    result = text_similarity(
        "On a eu Acola, Miya, Asimo",
        "On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru.",
    )
    assert result.containment == 1.0
    assert not result.lost_content


def test_groupe_sans_ancrage_nest_pas_retenu():
    """Sans marqueur ni similarite, aucun groupe de reprise n'est cree."""
    transcript = make_transcript(
        [
            ("Bref.", 0.8),
            ("Le tournoi se deroulait a Tokyo au mois de juillet.", 0.9),
        ]
    )
    utterances = build_utterances(transcript)
    settings = Settings.for_style("dynamique")
    groups, _ = detect_retakes(utterances, settings.retake)
    for group in groups:
        assert group.attempts, "un groupe sans tentative ne doit pas exister"


def test_un_connecteur_reste_en_plan_est_retire():
    """``et`` seul, laisse en suspens avant un redemarrage, ne dit rien.

    Cas releve sur un rush reel : "Et meme si au debut, Et meme si... / et /
    meme si avec quelques joueurs..." laissait trois amorces dans le montage.
    Un mot de liaison isole n'a besoin d'aucune version de reference pour
    etre retire : il n'y a rien a comparer.
    """
    # blancs longs de chaque cote : "et" reste seul, comme sur le rush reel
    result = run(
        [
            ("Le niveau a vraiment explose cette saison au Japon.", 0.8),
            ("et", 1.6),
            ("meme si quelques joueurs tenaient encore le niveau europeen.", 1.8),
        ]
    )
    texte = final_text(result)
    assert " et " not in f" {texte} ", f"le connecteur isole subsiste : {texte!r}"
    assert "explose" in texte
    assert "europeen" in texte


def test_un_connecteur_porteur_de_sens_nest_pas_retire():
    """``Et voila.`` porte un contenu : on n'y touche pas."""
    result = run(
        [
            ("Le niveau a vraiment explose cette saison au Japon.", 0.8),
            ("Et voila le resultat final du classement.", 0.6),
        ]
    )
    assert "resultat" in final_text(result)


def test_un_enonce_court_mais_fini_nest_pas_pris_pour_un_connecteur():
    """Une phrase courte qui se termine proprement reste en place."""
    result = run(
        [
            ("Le niveau a vraiment explose cette saison au Japon.", 0.8),
            ("Enorme.", 0.6),
            ("Les joueurs coreens ont fini juste derriere cette annee.", 0.6),
        ]
    )
    assert "enorme" in final_text(result).lower()
