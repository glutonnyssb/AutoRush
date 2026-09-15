"""Rapport de montage lisible (HTML et Markdown).

Le rapport repond a deux questions :

* **qu'est-ce qui a ete enleve, et pourquoi ?**
* **ou faut-il que je verifie a la main ?**

Chaque suppression est datee, citee et justifiee. Chaque doute est liste a part,
avec son niveau de confiance, de facon a pouvoir etre verifie en quelques
minutes dans Premiere.
"""

from __future__ import annotations

import html
from pathlib import Path

from autorush.analysis.decisions import AnalysisResult
from autorush.config import Settings, style_label
from autorush.logging_setup import get_logger
from autorush.media.ffmpeg import MediaInfo
from autorush.utils import format_timecode, human_duration
from autorush.version import APP_NAME, __version__
from autorush.zoom.planner import ZoomPlan

log = get_logger("export.report")

FLAG_LABELS: dict[str, str] = {
    "reprise_possible": "Reprise possible (conservee)",
    "fragment_douteux": "Fragment douteux (conserve)",
    "hesitation_douteuse": "Hesitation incertaine (conservee)",
    "limite_securite": "Annule par le filet de securite",
}

SEAM_LABELS: dict[str, str] = {
    "correction_conservee": "Correction orale restee dans le montage",
    "liaison_orpheline": "Phrase coupee sur un mot de liaison",
    "reprise_bancale": "Le plan suivant demarre mal",
    "repetition_raccord": "Mot repete de part et d'autre de la coupe",
    "coupes_rapprochees": "Deux coupes tres rapprochees",
    "plan_court": "Plan tres court",
    "reste_vide": "Enonce vide de sens apres coupe",
}

SEVERITY_ORDER = {"haute": 0, "moyenne": 1, "basse": 2}


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def _pct(value: float) -> str:
    return f"{value * 100:.1f} %"


# --------------------------------------------------------------------------- #
# Contenu commun
# --------------------------------------------------------------------------- #
def _summary_rows(
    media: MediaInfo, settings: Settings, analysis: AnalysisResult, zooms: ZoomPlan | None
) -> list[tuple[str, str]]:
    stats = analysis.stats
    timeline = analysis.timeline
    source = stats.get("source_duration", 0.0)
    final = stats.get("final_duration", 0.0)
    gained = max(0.0, source - final)
    rows = [
        ("Rush", media.path.name),
        ("Style de montage", style_label(settings.style)),
        ("Duree d'origine", human_duration(source)),
        ("Duree montee", human_duration(final)),
        ("Temps gagne", f"{human_duration(gained)}  ({_pct(gained / source if source else 0)})"),
        ("Plans", str(stats.get("shots", 0))),
        ("Coupes", str(stats.get("cuts", 0))),
        (
            "Silences retires",
            human_duration(stats.get("removed_silence_duration", 0.0)),
        ),
        (
            "Hesitations retirees",
            f"{stats.get('counts', {}).get('hesitations', 0)} "
            f"({human_duration(stats.get('removed_disfluency_duration', 0.0))})",
        ),
        (
            "Mauvaises prises retirees",
            f"{stats.get('counts', {}).get('reprises', 0)} "
            f"({human_duration(stats.get('removed_speech_duration', 0.0))})",
        ),
        ("Corrections orales retirees", str(stats.get("counts", {}).get("corrections", 0))),
        ("Fragments retires", str(stats.get("counts", {}).get("fragments", 0))),
        ("Points a verifier", str(len(analysis.flags) + len(analysis.seams))),
    ]
    if zooms is not None:
        counts = zooms.count_by_kind()
        rows.append(
            (
                "Zooms",
                f"{len(zooms.events)} "
                f"({counts.get('direct', 0)} directs, "
                f"{counts.get('progressif', 0)} progressifs, "
                f"{counts.get('lance', 0)} lances) "
                f"- {zooms.per_minute(timeline.duration):.1f}/min",
            )
        )
    return rows


