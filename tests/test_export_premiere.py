"""Export Premiere Pro : le XML doit contenir de vraies keyframes.

Le cahier des charges est explicite : un zoom n'est pas fonctionnel parce qu'il
existe dans le code, il doit apparaitre dans les Options d'effet de Premiere.
Ces tests verifient la structure exacte que Premiere lit a l'import.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from autorush.export.fcp7xml import Fcp7Writer, _path_to_url, write_premiere_xml
from autorush.zoom.planner import ZOOM_DIRECT, ZOOM_LAUNCHED, ZOOM_PROGRESSIVE


@pytest.fixture
def xml_root(media_info, analysed, zoom_plan):
    writer = Fcp7Writer(media_info, analysed.timeline, zoom_plan)
    return ET.fromstring(writer.render()), writer


def scale_parameter(clipitem):
    for parameter in clipitem.findall("filter/effect/parameter"):
        if parameter.findtext("parameterid") == "scale":
            return parameter
    return None


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #
def test_le_xml_est_bien_forme_et_en_version_5(xml_root):
    root, _ = xml_root
    assert root.tag == "xmeml"
    assert root.get("version") == "5"
    assert root.find("sequence") is not None


def test_l_entete_doctype_est_present(media_info, analysed, zoom_plan):
    xml = Fcp7Writer(media_info, analysed.timeline, zoom_plan).render()
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<!DOCTYPE xmeml>" in xml


def test_la_sequence_porte_le_bon_format(xml_root, media_info):
    root, writer = xml_root
    characteristics = root.find("sequence/media/video/format/samplecharacteristics")
    assert characteristics.findtext("width") == str(media_info.width)
    assert characteristics.findtext("height") == str(media_info.height)
    assert characteristics.findtext("rate/timebase") == str(media_info.timebase)
    assert root.findtext("sequence/duration") == str(writer.total_frames)


def test_un_clip_par_plan(xml_root, analysed):
    root, _ = xml_root
    clips = root.findall("sequence/media/video/track/clipitem")
    assert len(clips) == len(analysed.timeline.shots)


def test_la_timeline_est_contigue_sans_trou(xml_root):
    """Un trou d'une image se verrait immediatement dans Premiere."""
    root, _ = xml_root
    cursor = 0
    for clip in root.findall("sequence/media/video/track/clipitem"):
        start = int(clip.findtext("start"))
        end = int(clip.findtext("end"))
        assert start == cursor, f"trou ou recouvrement a l'image {cursor}"
        assert end > start
        cursor = end
    assert cursor > 0


def test_les_points_dentree_correspondent_aux_plans(xml_root, analysed, media_info):
    root, _ = xml_root
    clips = root.findall("sequence/media/video/track/clipitem")
    for clip, shot in zip(clips, analysed.timeline.shots, strict=True):
        attendu = round(shot.source_start * media_info.fps)
        assert abs(int(clip.findtext("in")) - attendu) <= 1


def test_le_fichier_source_nest_declare_quune_fois(xml_root):
    """Premiere n'importe qu'un seul rush : un seul <file> complet suffit."""
    root, _ = xml_root
    complets = [
        clip
        for clip in root.findall("sequence/media/video/track/clipitem")
        if clip.find("file/pathurl") is not None
    ]
    assert len(complets) == 1
    assert complets[0].findtext("file/name")


def test_les_pistes_audio_suivent_le_nombre_de_canaux(xml_root, media_info, analysed):
    root, _ = xml_root
    tracks = root.findall("sequence/media/audio/track")
    assert len(tracks) == min(2, media_info.audio_channels)
    for track in tracks:
        assert len(track.findall("clipitem")) == len(analysed.timeline.shots)


def test_les_clips_audio_et_video_sont_lies(xml_root):
    root, _ = xml_root
    clip = root.find("sequence/media/video/track/clipitem")
    references = [link.findtext("linkclipref") for link in clip.findall("link")]
    assert any(reference.startswith("clipitem-v") for reference in references)
    assert any(reference.startswith("clipitem-a") for reference in references)


def test_des_transitions_audio_sont_posees_aux_coupes(xml_root, analysed):
    root, _ = xml_root
    track = root.find("sequence/media/audio/track")
    transitions = track.findall("transitionitem")
    assert transitions, "il faut un fondu audio aux points de coupe"
    assert len(transitions) <= analysed.timeline.cut_count
    for transition in transitions:
        assert transition.findtext("effect/mediatype") == "audio"
        assert transition.findtext("alignment") == "center"
        assert int(transition.findtext("end")) > int(transition.findtext("start"))


