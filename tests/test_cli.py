"""Ligne de commande."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autorush.cli import build_parser, main, normalize_argv, settings_from_args


def parse(arguments):
    return build_parser().parse_args(normalize_argv(arguments))


# --------------------------------------------------------------------------- #
# Commande par defaut
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "arguments,attendu",
    [
        (["rush.mp4"], ["process", "rush.mp4"]),
        (["-s", "naturel", "rush.mp4"], ["process", "-s", "naturel", "rush.mp4"]),
        (["--preview", "rush.mp4"], ["process", "--preview", "rush.mp4"]),
        (["process", "rush.mp4"], ["process", "rush.mp4"]),
        (["doctor"], ["doctor"]),
        (["demo", "--out", "x"], ["demo", "--out", "x"]),
        (["--version"], ["--version"]),
    ],
)
def test_la_commande_process_est_implicite(arguments, attendu):
    assert normalize_argv(arguments) == attendu


def test_on_peut_donner_juste_un_fichier():
    args = parse(["mon rush.mp4"])
    assert args.command == "process"
    assert args.input == Path("mon rush.mp4")
    assert args.style == "dynamique"


# --------------------------------------------------------------------------- #
# Traduction des options en reglages
# --------------------------------------------------------------------------- #
def test_le_style_applique_le_preset():
    settings = settings_from_args(parse(["r.mp4", "--style", "tres_dynamique"]))
    assert settings.style == "tres_dynamique"
    assert settings.silence.keep_below == pytest.approx(0.19)


def test_lintensite_de_zoom_surcharge_le_preset():
    settings = settings_from_args(parse(["r.mp4", "-s", "naturel", "-z", "90"]))
    assert settings.style == "naturel"
    assert settings.zoom.intensity == 90.0


def test_lintensite_est_bornee():
    assert settings_from_args(parse(["r.mp4", "-z", "500"])).zoom.intensity == 100.0
    assert settings_from_args(parse(["r.mp4", "-z", "-20"])).zoom.intensity == 0.0


def test_desactivation_des_zooms():
    assert not settings_from_args(parse(["r.mp4", "--no-zoom"])).zoom.enabled


def test_mode_silences_seulement():
    settings = settings_from_args(parse(["r.mp4", "--silences-only"]))
    assert settings.silences_only


def test_mode_ne_rien_supprimer():
    assert settings_from_args(parse(["r.mp4", "--keep-all"])).dry_run_decisions


def test_desactivation_ciblee():
    settings = settings_from_args(
        parse(["r.mp4", "--no-retakes", "--no-fragments", "--no-disfluency"])
    )
    assert not settings.retake.enabled
    assert not settings.fragment.enabled
    assert not settings.disfluency.remove_fillers
    assert not settings.disfluency.remove_stutters


def test_seuil_de_confiance_global():
    settings = settings_from_args(parse(["r.mp4", "--min-confidence", "0.9"]))
    assert settings.retake.min_delete_confidence == 0.9
    assert settings.fragment.min_delete_confidence == 0.9
    assert settings.disfluency.min_delete_confidence == 0.9


def test_options_de_transcription():
    settings = settings_from_args(
        parse(["r.mp4", "--lang", "multi", "--model", "medium", "--device", "cpu", "--no-cache"])
    )
    assert settings.transcription.language == "multi"
    assert settings.transcription.model == "medium"
    assert settings.transcription.device == "cpu"
    assert not settings.transcription.use_cache


def test_options_de_sortie():
    settings = settings_from_args(
        parse(["r.mp4", "--no-xml", "--no-edl", "--no-report", "--no-crossfade"])
    )
    assert not settings.export.write_premiere_xml
    assert not settings.export.write_edl
    assert not settings.export.write_report_html
    assert not settings.export.write_report_markdown
    assert not settings.export.audio_crossfade


def test_base_de_temps_des_keyframes():
    settings = settings_from_args(parse(["r.mp4", "--keyframe-time-base", "clip"]))
    assert settings.export.keyframe_time_base == "clip"


def test_chargement_de_reglages(tmp_path):
    from autorush.config import Settings

    source = Settings.for_style("naturel")
    source.zoom.focus_y = 0.42
    path = source.save(tmp_path / "reglages.json")
    settings = settings_from_args(parse(["r.mp4", "--settings", str(path)]))
    assert settings.zoom.focus_y == pytest.approx(0.42)


def test_enregistrement_des_reglages_sans_traitement(tmp_path):
    """``--save-settings`` seul produit un fichier a editer."""
    cible = tmp_path / "sortie.json"
    code = main(["process", "--save-settings", str(cible), "--style", "naturel", "-q"])
    assert code == 0
    assert cible.exists()
    data = json.loads(cible.read_text(encoding="utf-8"))
    assert data["style"] == "naturel"
    assert data["silence"]["keep_below"] == 0.42


# --------------------------------------------------------------------------- #
# Commandes annexes
# --------------------------------------------------------------------------- #
def test_aide_sans_argument(capsys):
    assert main([]) == 1
    assert "AutoRush" in capsys.readouterr().out


def test_commande_demo(tmp_path, capsys):
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    sortie = capsys.readouterr().out
    assert "demonstration" in sortie.lower()
    produits = list((tmp_path / "demo").glob("*"))
    noms = {p.name for p in produits}
    assert "demo_premiere.xml" in noms
    assert "demo_rapport.html" in noms
    assert "demo_montage.json" in noms


def test_commande_demo_respecte_le_style(tmp_path):
    assert main(["demo", "--out", str(tmp_path / "d1"), "--style", "naturel"]) == 0
    assert main(["demo", "--out", str(tmp_path / "d2"), "--style", "tres_dynamique"]) == 0
    import xml.etree.ElementTree as ET

    def plans(dossier):
        root = ET.parse(dossier / "demo_premiere.xml").getroot()
        return len(root.findall("sequence/media/video/track/clipitem"))

    assert plans(tmp_path / "d1") <= plans(tmp_path / "d2")


def test_commande_doctor(capsys):
    code = main(["doctor"])
    sortie = capsys.readouterr().out
    assert "verification de l'installation" in sortie
    assert code in (0, 1)


def test_commande_cache(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTORUSH_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["cache"]) == 0
    assert "Dossier" in capsys.readouterr().out


def test_fichier_introuvable(capsys):
    code = main(["process", "/introuvable/rush.mp4", "-q"])
    assert code == 2
