"""Controle qualite des raccords (cahier des charges, section 8)."""

from __future__ import annotations

from conftest import make_transcript

from autorush.analysis.decisions import analyze
from autorush.analysis.seams import check_seams
from autorush.config import Settings


def run(lines, style="dynamique"):
    transcript = make_transcript(lines)
    settings = Settings.for_style(style)
    result = analyze(transcript, settings, None, transcript.duration)
    warnings = check_seams(
        result.timeline,
        result.utterances,
        result.removed_word_indices,
        settings.seam,
        result.benign_word_indices,
    )
    return result, warnings


def categories(warnings):
    return {w.category for w in warnings}


def test_un_marqueur_conserve_est_signale_en_priorite_haute():
    _, warnings = run(
        [
            ("Je pense que le niveau a beaucoup progresse.", 0.6),
            ("Non.", 1.0),
            ("Et pourtant les resultats ne suivent pas.", 1.0),
        ]
    )
    assert "correction_conservee" in categories(warnings)
    assert any(w.severity == "haute" for w in warnings)


def test_une_coupe_anodine_nest_pas_signalee():
    """Retirer « euh » dans « Et euh du coup » ne casse pas la phrase."""
    _, warnings = run([("Et euh du coup on va voir tout ca ensemble.", 0.6)])
    assert "liaison_orpheline" not in categories(warnings)


def test_le_rush_de_demonstration_na_aucun_raccord_suspect(analysed):
    assert not analysed.seams, [w.as_dict() for w in analysed.seams]


def test_un_plan_tres_court_est_signale():
    from autorush.editing.timeline import Shot, Timeline

    timeline = Timeline(source_duration=10.0)
    timeline.shots.append(
        Shot(index=0, source_start=0.0, source_end=0.3, timeline_start=0.0)
    )
    settings = Settings.for_style("dynamique")
    warnings = check_seams(timeline, [], set(), settings.seam)
    assert "plan_court" in categories(warnings)


def test_les_avertissements_peuvent_etre_desactives():
    transcript = make_transcript([("Non.", 0.6), ("Et pourtant tout allait bien.", 1.0)])
    settings = Settings.for_style("dynamique")
    settings.seam.enabled = False
    result = analyze(transcript, settings, None, transcript.duration)
    assert not check_seams(
        result.timeline, result.utterances, result.removed_word_indices, settings.seam
    )


def test_les_avertissements_sont_serialisables():
    _, warnings = run(
        [
            ("Je pense que oui.", 0.6),
            ("Non.", 1.0),
            ("Et pourtant non.", 1.0),
        ]
    )
    for warning in warnings:
        data = warning.as_dict()
        assert "timecode" in data and "category" in data and "severity" in data
