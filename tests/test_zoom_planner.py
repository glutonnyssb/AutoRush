"""Placement des zooms : densite, variete, anti-fatigue."""

from __future__ import annotations

from conftest import make_transcript

from autorush.analysis.decisions import analyze
from autorush.config import Settings
from autorush.editing.timeline import Shot, Timeline
from autorush.transcription.base import Word
from autorush.zoom.planner import (
    ZOOM_DIRECT,
    ZOOM_LAUNCHED,
    ZOOM_PROGRESSIVE,
    plan_zooms,
)

LONG_SCRIPT = [
    ("Bienvenue dans cette nouvelle video sur le tournoi.", 0.7),
    ("Aujourd'hui je vais vous raconter toute la competition.", 0.8),
    ("Mais avant ca il faut comprendre le contexte general.", 0.9),
    ("Le niveau etait absolument incroyable cette annee.", 0.8),
    ("Donc forcement les surprises ont ete nombreuses.", 0.7),
    ("Ensuite la phase finale a completement change la donne.", 0.9),
    ("Franchement personne n'avait predit ce resultat.", 0.8),
    ("Et pourtant les chiffres etaient clairs depuis le debut.", 0.7),
    ("Finalement cette edition restera dans les memoires.", 0.9),
    ("Merci d'avoir regarde jusqu'au bout de cette video.", 0.8),
]


def build(style="dynamique", intensity=None, seed=1234):
    transcript = make_transcript(LONG_SCRIPT)
    settings = Settings.for_style(style)
    if intensity is not None:
        settings.zoom.intensity = intensity
    settings.seed = seed
    result = analyze(transcript, settings, None, transcript.duration)
    return result, plan_zooms(result.timeline, settings.zoom, settings.seed), settings


#: quelques mots interessants pour que les plans synthetiques soient notables
FILLER_WORDS = ["Donc", "le", "niveau", "etait", "vraiment", "incroyable", "cette", "annee."]


def synthetic_timeline(count=14, duration=4.0, with_speech=True):
    """Timeline artificielle : des plans reguliers, avec de la parole dedans."""
    timeline = Timeline(source_duration=count * duration)
    cursor = 0.0
    word_index = 0
    for index in range(count):
        shot = Shot(
            index=index,
            source_start=cursor,
            source_end=cursor + duration,
            timeline_start=cursor,
            energy=0.7,
            onset=0.4,
            tags={"debut_phrase"},
        )
        if with_speech:
            step = duration / (len(FILLER_WORDS) + 1)
            for position, text in enumerate(FILLER_WORDS):
                shot.words.append(
                    Word(
                        text=text,
                        start=cursor + position * step,
                        end=cursor + (position + 0.8) * step,
                        index=word_index,
                    )
                )
                word_index += 1
        timeline.shots.append(shot)
        cursor += duration
    return timeline


# --------------------------------------------------------------------------- #
def test_le_mode_dynamique_bouge_plus_que_le_naturel():
    """Exigence du cahier des charges, section 10."""
    _, naturel, _ = build("naturel")
    _, dynamique, _ = build("dynamique")
    _, tres, _ = build("tres_dynamique")
    assert len(naturel.events) < len(tres.events)
    assert len(dynamique.events) >= len(naturel.events)


def test_intensite_zero_ne_place_presque_rien_et_cent_beaucoup():
    _, faible, _ = build(intensity=0.0)
    _, forte, _ = build(intensity=100.0)
    assert len(forte.events) > len(faible.events)


def test_intensite_pilote_l_amplitude():
    _, faible, _ = build(intensity=10.0)
    _, forte, _ = build(intensity=95.0)
    if faible.events and forte.events:
        moyenne_faible = sum(e.end_scale for e in faible.events) / len(faible.events)
        moyenne_forte = sum(e.end_scale for e in forte.events) / len(forte.events)
        assert moyenne_forte > moyenne_faible + 3.0


def test_aucun_zoom_ne_depasse_les_bornes():
    for style in ("naturel", "dynamique", "tres_dynamique"):
        _, plan, settings = build(style, intensity=100.0)
        for event in plan.events:
            assert event.start_scale >= settings.zoom.scale_floor
            assert event.end_scale <= settings.zoom.scale_ceiling
            assert event.end_scale >= event.start_scale or event.kind == ZOOM_DIRECT


def test_ecart_minimal_entre_deux_zooms_est_respecte():
    timeline = synthetic_timeline(count=20, duration=2.0)
    settings = Settings.for_style("dynamique")
    settings.zoom.intensity = 100.0
    plan = plan_zooms(timeline, settings.zoom, seed=7)
    times = sorted(event.timeline_start for event in plan.events)
    spacing = settings.zoom.min_spacing * 0.62  # valeur a intensite 100
    for previous, current in zip(times, times[1:], strict=False):
        assert current - previous >= spacing * 0.65 - 1e-6


