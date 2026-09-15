"""Fragments orphelins (cahier des charges, section 7)."""

from __future__ import annotations

from conftest import final_text, make_transcript

from autorush.analysis.decisions import analyze
from autorush.analysis.fragments import detect_fragments
from autorush.analysis.utterances import build_utterances
from autorush.config import Settings


def run(lines, style="dynamique"):
    transcript = make_transcript(lines)
    settings = Settings.for_style(style)
    return analyze(transcript, settings, None, transcript.duration)


def test_un_fragment_isole_entre_deux_prises_est_retire():
    result = run(
        [
            ("Cette equipe etait redoutable en poule.", 0.8),
            ("Redoutable.", 1.1),
            ("Non, je recommence.", 1.2),
            ("Cette equipe etait absolument redoutable en poule cette annee.", 1.2),
        ]
    )
    text = final_text(result)
    assert text.count("Redoutable") <= 1
    assert "absolument redoutable" in text


def test_un_fragment_sans_contexte_est_conserve():
    """Sans indice autour, un fragment reste : la prudence primeprime."""
    transcript = make_transcript(
        [
            ("Le tournoi etait vraiment reussi cette annee.", 0.8),
            ("Voila.", 0.9),
            ("On passe maintenant au classement general.", 0.9),
        ]
    )
    settings = Settings.for_style("dynamique")
    result = analyze(transcript, settings, None, transcript.duration)
    assert "Voila." in final_text(result)


def test_un_fragment_porteur_de_contenu_unique_est_conserve():
    utterances = build_utterances(
        make_transcript(
            [
                ("Non, je recommence.", 0.8),
                ("Le stade de Yokohama etait complet.", 0.9),
                ("Passons a la suite du programme.", 0.9),
            ]
        )
    )
    settings = Settings.for_style("dynamique")
    settings.fragment.max_tokens = 12
    settings.fragment.max_duration = 6.0
    findings = detect_fragments(utterances, {0}, settings.fragment)
    porteurs = [f for f in findings if len(f.unique_content) >= 2]
    for finding in porteurs:
        assert finding.confidence < settings.fragment.min_delete_confidence


def test_desactivation_des_fragments():
    transcript = make_transcript(
        [
            ("Cette equipe etait redoutable.", 0.8),
            ("Redoutable.", 1.1),
            ("Non, je recommence.", 1.2),
            ("Cette equipe etait absolument redoutable cette annee.", 1.2),
        ]
    )
    settings = Settings.for_style("dynamique")
    settings.fragment.enabled = False
    result = analyze(transcript, settings, None, transcript.duration)
    assert not result.fragments


def test_les_fragments_sont_serialisables():
    result = run(
        [
            ("Cette equipe etait redoutable en poule.", 0.8),
            ("Redoutable.", 1.1),
            ("Non, je recommence.", 1.2),
            ("Cette equipe etait absolument redoutable en poule.", 1.2),
        ]
    )
    for fragment in result.fragments:
        data = fragment.as_dict()
        assert "confidence" in data and "reason" in data
