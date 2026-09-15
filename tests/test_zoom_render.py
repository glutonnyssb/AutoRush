"""Verification que les zooms sont **reellement visibles** a l'image.

Le cahier des charges insiste : un zoom ne compte pas parce qu'il existe dans
un fichier, il doit se voir. Ce test mesure donc le zoom sur les images rendues.

Methode : un rush statique contenant un carre blanc de taille connue. La
largeur du carre dans la preview donne directement le facteur d'agrandissement,
au pixel pres.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from autorush.editing.timeline import Shot, Timeline
from autorush.export.preview import render_preview
from autorush.media.ffmpeg import ffmpeg_available, find_ffmpeg, probe_media
from autorush.transcription.base import Word
from autorush.zoom.curves import ballistic, progressive, sample_curve
from autorush.zoom.planner import (
    ZOOM_DIRECT,
    ZOOM_LAUNCHED,
    ZOOM_PROGRESSIVE,
    ZoomEvent,
    ZoomPlan,
)

pytestmark = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg n'est pas installe"
)

SOURCE_WIDTH, SOURCE_HEIGHT = 960, 540
SQUARE_HALF = 60
PREVIEW_HEIGHT = 540
SHOT_DURATION = 4.0
TARGET_SCALE = 120.0


@pytest.fixture(scope="module")
def square_rush(tmp_path_factory):
    """Un rush statique : carre blanc centre sur fond noir."""
    directory = tmp_path_factory.mktemp("carre")
    frame = np.zeros((SOURCE_HEIGHT, SOURCE_WIDTH, 3), dtype=np.uint8)
    frame[
        SOURCE_HEIGHT // 2 - SQUARE_HALF : SOURCE_HEIGHT // 2 + SQUARE_HALF,
        SOURCE_WIDTH // 2 - SQUARE_HALF : SOURCE_WIDTH // 2 + SQUARE_HALF,
    ] = 255
    raw = directory / "carre.rgb"
    frame.tofile(raw)

    import soundfile as sf

    sample_rate = 48000
    total = int(3 * SHOT_DURATION * sample_rate)
    times = np.arange(total) / sample_rate
    sf.write(
        str(directory / "son.wav"),
        (0.2 * np.sin(2 * np.pi * 180 * times)).astype(np.float32),
        sample_rate,
        subtype="PCM_16",
    )

    video = directory / "carre.mp4"
    subprocess.run(
        [
            find_ffmpeg(), "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{SOURCE_WIDTH}x{SOURCE_HEIGHT}", "-framerate", "25",
            "-stream_loop", str(int(3 * SHOT_DURATION * 25)), "-i", str(raw),
            "-i", str(directory / "son.wav"),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            "-t", str(3 * SHOT_DURATION), str(video),
        ],
        check=True,
    )
    return video


def _build_plan(media):
    """Trois plans de 4 s : lance, progressif, direct."""
    timeline = Timeline(source_duration=media.duration)
    cursor = 0.0
    for index in range(3):
        shot = Shot(
            index=index,
            source_start=cursor,
            source_end=cursor + SHOT_DURATION,
            timeline_start=cursor,
        )
        shot.words.append(
            Word(text="mot", start=cursor + 0.2, end=cursor + 0.6, index=index)
        )
        timeline.shots.append(shot)
        cursor += SHOT_DURATION

    plan = ZoomPlan()
    for index, (kind, animation) in enumerate(
        [(ZOOM_LAUNCHED, 1.05), (ZOOM_PROGRESSIVE, SHOT_DURATION), (ZOOM_DIRECT, 0.0)]
    ):
        shot = timeline.shots[index]
        plan.events.append(
            ZoomEvent(
                index=index,
                shot_index=index,
                kind=kind,
                timeline_start=shot.timeline_start,
                timeline_end=shot.timeline_end,
                source_start=shot.source_start,
                source_end=shot.source_end,
                start_scale=TARGET_SCALE if kind == ZOOM_DIRECT else 100.0,
                end_scale=TARGET_SCALE,
                animation_duration=animation,
                overshoot=0.11,
                stiffness=5.6,
            )
        )
    return timeline, plan


def _read_pgm(path: Path) -> np.ndarray:
    data = path.read_bytes()
    fields: list[bytes] = []
    cursor = 0
    while len(fields) < 4:
        while data[cursor : cursor + 1].isspace():
            cursor += 1
        if data[cursor : cursor + 1] == b"#":
            while data[cursor : cursor + 1] not in (b"\n", b""):
                cursor += 1
            continue
        start = cursor
        while not data[cursor : cursor + 1].isspace():
            cursor += 1
        fields.append(data[start:cursor])
    width, height = int(fields[1]), int(fields[2])
    cursor += 1
    return np.frombuffer(data[cursor : cursor + width * height], dtype=np.uint8).reshape(
        height, width
    )


def _square_width(image: np.ndarray) -> int:
    columns = (image > 128).any(axis=0)
    if not columns.any():
        return 0
    indices = np.where(columns)[0]
    return int(indices[-1] - indices[0] + 1)


@pytest.fixture(scope="module")
def measurements(square_rush, tmp_path_factory):
    """Rend la preview puis mesure le carre sur chaque image."""
    directory = tmp_path_factory.mktemp("mesure")
    media = probe_media(square_rush)
    timeline, plan = _build_plan(media)
    result = render_preview(
        directory / "preview.mp4",
        media,
        timeline,
        plan,
        work_dir=directory / "work",
        height=PREVIEW_HEIGHT,
        crf=14,
        preset="ultrafast",
    )
    frames = directory / "frames"
    frames.mkdir()
    subprocess.run(
        [
            find_ffmpeg(), "-loglevel", "error", "-y", "-i", str(result.path),
            "-vf", "fps=25", "-pix_fmt", "gray", "-f", "image2",
            str(frames / "f%04d.pgm"),
        ],
        check=True,
    )
    images = sorted(frames.glob("*.pgm"))
    assert images, "aucune image extraite"
    widths = [_square_width(_read_pgm(path)) for path in images]
    reference = widths[0]
    assert reference > 0, "le carre doit etre visible sur la premiere image"
    return [100.0 * width / reference for width in widths], result


def scale_at(scales: list[float], time: float) -> float:
    index = min(len(scales) - 1, int(round(time * 25)))
    return scales[index]


# --------------------------------------------------------------------------- #
# Zoom lance : depart rapide, depassement, retour
# --------------------------------------------------------------------------- #
def test_le_zoom_lance_est_visible_a_limage(measurements):
    scales, _ = measurements
    debut = scale_at(scales, 0.0)
    apres_un_dixieme = scale_at(scales, 0.12)
    assert debut == pytest.approx(100.0, abs=1.2)
    assert apres_un_dixieme > 110.0, (
        "le zoom lance doit avoir fait l'essentiel du chemin en 0,12 s"
    )


def test_le_zoom_lance_depasse_puis_se_pose(measurements):
    scales, _ = measurements
    pendant = [scale_at(scales, t / 100) for t in range(0, 105)]
    assert max(pendant) > TARGET_SCALE + 1.0, "l'inertie doit se voir"
    assert scale_at(scales, 3.8) == pytest.approx(TARGET_SCALE, abs=1.2)


# --------------------------------------------------------------------------- #
# Zoom progressif : continu sur tout le plan
# --------------------------------------------------------------------------- #
def test_le_zoom_progressif_avance_sur_tout_le_plan(measurements):
    scales, _ = measurements
    base = SHOT_DURATION
    jalons = [
        scale_at(scales, base + 0.05),
        scale_at(scales, base + 1.0),
        scale_at(scales, base + 2.0),
        scale_at(scales, base + 3.0),
        scale_at(scales, base + 3.9),
    ]
    assert jalons[0] == pytest.approx(100.0, abs=1.5)
    assert jalons[-1] == pytest.approx(TARGET_SCALE, abs=1.5)
    for precedent, suivant in zip(jalons, jalons[1:], strict=False):
        assert suivant > precedent + 1.0, (
            f"le zoom doit progresser sur chaque seconde : {jalons}"
        )


# --------------------------------------------------------------------------- #
# Zoom direct : saut instantane au point de coupe
# --------------------------------------------------------------------------- #
def test_le_zoom_direct_est_deja_en_place_au_premier_plan(measurements):
    scales, _ = measurements
    base = 2 * SHOT_DURATION
    assert scale_at(scales, base + 0.04) == pytest.approx(TARGET_SCALE, abs=1.5)
    assert scale_at(scales, base + 2.0) == pytest.approx(TARGET_SCALE, abs=1.5)


# --------------------------------------------------------------------------- #
# Fidelite a la courbe ecrite dans le XML
# --------------------------------------------------------------------------- #
def test_le_rendu_suit_les_keyframes_du_xml(measurements):
    """La preview doit reproduire les keyframes ecrites pour Premiere."""
    scales, _ = measurements

    keyframes = sample_curve(
        lambda t: ballistic(t, 0.11, 5.6), 1.05, 100.0, TARGET_SCALE, fps=25
    )
    points = list(keyframes) + [(SHOT_DURATION, TARGET_SCALE)]

    def attendu(time: float) -> float:
        for index in range(len(points) - 1):
            t0, v0 = points[index]
            t1, v1 = points[index + 1]
            if t0 <= time <= t1 and t1 > t0:
                return v0 + (v1 - v0) * (time - t0) / (t1 - t0)
        return points[-1][1]

    # On compare image par image : entre deux images le zoom lance bouge
    # beaucoup, comparer a un instant intermediaire n'aurait pas de sens.
    ecarts = [abs(scale_at(scales, t / 25) - attendu(t / 25)) for t in range(0, 95)]
    assert max(ecarts) < 2.0, f"ecart maximal {max(ecarts):.2f} point de zoom"


def test_le_zoom_progressif_suit_sa_courbe(measurements):
    scales, _ = measurements
    keyframes = sample_curve(progressive, SHOT_DURATION, 100.0, TARGET_SCALE, fps=25)

    def attendu(time: float) -> float:
        for index in range(len(keyframes) - 1):
            t0, v0 = keyframes[index]
            t1, v1 = keyframes[index + 1]
            if t0 <= time <= t1 and t1 > t0:
                return v0 + (v1 - v0) * (time - t0) / (t1 - t0)
        return keyframes[-1][1]

    ecarts = [
        abs(scale_at(scales, SHOT_DURATION + t / 25) - attendu(t / 25))
        for t in range(0, 99)
    ]
    assert max(ecarts) < 2.0, f"ecart maximal {max(ecarts):.2f} point de zoom"


def test_aucun_bord_noir_nest_visible(measurements):
    """Les zooms recadrent toujours vers l'interieur : jamais de bord noir."""
    scales, _ = measurements
    assert min(scales) >= 99.0, min(scales)