def test_jamais_deux_fois_le_meme_type_daffilee():
    timeline = synthetic_timeline(count=24, duration=3.0)
    settings = Settings.for_style("tres_dynamique")
    settings.zoom.intensity = 100.0
    plan = plan_zooms(timeline, settings.zoom, seed=3)
    kinds = [event.kind for event in plan.events]
    assert len(kinds) >= 4
    for previous, current in zip(kinds, kinds[1:], strict=False):
        assert previous != current, kinds


def test_les_trois_types_sont_utilises():
    timeline = synthetic_timeline(count=40, duration=3.5)
    settings = Settings.for_style("tres_dynamique")
    settings.zoom.intensity = 90.0
    plan = plan_zooms(timeline, settings.zoom, seed=11)
    kinds = set(plan.count_by_kind())
    assert kinds == {ZOOM_DIRECT, ZOOM_PROGRESSIVE, ZOOM_LAUNCHED}


def test_un_plan_trop_court_ne_recoit_pas_de_zoom():
    timeline = synthetic_timeline(count=2, duration=3.0)
    court = timeline.shots[0]
    court.source_end = court.source_start + 0.30
    court.words = court.words[:1]
    court.words[0].end = court.source_end
    settings = Settings.for_style("tres_dynamique")
    plan = plan_zooms(timeline, settings.zoom, seed=5)
    assert 0 not in plan.by_shot()


def test_un_plan_muet_ne_recoit_pas_de_zoom():
    timeline = synthetic_timeline(count=6, with_speech=False)
    for shot in timeline.shots:
        shot.tags = set()
    settings = Settings.for_style("dynamique")
    plan = plan_zooms(timeline, settings.zoom, seed=2)
    # aucun plan n'a de mot : le planificateur doit s'abstenir
    assert not plan.events


def test_zoom_desactive():
    timeline = synthetic_timeline()
    settings = Settings.for_style("dynamique")
    settings.zoom.enabled = False
    assert not plan_zooms(timeline, settings.zoom, 1).events


def test_le_zoom_lance_dure_environ_une_seconde():
    _, plan, settings = build("tres_dynamique", intensity=90.0)
    lances = [e for e in plan.events if e.kind == ZOOM_LAUNCHED]
    assert lances
    for event in lances:
        assert 0.7 <= event.animation_duration <= 1.4, event.animation_duration


def test_le_zoom_progressif_couvre_tout_le_plan():
    _, plan, _ = build("dynamique", intensity=80.0)
    progressifs = [e for e in plan.events if e.kind == ZOOM_PROGRESSIVE]
    for event in progressifs:
        assert event.animation_duration == event.shot_duration


def test_le_zoom_direct_est_une_echelle_fixe():
    timeline = synthetic_timeline(count=30, duration=1.2)
    settings = Settings.for_style("tres_dynamique")
    settings.zoom.intensity = 95.0
    plan = plan_zooms(timeline, settings.zoom, seed=9)
    directs = [e for e in plan.events if e.kind == ZOOM_DIRECT]
    assert directs
    for event in directs:
        assert event.start_scale == event.end_scale
        assert event.keyframes(25.0) == []
        assert event.end_scale > 100.0


def test_reproductibilite_par_la_graine():
    _, premier, _ = build(seed=42)
    _, second, _ = build(seed=42)
    assert [e.as_dict() for e in premier.events] == [e.as_dict() for e in second.events]


def test_graines_differentes_donnent_de_la_variete():
    _, a, _ = build(seed=1)
    _, b, _ = build(seed=999)
    assert [e.kind for e in a.events] != [e.kind for e in b.events] or [
        round(e.end_scale, 2) for e in a.events
    ] != [round(e.end_scale, 2) for e in b.events]


def test_un_long_passage_statique_est_casse():
    timeline = synthetic_timeline(count=12, duration=6.0)  # 72 s
    settings = Settings.for_style("naturel")
    settings.zoom.max_static_duration = 14.0
    plan = plan_zooms(timeline, settings.zoom, seed=4)
    times = [0.0] + sorted(e.timeline_start for e in plan.events) + [timeline.duration]
    plus_grand = max(b - a for a, b in zip(times, times[1:], strict=False))
    assert plus_grand <= settings.zoom.max_static_duration + 6.5


def test_les_keyframes_dun_zoom_lance_incluent_un_maintien():
    timeline = synthetic_timeline(count=4, duration=5.0)
    settings = Settings.for_style("dynamique")
    settings.zoom.intensity = 70.0
    plan = plan_zooms(timeline, settings.zoom, seed=6)
    for event in plan.events:
        if event.kind != ZOOM_LAUNCHED:
            continue
        keyframes = event.keyframes(25.0)
        assert keyframes[-1][0] > event.animation_duration
        assert keyframes[-1][1] == event.end_scale
