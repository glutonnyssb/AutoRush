"""Rapports, EDL et JSON de montage (cahier des charges, sections 13 et 14)."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from autorush.export.edl import render_edl, write_edl
from autorush.export.json_edl import build_payload, load_json_edl, write_json_edl
from autorush.export.report import (
    render_html,
    render_markdown,
    write_html_report,
    write_markdown_report,
)


class _Balanced(HTMLParser):
    """Verifie grossierement que les balises sont equilibrees."""

    VOID = {"br", "img", "meta", "hr", "input", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"</{tag}> sans ouverture")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> alors que <{self.stack[-1]}> est ouvert")
        else:
            self.stack.pop()


# --------------------------------------------------------------------------- #
# Rapport HTML
# --------------------------------------------------------------------------- #
def test_le_html_est_equilibre(media_info, settings, analysed, zoom_plan):
    html = render_html(media_info, settings, analysed, zoom_plan)
    parser = _Balanced()
    parser.feed(html)
    assert not parser.errors, parser.errors
    assert not parser.stack, parser.stack


def test_le_rapport_contient_toutes_les_sections_demandees(
    media_info, settings, analysed, zoom_plan
):
    html = render_html(media_info, settings, analysed, zoom_plan)
    for titre in (
        "Resume",
        "Zooms places",
        "Reprises de phrases",
        "Hesitations et bafouillages supprimes",
        "Tout ce qui a ete supprime",
        "Decisions incertaines",
        "Raccords suspects",
        "Traitement des blancs",
        "Texte du montage final",
    ):
        assert titre in html, titre


def test_le_rapport_liste_les_zooms_avec_timecode_et_type(
    media_info, settings, analysed, zoom_plan
):
    """Le cahier des charges demande « 00:27 - Zoom lance »."""
    html = render_html(media_info, settings, analysed, zoom_plan)
    assert zoom_plan.events
    for event in zoom_plan.events:
        from autorush.utils import format_timecode

        assert format_timecode(event.timeline_start) in html
        assert event.label in html


def test_le_rapport_cite_les_reprises_detectees(
    media_info, settings, analysed, zoom_plan
):
    html = render_html(media_info, settings, analysed, zoom_plan)
    assert analysed.retake_groups
    for group in analysed.retake_groups:
        assert group.kept_text[:40] in html


def test_le_rapport_est_autonome(media_info, settings, analysed, zoom_plan):
    """Aucune ressource externe : le fichier doit s'ouvrir hors ligne."""
    html = render_html(media_info, settings, analysed, zoom_plan)
    assert "<style>" in html
    assert not re.search(r'src\s*=\s*"http', html)
    assert not re.search(r'href\s*=\s*"http', html)


def test_le_rapport_gere_le_theme_sombre(media_info, settings, analysed, zoom_plan):
    html = render_html(media_info, settings, analysed, zoom_plan)
    assert "prefers-color-scheme" in html


def test_ecriture_du_rapport_html(tmp_path, media_info, settings, analysed, zoom_plan):
    path = write_html_report(
        tmp_path / "rapport.html", media_info, settings, analysed, zoom_plan
    )
    assert path.exists() and path.stat().st_size > 2000


# --------------------------------------------------------------------------- #
# Rapport Markdown
# --------------------------------------------------------------------------- #
def test_le_markdown_contient_le_resume_et_les_zooms(
    media_info, settings, analysed, zoom_plan
):
    text = render_markdown(media_info, settings, analysed, zoom_plan)
    assert text.startswith("# Rapport de montage")
    assert "## Zooms places" in text
    assert "## Reprises de phrases" in text
    assert "## Raccords suspects" in text


def test_ecriture_du_rapport_markdown(tmp_path, media_info, settings, analysed, zoom_plan):
    path = write_markdown_report(
        tmp_path / "rapport.md", media_info, settings, analysed, zoom_plan
    )
    assert path.exists()
    assert "Texte du montage final" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# EDL
# --------------------------------------------------------------------------- #
def test_l_edl_a_une_ligne_par_plan(analysed, media_info):
    edl = render_edl(analysed.timeline, "Test", fps=media_info.fps)
    lignes = [ligne for ligne in edl.splitlines() if re.match(r"^\d{3}\s", ligne)]
    assert len(lignes) == len(analysed.timeline.shots)


def test_l_edl_contient_des_timecodes_smpte(analysed, media_info):
    edl = render_edl(analysed.timeline, "Test", fps=media_info.fps)
    assert "TITLE: Test" in edl
    assert "NON-DROP FRAME" in edl
    assert re.search(r"\d{2}:\d{2}:\d{2}:\d{2}", edl)


def test_les_enregistrements_de_ledl_se_suivent(analysed, media_info):
    edl = render_edl(analysed.timeline, "Test", fps=media_info.fps)
    records = re.findall(r"(\d{2}:\d{2}:\d{2}:\d{2}) (\d{2}:\d{2}:\d{2}:\d{2})$", edl, re.M)
    assert records
    precedent = None
    for entree, sortie in records:
        if precedent is not None:
            assert entree == precedent
        precedent = sortie


def test_ecriture_de_ledl(tmp_path, analysed, media_info):
    path = write_edl(
        tmp_path / "montage.edl", analysed.timeline, "Mon rush", fps=media_info.fps,
        source_name=media_info.path.name,
    )
    assert path.exists()
    assert "FROM CLIP NAME" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# JSON de montage
# --------------------------------------------------------------------------- #
def test_le_json_contient_tout_le_montage(media_info, settings, analysed, zoom_plan):
    payload = build_payload(
        media_info, settings, analysed, zoom_plan, analysed.seams
    )
    assert payload["format"] == "autorush-edit"
    assert payload["media"]["path"]
    assert payload["settings"]["style"] == settings.style
    assert len(payload["analysis"]["timeline"]["shots"]) == len(analysed.timeline.shots)
    assert payload["zooms"]["count"] == len(zoom_plan.events)
    assert "removals" in payload["analysis"] and "flags" in payload["analysis"]


def test_le_json_est_relisible(tmp_path, media_info, settings, analysed, zoom_plan):
    path = write_json_edl(
        tmp_path / "montage.json", media_info, settings, analysed, zoom_plan,
        analysed.seams,
    )
    rechargé = load_json_edl(path)
    assert rechargé["version"]
    assert rechargé["analysis"]["stats"]["shots"] == len(analysed.timeline.shots)


def test_le_json_est_serialisable_sans_perte(media_info, settings, analysed, zoom_plan):
    payload = build_payload(media_info, settings, analysed, zoom_plan, analysed.seams)
    texte = json.dumps(payload, ensure_ascii=False)
    assert json.loads(texte) == payload


def test_les_reglages_du_json_permettent_de_rejouer(media_info, settings, analysed):
    from autorush.config import Settings

    payload = build_payload(media_info, settings, analysed, None, None)
    rejoue = Settings.from_dict(payload["settings"])
    assert rejoue.style == settings.style
    assert rejoue.zoom.intensity == settings.zoom.intensity
    assert rejoue.silence.keep_below == settings.silence.keep_below
