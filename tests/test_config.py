"""Reglages et presets de style."""

from __future__ import annotations

import pytest

from autorush.config import (
    STYLE_PRESETS,
    Settings,
    apply_style,
    resolve_style,
    style_label,
)
from autorush.errors import AutoRushError


@pytest.mark.parametrize(
    "entree,attendu",
    [
        ("naturel", "naturel"),
        ("Naturel", "naturel"),
        ("dynamique", "dynamique"),
        ("Tres dynamique", "tres_dynamique"),
        ("tres-dynamique", "tres_dynamique"),
        ("very-dynamic", "tres_dynamique"),
        ("max", "tres_dynamique"),
    ],
)
def test_resolution_des_styles(entree, attendu):
    assert resolve_style(entree) == attendu


def test_style_inconnu_leve_une_erreur_lisible():
    with pytest.raises(AutoRushError) as info:
        resolve_style("turbo")
    assert "naturel" in info.value.hint


def test_les_trois_styles_existent():
    assert set(STYLE_PRESETS) == {"naturel", "dynamique", "tres_dynamique"}


def test_les_presets_serrent_progressivement_le_rythme():
    naturel = Settings.for_style("naturel").silence
    dynamique = Settings.for_style("dynamique").silence
    tres = Settings.for_style("tres_dynamique").silence
    assert naturel.keep_below > dynamique.keep_below > tres.keep_below
    assert naturel.target_gap > dynamique.target_gap > tres.target_gap
    assert (
        naturel.long_pause_threshold
        > dynamique.long_pause_threshold
        > tres.long_pause_threshold
    )


def test_les_presets_augmentent_progressivement_les_zooms():
    naturel = Settings.for_style("naturel").zoom
    dynamique = Settings.for_style("dynamique").zoom
    tres = Settings.for_style("tres_dynamique").zoom
    assert naturel.intensity < dynamique.intensity < tres.intensity
    assert naturel.min_spacing > dynamique.min_spacing > tres.min_spacing
    assert naturel.max_per_minute < dynamique.max_per_minute < tres.max_per_minute


def test_apply_style_ne_modifie_pas_loriginal():
    base = Settings()
    valeur = base.silence.keep_below
    autre = apply_style(base, "tres_dynamique")
    assert base.silence.keep_below == valeur
    assert autre.silence.keep_below != valeur


def test_aller_retour_json(tmp_path):
    settings = Settings.for_style("tres_dynamique")
    settings.zoom.intensity = 77.0
    settings.transcription.model = "medium"
    path = settings.save(tmp_path / "reglages.json")
    recharge = Settings.load(path)
    assert recharge.style == "tres_dynamique"
    assert recharge.zoom.intensity == 77.0
    assert recharge.transcription.model == "medium"
    assert recharge.transcription.temperature == settings.transcription.temperature
    assert recharge.to_dict() == settings.to_dict()


def test_from_dict_tolere_les_champs_inconnus():
    data = Settings().to_dict()
    data["zoom"]["champ_inexistant"] = 42
    data["section_inconnue"] = {"x": 1}
    settings = Settings.from_dict(data)
    assert settings.zoom.intensity == Settings().zoom.intensity


def test_style_label():
    assert style_label("tres_dynamique") == "Tres dynamique"
    assert style_label("naturel") == "Naturel"


def test_les_seuils_de_confiance_restent_prudents():
    """Aucun style ne doit descendre sous une confiance raisonnable."""
    for style in ("naturel", "dynamique", "tres_dynamique"):
        settings = Settings.for_style(style)
        assert settings.retake.min_delete_confidence >= 0.50
        assert settings.fragment.min_delete_confidence >= 0.50
        assert settings.disfluency.min_delete_confidence >= 0.50
        assert settings.retake.similarity_without_marker > settings.retake.similarity_with_marker


def test_les_zooms_ne_reculent_jamais():
    for style in ("naturel", "dynamique", "tres_dynamique"):
        zoom = Settings.for_style(style).zoom
        assert zoom.scale_floor >= 100.0, "un zoom arriere montrerait les bords"
        assert zoom.scale_ceiling <= 200.0