def test_sans_fondu_audio_aucune_transition(media_info, analysed, zoom_plan):
    writer = Fcp7Writer(
        media_info, analysed.timeline, zoom_plan, audio_crossfade=False
    )
    root = ET.fromstring(writer.render())
    track = root.find("sequence/media/audio/track")
    assert not track.findall("transitionitem")


# --------------------------------------------------------------------------- #
# Zooms : le point critique
# --------------------------------------------------------------------------- #
def test_le_filtre_est_bien_basic_motion(xml_root, zoom_plan):
    """C'est ce nom qui fait apparaitre l'animation dans Mouvement > Echelle."""
    root, _ = xml_root
    clips = root.findall("sequence/media/video/track/clipitem")
    avec_filtre = [clip for clip in clips if clip.find("filter") is not None]
    assert len(avec_filtre) == len(zoom_plan.events)
    for clip in avec_filtre:
        effect = clip.find("filter/effect")
        assert effect.findtext("name") == "Basic Motion"
        assert effect.findtext("effectid") == "basic"
        assert effect.findtext("effecttype") == "motion"
        assert effect.findtext("mediatype") == "video"


def test_les_zooms_animes_ont_des_keyframes(xml_root, zoom_plan):
    root, _ = xml_root
    clips = {
        index: clip
        for index, clip in enumerate(root.findall("sequence/media/video/track/clipitem"))
    }
    animes = [e for e in zoom_plan.events if e.is_animated]
    assert animes, "le rush de demonstration doit contenir des zooms animes"
    for event in animes:
        parameter = scale_parameter(clips[event.shot_index])
        keyframes = parameter.findall("keyframe")
        assert len(keyframes) >= 2, f"zoom {event.kind} sans animation"
        valeurs = [float(k.findtext("value")) for k in keyframes]
        assert valeurs[0] == pytest.approx(event.start_scale, abs=0.01)
        assert valeurs[-1] == pytest.approx(event.end_scale, abs=0.01)


def test_le_zoom_direct_est_une_valeur_fixe(media_info, analysed):
    from autorush.editing.timeline import Timeline
    from autorush.zoom.planner import ZoomEvent, ZoomPlan

    shot = analysed.timeline.shots[0]
    plan = ZoomPlan()
    plan.events.append(
        ZoomEvent(
            index=0, shot_index=shot.index, kind=ZOOM_DIRECT,
            timeline_start=shot.timeline_start, timeline_end=shot.timeline_end,
            source_start=shot.source_start, source_end=shot.source_end,
            start_scale=112.0, end_scale=112.0, animation_duration=0.0,
        )
    )
    assert isinstance(analysed.timeline, Timeline)
    root = ET.fromstring(Fcp7Writer(media_info, analysed.timeline, plan).render())
    clip = root.findall("sequence/media/video/track/clipitem")[shot.index]
    parameter = scale_parameter(clip)
    assert not parameter.findall("keyframe")
    assert float(parameter.findtext("value")) == pytest.approx(112.0)


def test_les_keyframes_restent_dans_la_plage_du_plan(xml_root):
    """Une keyframe hors de la plage source serait ignoree par Premiere."""
    root, _ = xml_root
    for clip in root.findall("sequence/media/video/track/clipitem"):
        entree = int(clip.findtext("in"))
        sortie = int(clip.findtext("out"))
        for parameter in clip.findall("filter/effect/parameter"):
            for keyframe in parameter.findall("keyframe"):
                when = int(keyframe.findtext("when"))
                assert entree <= when <= sortie, (when, entree, sortie)


def test_les_keyframes_sont_croissantes(xml_root):
    root, _ = xml_root
    for clip in root.findall("sequence/media/video/track/clipitem"):
        for parameter in clip.findall("filter/effect/parameter"):
            whens = [int(k.findtext("when")) for k in parameter.findall("keyframe")]
            assert whens == sorted(whens)


def test_base_de_temps_clip_commence_a_zero(media_info, analysed, zoom_plan):
    writer = Fcp7Writer(
        media_info, analysed.timeline, zoom_plan, keyframe_time_base="clip"
    )
    root = ET.fromstring(writer.render())
    trouve = False
    for clip in root.findall("sequence/media/video/track/clipitem"):
        parameter = scale_parameter(clip)
        if parameter is None:
            continue
        keyframes = parameter.findall("keyframe")
        if keyframes:
            assert int(keyframes[0].findtext("when")) == 0
            trouve = True
    assert trouve