def _final_text(analysis: AnalysisResult) -> str:
    removed = analysis.removed_word_indices
    parts: list[str] = []
    for shot in analysis.timeline.shots:
        kept = [w.clean for w in shot.words if w.index not in removed]
        if kept:
            parts.append(" ".join(kept))
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
_CSS = """
:root{--bg:#f7f7f5;--fg:#1b1b1a;--muted:#6b6b66;--line:#e2e2dd;--card:#ffffff;
--accent:#b4531f;--ok:#2f6f4f;--warn:#9a6a10;--bad:#a33a2a;--chip:#f0efea}
@media (prefers-color-scheme:dark){:root{--bg:#181816;--fg:#eceae4;--muted:#a3a19a;
--line:#2f2f2b;--card:#201f1d;--accent:#e0854b;--ok:#6fbf92;--warn:#d8a83f;
--bad:#e0806e;--chip:#2a2926}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 72px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.2px}
h2{font-size:18px;margin:34px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:20px 0 8px}
.sub{color:var(--muted);font-size:13px;margin:0 0 22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:11px}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.055em}
.card .v{font-size:17px;font-weight:600;margin-top:3px;word-break:break-word}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;
letter-spacing:.045em;white-space:nowrap}
td.tc{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--chip);
font-size:11.5px;white-space:nowrap}
.sev-haute{color:var(--bad);font-weight:600}
.sev-moyenne{color:var(--warn);font-weight:600}
.sev-basse{color:var(--muted)}
.del{text-decoration:line-through;color:var(--muted)}
.keep{color:var(--ok)}
.q{font-style:italic}
.bar{display:flex;height:11px;border-radius:6px;overflow:hidden;
border:1px solid var(--line);margin:10px 0 4px}
.bar span{display:block}
.legend{display:flex;flex-wrap:wrap;gap:13px;font-size:12px;color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.final{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:15px 17px;line-height:1.75}
.empty{color:var(--muted);font-style:italic}
footer{margin-top:44px;color:var(--muted);font-size:12px;
border-top:1px solid var(--line);padding-top:14px}
"""


