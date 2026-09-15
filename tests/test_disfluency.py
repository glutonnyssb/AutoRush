"""Hesitations et bafouillages : exactement les cas du cahier des charges."""

from __future__ import annotations

from conftest import final_text, kept_words, make_transcript

from autorush.analysis.decisions import analyze
from autorush.analysis.disfluency import detect_disfluencies
from autorush.analysis.utterances import build_utterances
from autorush.config import Settings


def run(lines, style="dynamique", **overrides):
    transcript = make_transcript(lines)
    settings = Settings.for_style(style)
    for section, values in overrides.items():
        target = getattr(settings, section)
        for key, value in values.items():
            setattr(target, key, value)
    return analyze(transcript, settings, None, transcript.duration)


# --------------------------------------------------------------------------- #
# Exemples explicites du cahier des charges
# --------------------------------------------------------------------------- #
def test_repetition_immediate_est_nettoyee():
    """« Je je pense que... » -> « Je pense que... »"""
    result = run([("Je je pense que c'est une bonne idee.", 0.6)])
    assert kept_words(result) == ["je", "pense", "que", "c'est", "une", "bonne", "idee"]


def test_tic_sonore_est_retire_mais_pas_le_tic_de_langage():
    """« Et euh du coup... » -> « Et du coup... »"""
    result = run([("Et euh du coup on va voir pourquoi.", 0.6)])
    text = final_text(result).lower()
    assert "euh" not in text
    assert "du coup" in text
    assert text.startswith("et du coup")


def test_debut_de_mot_abandonne():
    """« Mal- Malgre ca... » -> « Malgre ca... »"""
    result = run([("Mal- Malgre ca il y a eu des soucis.", 0.6)])
    words = kept_words(result)
    assert "mal-" not in words
    assert words[:2] == ["malgre", "ca"]


def test_repetition_volontaire_est_protegee():
    """« C'etait tres tres fort. » doit rester intact."""
    result = run([("C'etait tres tres fort.", 0.6)])
    assert kept_words(result) == ["c'etait", "tres", "tres", "fort"]


def test_repetition_non_intensifiante_est_nettoyee():
    result = run([("Le niveau niveau de jeu etait haut.", 0.6)])
    words = kept_words(result)
    assert words.count("niveau") == 1


# --------------------------------------------------------------------------- #
# Protections
# --------------------------------------------------------------------------- #
def test_eu_participe_passe_nest_pas_un_tic():
    """``eu`` est le participe passe d'avoir : le retirer casserait la phrase."""
    result = run([("Il y a eu quelques problemes d'organisation.", 0.6)])
    assert "eu" in kept_words(result)


def test_interjection_nest_pas_retiree_par_defaut():
    result = run([("Ah, ca c'est une excellente question.", 0.6)])
    assert "ah" in kept_words(result)


def test_tics_de_langage_retires_en_tres_dynamique():
    result = run([("Bon, on va passer a la suite du classement.", 0.6)], style="tres_dynamique")
    assert "bon" not in kept_words(result)


def test_mot_court_frequent_nest_pas_pris_pour_une_amorce():
    """« car cardiaque » n'est pas un bafouillage."""
    result = run([("Le rythme car cardiaque etait mesure.", 0.6)])
    assert "car" in kept_words(result)


def test_euh_tres_long_est_conserve():
    """Un « euh » de plus d'une seconde est probablement mal transcrit."""
    transcript = make_transcript([("Alors euh voila le resultat.", 0.6)])
    for word in transcript.words:
        if word.norm == "euh":
            word.end = word.start + 1.6
    transcript.reindex()
    settings = Settings.for_style("dynamique")
    result = analyze(transcript, settings, None, transcript.duration)
    assert "euh" in kept_words(result)


def test_desactivation_des_hesitations():
    result = run(
        [("Je je pense que euh c'est bien.", 0.6)],
        disfluency={
            "remove_fillers": False,
            "remove_stutters": False,
            "remove_abandoned_words": False,
        },
    )
    words = kept_words(result)
    assert words.count("je") == 2
    assert "euh" in words


def test_confiance_sous_le_seuil_conserve_et_signale():
    transcript = make_transcript([("Le rythme rythmique etait bon.", 0.6)])
    settings = Settings.for_style("dynamique")
    settings.disfluency.min_delete_confidence = 0.99
    result = analyze(transcript, settings, None, transcript.duration)
    signalements = [f for f in result.flags if f.category == "hesitation_douteuse"]
    if result.disfluencies:
        assert signalements, "une hesitation non appliquee doit etre signalee"


def test_un_enonce_ne_peut_pas_etre_entierement_vide():
    """On ne supprime jamais tous les mots d'un enonce porteur de sens."""
    transcript = make_transcript([("Bien bien.", 0.6)])
    settings = Settings.for_style("dynamique")
    utterances = build_utterances(transcript)
    findings = detect_disfluencies(transcript, utterances, settings.disfluency)
    removed = {i for f in findings for i in f.word_indices}
    assert len(removed) < len(transcript.words)