def test_le_nombre_de_keyframes_reste_raisonnable(media_info, analysed, zoom_plan):
    writer = Fcp7Writer(media_info, analysed.timeline, zoom_plan, max_keyframes=12)
    root = ET.fromstring(writer.render())
    for clip in root.findall("sequence/media/video/track/clipitem"):
        parameter = scale_parameter(clip)
        if parameter is None:
            continue
        assert len(parameter.findall("keyframe")) <= 13


def test_un_zoom_progressif_couvre_tout_le_plan_dans_le_xml(media_info, analysed):
    from autorush.zoom.planner import ZoomEvent, ZoomPlan

    shot = max(analysed.timeline.shots, key=lambda s: s.duration)
    plan = ZoomPlan()
    plan.events.append(
        ZoomEvent(
            index=0, shot_index=shot.index, kind=ZOOM_PROGRESSIVE,
            timeline_start=shot.timeline_start, timeline_end=shot.timeline_end,
            source_start=shot.source_start, source_end=shot.source_end,
            start_scale=100.0, end_scale=112.0, animation_duration=shot.duration,
        )
    )
    root = ET.fromstring(Fcp7Writer(media_info, analysed.timeline, plan).render())
    clip = root.findall("sequence/media/video/track/clipitem")[shot.index]
    parameter = scale_parameter(clip)
    keyframes = parameter.findall("keyframe")
    premier = int(keyframes[0].findtext("when"))
    dernier = int(keyframes[-1].findtext("when"))
    assert premier == int(clip.findtext("in"))
    assert dernier == pytest.approx(int(clip.findtext("out")), abs=1)


def test_le_zoom_lance_depasse_puis_revient_dans_le_xml(media_info, analysed):
    from autorush.zoom.planner import ZoomEvent, ZoomPlan

    shot = max(analysed.timeline.shots, key=lambda s: s.duration)
    plan = ZoomPlan()
    plan.events.append(
        ZoomEvent(
            index=0, shot_index=shot.index, kind=ZOOM_LAUNCHED,
            timeline_start=shot.timeline_start, timeline_end=shot.timeline_end,
            source_start=shot.source_start, source_end=shot.source_end,
            start_scale=100.0, end_scale=112.0, animation_duration=1.05,
            overshoot=0.11, stiffness=5.6,
        )
    )
    root = ET.fromstring(Fcp7Writer(media_info, analysed.timeline, plan).render())
    parameter = scale_parameter(
        root.findall("sequence/media/video/track/clipitem")[shot.index]
    )
    valeurs = [float(k.findtext("value")) for k in parameter.findall("keyframe")]
    assert max(valeurs) > 112.5, "l'inertie doit depasser la cible"
    assert valeurs[-1] == pytest.approx(112.0, abs=0.01)


# --------------------------------------------------------------------------- #
# Chemins et echappement
# --------------------------------------------------------------------------- #
def test_url_de_fichier_windows():
    assert _path_to_url(Path("C:/Rushes/mon rush.mp4")) == (
        "file://localhost/C:/Rushes/mon%20rush.mp4"
    )


def test_url_de_fichier_avec_accents():
    url = _path_to_url(Path("D:/Videos/rush \u00e9t\u00e9.mov"))
    assert url.startswith("file://localhost/D:/Videos/rush%20")
    assert "%C3%A9" in url


def test_les_caracteres_speciaux_sont_echappes(analysed, zoom_plan, media_info):
    from dataclasses import replace

    media = replace(media_info, path=Path('C:/Rushes/rush <a> & "b".mp4'))
    xml = Fcp7Writer(media, analysed.timeline, zoom_plan).render()
    ET.fromstring(xml)  # ne doit pas lever
    assert "&amp;" in xml
    assert "&lt;a&gt;" in xml


def test_ecriture_sur_disque(tmp_path, media_info, analysed, zoom_plan):
    destination = tmp_path / "sequence.xml"
    written = write_premiere_xml(
        destination, media_info, analysed.timeline, zoom_plan,
        sequence_name="Test AutoRush",
    )
    assert written.exists()
    root = ET.fromstring(written.read_text(encoding="utf-8"))
    assert root.findtext("sequence/name") == "Test AutoRush"


def test_cadence_ntsc(analysed, zoom_plan, media_info):
    from dataclasses import replace

    media = replace(media_info, fps=29.97, timebase=30, ntsc=True)
    root = ET.fromstring(Fcp7Writer(media, analysed.timeline, zoom_plan).render())
    rate = root.find("sequence/rate")
    assert rate.findtext("timebase") == "30"
    assert rate.findtext("ntsc") == "TRUE"
    assert root.findtext("sequence/timecode/displayformat") == "DF"
