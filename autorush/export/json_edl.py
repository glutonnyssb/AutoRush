"""Export JSON complet du montage.

Ce fichier est la source de verite machine : il contient la timeline, les
zooms, toutes les decisions et leurs justifications. Il sert a

* rejouer un rendu (preview) sans refaire la transcription ;
* brancher un autre outil sur AutoRush ;
* comparer deux reglages.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autorush.analysis.decisions import AnalysisResult
from autorush.config import Settings
from autorush.logging_setup import get_logger
from autorush.media.ffmpeg import MediaInfo
from autorush.version import APP_NAME, __version__
from autorush.zoom.planner import ZoomPlan

log = get_logger("export.json")


def build_payload(
    media: MediaInfo,
    settings: Settings,
    analysis: AnalysisResult,
    zooms: ZoomPlan | None = None,
    seams: list | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "app": APP_NAME,
        "version": __version__,
        "format": "autorush-edit",
        "format_version": 2,
        "media": media.as_dict(),
        "settings": settings.to_dict(),
        "analysis": analysis.as_dict(),
        "zooms": zooms.as_dict() if zooms is not None else {"count": 0, "events": []},
        "seams": [w.as_dict() for w in (seams or [])],
    }
    if extra:
        payload.update(extra)
    return payload


def write_json_edl(
    path: str | Path,
    media: MediaInfo,
    settings: Settings,
    analysis: AnalysisResult,
    zooms: ZoomPlan | None = None,
    seams: list | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(media, settings, analysis, zooms, seams, extra)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("JSON de montage ecrit : %s", path.name)
    return path


def load_json_edl(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
