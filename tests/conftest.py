"""Fixtures partagees par les tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autorush.analysis.decisions import analyze  # noqa: E402
from autorush.analysis.seams import check_seams  # noqa: E402
from autorush.config import Settings  # noqa: E402
from autorush.media.ffmpeg import MediaInfo  # noqa: E402
from autorush.transcription.synthetic import (  # noqa: E402
    ScriptLine,
    build_demo_transcript,
    build_transcript,
)
from autorush.zoom.planner import plan_zooms  # noqa: E402


@pytest.fixture
def demo_transcript():
    return build_demo_transcript()


@pytest.fixture
def settings():
    return Settings.for_style("dynamique")


@pytest.fixture
def media_info():
    return MediaInfo(
        path=Path("C:/Rushes/rush test.mp4"),
        duration=91.0,
        fps=25.0,
        timebase=25,
        ntsc=False,
        width=1920,
        height=1080,
        video_codec="h264",
        has_audio=True,
        audio_channels=2,
        audio_sample_rate=48000,
        audio_codec="aac",
    )


@pytest.fixture
def analysed(demo_transcript, settings):
    """Analyse complete du rush de demonstration."""
    result = analyze(demo_transcript, settings, None, demo_transcript.duration)
    result.seams = check_seams(
        result.timeline,
        result.utterances,
        result.removed_word_indices,
        settings.seam,
        result.benign_word_indices,
    )
    return result


@pytest.fixture
def zoom_plan(analysed, settings):
    return plan_zooms(analysed.timeline, settings.zoom, settings.seed)


def make_transcript(lines):
    """Raccourci : liste de ``(texte, blanc_avant)`` -> ``Transcript``."""
    return build_transcript(
        [ScriptLine(text=text, gap_before=gap) for text, gap in lines]
    )


def final_text(result) -> str:
    """Texte reellement conserve dans le montage."""
    removed = result.removed_word_indices
    parts = []
    for shot in result.timeline.shots:
        kept = [w.clean for w in shot.words if w.index not in removed]
        if kept:
            parts.append(" ".join(kept))
    return " ".join(parts)


def kept_words(result) -> list[str]:
    removed = result.removed_word_indices
    return [w.norm for w in result.transcript.words if w.index not in removed]
