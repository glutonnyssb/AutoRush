"""Decoupage en enonces."""

from __future__ import annotations

from conftest import make_transcript

from autorush.analysis.utterances import build_utterances, utterance_at


def test_decoupage_a_la_ponctuation():
    transcript = make_transcript(
        [("Premiere phrase.", 0.6), ("Deuxieme phrase.", 0.5)]
    )
    utterances = build_utterances(transcript)
    assert len(utterances) == 2
    assert utterances[0].text == "Premiere phrase."
    assert utterances[0].break_reason == "debut"


def test_decoupage_a_un_blanc_long():
    transcript = make_transcript(
        [("Une phrase sans point final", 0.6), ("une autre suite", 1.5)]
    )
    utterances = build_utterances(transcript)
    assert len(utterances) == 2
    assert utterances[1].break_reason in {"blanc", "blanc_long"}


def test_une_phrase_en_suspens_est_abandonnee():
    transcript = make_transcript([("Les joueurs etaient plutot...", 0.6)])
    utterance = build_utterances(transcript)[0]
    assert utterance.ends_suspension
    assert utterance.is_abandoned
    assert not utterance.is_complete


def test_une_phrase_finie_est_complete():
    transcript = make_transcript(
        [("Les joueurs japonais etaient vraiment excellents cette annee.", 0.6)]
    )
    utterance = build_utterances(transcript)[0]
    assert utterance.is_complete
    assert not utterance.is_abandoned
    assert utterance.completeness() > 0.7


def test_une_phrase_terminee_sur_une_liaison_est_abandonnee():
    transcript = make_transcript([("Tous ces joueurs etaient", 0.6)])
    utterance = build_utterances(transcript)[0]
    assert utterance.ends_dangling
    assert utterance.is_abandoned


def test_un_mot_tronque_est_reconnu():
    transcript = make_transcript([("Mal-", 0.6), ("Malgre ca.", 0.3)])
    utterances = build_utterances(transcript)
    assert utterances[0].ends_truncated


def test_un_marqueur_est_isole():
    transcript = make_transcript(
        [("Non, je recommence.", 0.8), ("La bonne version maintenant.", 1.0)]
    )
    utterances = build_utterances(transcript)
    assert utterances[0].is_marker_only
    assert utterances[0].has_strong_marker
    assert not utterances[1].is_marker_only


def test_un_marqueur_sans_ponctuation_est_scinde():
    transcript = make_transcript(
        [("Non je recommence les joueurs japonais etaient excellents", 0.8)]
    )
    utterances = build_utterances(transcript)
    assert len(utterances) == 2
    assert utterances[0].is_marker_only
    assert "joueurs" in utterances[1].norm


def test_les_blancs_sont_mesures():
    transcript = make_transcript([("Phrase une.", 0.8), ("Phrase deux.", 1.4)])
    utterances = build_utterances(transcript)
    assert utterances[0].gap_before > 0
    assert abs(utterances[1].gap_before - 1.4) < 0.05
    assert abs(utterances[0].gap_after - 1.4) < 0.05


def test_tokens_et_mots_de_contenu():
    transcript = make_transcript([("Le niveau global a explose cette saison.", 0.6)])
    utterance = build_utterances(transcript)[0]
    assert "niveau" in utterance.content
    assert "le" not in utterance.content
    assert utterance.token_count == 7


def test_recherche_par_instant():
    transcript = make_transcript([("Phrase une.", 0.6), ("Phrase deux.", 1.0)])
    utterances = build_utterances(transcript)
    cible = utterances[1]
    trouve = utterance_at(utterances, 0.5 * (cible.start + cible.end))
    assert trouve is cible
    assert utterance_at(utterances, 10_000.0) is None


def test_transcription_vide():
    from autorush.transcription.base import Transcript

    assert build_utterances(Transcript()) == []
