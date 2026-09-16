"""Timeline, plans et arrondi a l'image."""

from __future__ import annotations

import pytest
from conftest import make_transcript

from autorush.analysis.silences import plan_silences
from autorush.config import Settings
from autorush.editing.frames import frame_clips, total_frames
from autorush.editing.timeline import build_timeline

LINES = [
    ("Premiere phrase du rush.", 0.8),
    ("Deuxieme phrase apres une pause.", 1.8),
    ("Troisieme phrase pour finir.", 1.2),
]


def build(style="dynamique", removed=None):
    transcript = make_transcript(LINES)
    settings = Settings.for_style(style)
    gaps = plan_silences(
        transcript, removed or set(), settings.silence, None, transcript.duration
    )
    intervals = [g.removal for g in gaps if g.removal]
    timeline = build_timeline(
        transcript, intervals, settings.silence, transcript.duration
    )
    return transcript, settings, timeline


def test_les_plans_sont_ordonnes_et_contigus_dans_le_montage():
    _, _, timeline = build()
    cursor = 0.0
    for shot in timeline.shots:
        assert shot.timeline_start == pytest.approx(cursor)
        cursor = shot.timeline_end
    assert timeline.duration == pytest.approx(cursor)


def test_aucun_plan_plus_court_que_le_minimum():
    for style in ("naturel", "dynamique", "tres_dynamique"):
        _, settings, timeline = build(style)
        for shot in timeline.shots:
            assert shot.duration >= settings.silence.min_shot_duration - 1e-6


def test_le_montage_est_plus_court_que_la_source():
    transcript, _, timeline = build()
    assert timeline.duration < timeline.source_duration
    assert timeline.removed_duration() > 0


def test_chaque_mot_conserve_appartient_a_un_plan():
    transcript, _, timeline = build()
    attribues = sum(len(shot.words) for shot in timeline.shots)
    assert attribues == transcript.word_count()


def test_conversion_source_vers_montage():
    transcript, _, timeline = build()
    premier = timeline.shots[0]
    milieu = 0.5 * (premier.source_start + premier.source_end)
    converti = timeline.source_to_timeline(milieu)
    assert converti is not None
    retour = timeline.timeline_to_source(converti)
    assert retour == pytest.approx(milieu)


def test_un_instant_coupe_na_pas_dequivalent():
    transcript, _, timeline = build()
    if len(timeline.shots) < 2:
        pytest.skip("il faut au moins deux plans")
    trou = 0.5 * (timeline.shots[0].source_end + timeline.shots[1].source_start)
    assert timeline.source_to_timeline(trou) is None


def test_etiquettes_de_plan():
    _, _, timeline = build()
    assert "debut_phrase" in timeline.shots[0].tags


# --------------------------------------------------------------------------- #
# Arrondi a l'image
# --------------------------------------------------------------------------- #
def test_les_plans_arrondis_ne_laissent_aucun_trou():
    _, _, timeline = build()
    clips = frame_clips(timeline, 25.0, int(timeline.source_duration * 25))
    cursor = 0
    for clip in clips:
        assert clip.start_frame == cursor
        assert clip.length >= 1
        cursor = clip.end_frame
    assert total_frames(clips) == cursor


def test_la_duree_arrondie_colle_a_la_duree_reelle():
    _, _, timeline = build()
    for fps in (24.0, 25.0, 30.0, 50.0):
        clips = frame_clips(timeline, fps, int(timeline.source_duration * fps))
        duree = total_frames(clips) / fps
        assert duree == pytest.approx(timeline.duration, abs=len(clips) / fps)


def test_les_bornes_arrondies_restent_dans_le_media():
    _, _, timeline = build()
    limite = 100
    clips = frame_clips(timeline, 25.0, limite)
    for clip in clips:
        assert 0 <= clip.in_frame < clip.out_frame <= limite


def test_fps_invalide_leve_une_erreur():
    _, _, timeline = build()
    with pytest.raises(ValueError):
        frame_clips(timeline, 0.0)


def test_le_rapport_nannonce_jamais_une_coupe_non_appliquee():
    """Un mot annonce supprime doit etre reellement hors des plans.

    La duree minimale d'un plan peut etendre un plan trop court sur la zone
    voisine et y ramener de la parole censee partir. Le mot resterait alors
    dans l'image et le son tout en etant absent du rapport : le montage
    decrit ne serait pas le montage produit.
    """
    from conftest import make_transcript

    from autorush.analysis.decisions import analyze
    from autorush.config import Settings

    ouverture = "Aujourd'hui quand on parle de Smash Ultimate il y a un point"
    transcript = make_transcript(
        [
            (f"{ouverture}.", 0.5),
            (f"{ouverture} sur lequel tout le monde est d'accord.", 0.4),
        ]
    )
    # une duree de plan absurde force la coupe a etre recouverte
    settings = Settings.for_style("dynamique")
    settings.silence.min_shot_duration = 12.0
    result = analyze(transcript, settings, None, transcript.duration)

    mots = {word.index: word for word in result.transcript.words}
    for index in result.removed_word_indices:
        word = mots[index]
        centre = 0.5 * (word.start + word.end)
        dans_un_plan = any(
            shot.source_start <= centre <= shot.source_end
            for shot in result.timeline.shots
        )
        assert not dans_un_plan, (
            f"{word.clean!r} est annonce supprime mais reste dans le montage"
        )
    assert [f for f in result.flags if f.category == "coupe_non_appliquee"], (
        "la coupe recouverte doit etre signalee"
    )