def _html_table(headers: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="empty">{_esc(empty)}</p>'
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_html(
    media: MediaInfo,
    settings: Settings,
    analysis: AnalysisResult,
    zooms: ZoomPlan | None = None,
) -> str:
    """Construit le rapport HTML complet (autonome, sans ressource externe)."""
    stats = analysis.stats
    source = max(1e-6, stats.get("source_duration", 0.0))

    # -- bandeau de repartition --------------------------------------- #
    silence = stats.get("removed_silence_duration", 0.0)
    disfluency = stats.get("removed_disfluency_duration", 0.0)
    speech = stats.get("removed_speech_duration", 0.0)
    kept = max(0.0, source - silence - disfluency - speech)
    segments = [
        ("var(--ok)", kept, "Conserve"),
        ("var(--muted)", silence, "Silences"),
        ("var(--warn)", disfluency, "Hesitations"),
        ("var(--bad)", speech, "Mauvaises prises"),
    ]
    bar = "".join(
        f'<span style="width:{100 * value / source:.3f}%;background:{color}"></span>'
        for color, value, _ in segments
        if value > 0
    )
    legend = "".join(
        f'<span><i style="background:{color}"></i>{_esc(label)} '
        f"{human_duration(value)}</span>"
        for color, value, label in segments
        if value > 0
    )

    cards = "".join(
        f'<div class="card"><div class="k">{_esc(k)}</div>'
        f'<div class="v">{_esc(v)}</div></div>'
        for k, v in _summary_rows(media, settings, analysis, zooms)
    )

    # -- zooms --------------------------------------------------------- #
    zoom_rows: list[list[str]] = []
    if zooms is not None:
        for event in zooms.events:
            amplitude = (
                f"{event.start_scale:.0f} % → {event.end_scale:.0f} %"
                if event.is_animated
                else f"{event.end_scale:.0f} % (fixe)"
            )
            if event.kind == "lance":
                amplitude += f" · pic {event.peak_scale:.0f} %"
            zoom_rows.append(
                [
                    f'<td class="tc">{_esc(format_timecode(event.timeline_start))}</td>',
                    f'<td><span class="chip">{_esc(event.label)}</span></td>',
                    f"<td>{_esc(amplitude)}</td>",
                    f'<td class="num">{event.animation_duration:.2f} s</td>',
                    f"<td>{_esc(event.reason)}</td>",
                ]
            )
    zoom_table = _html_table(
        ["Timecode", "Type", "Amplitude", "Duree", "Pourquoi ici"],
        zoom_rows,
        "Aucun zoom place.",
    )

    # -- reprises ------------------------------------------------------ #
    retake_blocks: list[str] = []
    for group in analysis.retake_groups:
        rows: list[list[str]] = []
        for attempt in group.attempts:
            applied = any(
                r.applied
                and r.utterance_index == attempt.utterance_index
                and r.source in {"retake", "marker"}
                for r in analysis.removals
            )
            status = (
                '<span class="del">supprime</span>'
                if applied
                else '<span class="keep">conserve (doute)</span>'
            )
            rows.append(
                [
                    f'<td class="tc">{_esc(format_timecode(attempt.start))}</td>',
                    f'<td><span class="chip">'
                    f'{"correction" if attempt.role == "marker" else "tentative"}'
                    f"</span></td>",
                    f'<td class="q">{_esc(attempt.text)}</td>',
                    f'<td class="num">{attempt.confidence:.2f}</td>',
                    f"<td>{status}</td>",
                ]
            )
        table = _html_table(
            ["Timecode", "Role", "Texte", "Confiance", "Decision"], rows, "-"
        )
        retake_blocks.append(
            f"<h3>Reprise {group.index + 1} "
            f'<span class="chip">{_esc(group.anchor)}</span></h3>'
            f'<p class="keep">Version conservee · '
            f"{_esc(format_timecode(group.kept_start))} · "
            f'<span class="q">{_esc(group.kept_text)}</span></p>{table}'
        )
    retakes_html = (
        "".join(retake_blocks)
        or '<p class="empty">Aucune reprise de phrase detectee.</p>'
    )

    # -- hesitations --------------------------------------------------- #
    disfluency_rows = [
        [
            f'<td class="tc">{_esc(format_timecode(r.start))}</td>',
            f'<td><span class="chip">{_esc(r.label)}</span></td>',
            f'<td class="del q">{_esc(r.text)}</td>',
            f'<td class="keep q">{_esc(r.kept_instead or "—")}</td>',
            f"<td>{_esc(r.reason)}</td>",
        ]
        for r in analysis.removals
        if r.applied and r.source in {"filler", "filler_phrase", "stutter", "abandoned"}
    ]
    disfluency_table = _html_table(
        ["Timecode", "Type", "Retire", "Conserve", "Detail"],
        disfluency_rows,
        "Aucune hesitation supprimee.",
    )

    # -- tout ce qui a ete supprime ------------------------------------ #
    removal_rows = [
        [
            f'<td class="tc">{_esc(format_timecode(r.start))}</td>',
            f'<td><span class="chip">{_esc(r.label)}</span></td>',
            f'<td class="num">{r.duration:.2f} s</td>',
            f'<td class="q">{_esc(r.text[:150])}</td>',
            f'<td class="num">{r.confidence:.2f}</td>',
            f"<td>{_esc(r.reason[:170])}</td>",
        ]
        for r in sorted(analysis.applied_removals, key=lambda x: x.start)
    ]
    removal_table = _html_table(
        ["Timecode", "Motif", "Duree", "Texte", "Confiance", "Justification"],
        removal_rows,
        "Aucune parole supprimee.",
    )

    # -- doutes -------------------------------------------------------- #
    flag_rows = [
        [
            f'<td class="tc">{_esc(format_timecode(f.start))}</td>',
            f'<td><span class="chip">'
            f"{_esc(FLAG_LABELS.get(f.category, f.category))}</span></td>",
            f'<td class="q">{_esc(f.text[:150])}</td>',
            f'<td class="num">{f.confidence:.2f}</td>',
            f"<td>{_esc(f.suggestion or f.reason)}</td>",
        ]
        for f in sorted(analysis.flags, key=lambda x: x.start)
    ]
    flag_table = _html_table(
        ["Timecode", "Nature du doute", "Texte", "Confiance", "Conseil"],
        flag_rows,
        "Aucune decision incertaine : tout etait net.",
    )

    # -- raccords ------------------------------------------------------ #
    seam_rows = [
        [
            f'<td class="tc">{_esc(format_timecode(w.timeline_time))}</td>',
            f'<td class="sev-{_esc(w.severity)}">{_esc(w.severity)}</td>',
            f'<td><span class="chip">'
            f"{_esc(SEAM_LABELS.get(w.category, w.category))}</span></td>",
            f"<td>{_esc(w.reason)}</td>",
            f'<td class="q">{_esc(w.before[:60])}'
            f'{" → " + _esc(w.after[:60]) if w.after else ""}</td>',
        ]
        for w in sorted(
            analysis.seams,
            key=lambda x: (SEVERITY_ORDER.get(x.severity, 3), x.timeline_time),
        )
    ]
    seam_table = _html_table(
        ["Timecode montage", "Gravite", "Type", "Detail", "Contexte"],
        seam_rows,
        "Aucun raccord suspect.",
    )

    # -- silences ------------------------------------------------------ #
    silence_rows = [
        [
            f"<td>{_esc(data.get('label', kind))}</td>",
            f'<td class="num">{int(data["count"])}</td>',
            f'<td class="num">{data["removed"]:.2f} s</td>',
        ]
        for kind, data in sorted(
            (
                (k, {**v, "label": k.replace("_", " ")})
                for k, v in stats.get("gaps", {}).items()
            ),
            key=lambda item: -item[1]["removed"],
        )
    ]
    silence_table = _html_table(
        ["Type de blanc", "Nombre", "Retire"], silence_rows, "Aucun blanc traite."
    )

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{_esc(APP_NAME)} – {_esc(media.path.name)}</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>Rapport de montage</h1>
<p class="sub">{_esc(media.path.name)} · style
{_esc(style_label(settings.style))} · {_esc(APP_NAME)} {_esc(__version__)}</p>

<div class="bar">{bar}</div>
<div class="legend">{legend}</div>

<h2>Resume</h2>
<div class="grid">{cards}</div>

<h2>Zooms places</h2>
{zoom_table}

<h2>Reprises de phrases</h2>
{retakes_html}

<h2>Hesitations et bafouillages supprimes</h2>
{disfluency_table}

<h2>Tout ce qui a ete supprime</h2>
{removal_table}

<h2>Decisions incertaines (conservees par prudence)</h2>
{flag_table}

<h2>Raccords suspects</h2>
{seam_table}

<h2>Traitement des blancs</h2>
{silence_table}

<h2>Texte du montage final</h2>
<div class="final">{_esc(_final_text(analysis))}</div>

<footer>Genere par {_esc(APP_NAME)} {_esc(__version__)}.
Les suppressions dont la confiance est inferieure au seuil ne sont jamais
appliquees : elles apparaissent dans « Decisions incertaines » et
restent dans le montage.</footer>
</div></body></html>
"""


def write_html_report(
    path: str | Path,
    media: MediaInfo,
    settings: Settings,
    analysis: AnalysisResult,
    zooms: ZoomPlan | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(media, settings, analysis, zooms), encoding="utf-8")
    log.info("Rapport HTML ecrit : %s", path.name)
    return path


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def render_markdown(
    media: MediaInfo,
    settings: Settings,
    analysis: AnalysisResult,
    zooms: ZoomPlan | None = None,
) -> str:
    """Version texte du rapport, pratique a relire dans un editeur."""
    lines: list[str] = [
        f"# Rapport de montage – {media.path.name}",
        "",
        f"*{APP_NAME} {__version__} · style {style_label(settings.style)}*",
        "",
        "## Resume",
        "",
        "| Element | Valeur |",
        "| --- | --- |",
    ]
    for key, value in _summary_rows(media, settings, analysis, zooms):
        lines.append(f"| {key} | {value} |")

    lines += ["", "## Zooms places", ""]
    if zooms is not None and zooms.events:
        lines += ["| Timecode | Type | Amplitude | Duree |", "| --- | --- | --- | --- |"]
        for event in zooms.events:
            amplitude = (
                f"{event.start_scale:.0f} % -> {event.end_scale:.0f} %"
                if event.is_animated
                else f"{event.end_scale:.0f} % (fixe)"
            )
            lines.append(
                f"| {format_timecode(event.timeline_start)} | {event.label} | "
                f"{amplitude} | {event.animation_duration:.2f} s |"
            )
    else:
        lines.append("_Aucun zoom place._")

    lines += ["", "## Reprises de phrases", ""]
    if analysis.retake_groups:
        for group in analysis.retake_groups:
            lines.append(
                f"### Reprise {group.index + 1} – {group.anchor}"
            )
            lines.append("")
            lines.append(
                f"- **Conserve** ({format_timecode(group.kept_start)}) : "
                f"« {group.kept_text} »"
            )
            for attempt in group.attempts:
                applied = any(
                    r.applied
                    and r.utterance_index == attempt.utterance_index
                    and r.source in {"retake", "marker"}
                    for r in analysis.removals
                )
                verb = "Supprime" if applied else "Conserve par prudence"
                lines.append(
                    f"- {verb} ({format_timecode(attempt.start)}, "
                    f"confiance {attempt.confidence:.2f}) : « {attempt.text} »"
                )
            lines.append("")
    else:
        lines.append("_Aucune reprise detectee._")

    lines += ["", "## Hesitations supprimees", ""]
    disfluencies = [
        r
        for r in analysis.applied_removals
        if r.source in {"filler", "filler_phrase", "stutter", "abandoned"}
    ]
    if disfluencies:
        for removal in disfluencies:
            kept = f" -> « {removal.kept_instead} »" if removal.kept_instead else ""
            lines.append(
                f"- {format_timecode(removal.start)} [{removal.label}] "
                f"« {removal.text} »{kept}"
            )
    else:
        lines.append("_Aucune._")

    lines += ["", "## Decisions incertaines (conservees)", ""]
    if analysis.flags:
        for flag in sorted(analysis.flags, key=lambda f: f.start):
            lines.append(
                f"- {format_timecode(flag.start)} "
                f"[{FLAG_LABELS.get(flag.category, flag.category)}] "
                f"confiance {flag.confidence:.2f} – « {flag.text[:120]} »"
            )
    else:
        lines.append("_Aucune._")

    lines += ["", "## Raccords suspects", ""]
    if analysis.seams:
        for warning in sorted(
            analysis.seams,
            key=lambda w: (SEVERITY_ORDER.get(w.severity, 3), w.timeline_time),
        ):
            lines.append(
                f"- {format_timecode(warning.timeline_time)} "
                f"[{warning.severity}] "
                f"{SEAM_LABELS.get(warning.category, warning.category)} – "
                f"{warning.reason}"
            )
    else:
        lines.append("_Aucun._")

    lines += ["", "## Texte du montage final", "", _final_text(analysis), ""]
    return "\n".join(lines)


def write_markdown_report(
    path: str | Path,
    media: MediaInfo,
    settings: Settings,
    analysis: AnalysisResult,
    zooms: ZoomPlan | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(media, settings, analysis, zooms), encoding="utf-8")
    log.info("Rapport Markdown ecrit : %s", path.name)
    return path
