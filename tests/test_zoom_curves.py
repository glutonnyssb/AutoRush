"""Courbes de zoom : la sensation de « ballon lance » doit etre mesurable."""

from __future__ import annotations

import pytest

from autorush.zoom.curves import (
    ballistic,
    curve_by_name,
    ease_out_expo,
    progressive,
    sample_curve,
    smootherstep,
)


# --------------------------------------------------------------------------- #
# Zoom progressif
# --------------------------------------------------------------------------- #
def test_progressive_part_de_zero_et_arrive_a_un():
    assert progressive(0.0) == pytest.approx(0.0)
    assert progressive(1.0) == pytest.approx(1.0)


def test_progressive_est_monotone():
    values = [progressive(i / 200) for i in range(201)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:], strict=False))


def test_progressive_est_visible_sur_tout_le_plan():
    """Le cahier des charges interdit un zoom qui s'arrete au bout d'une seconde.

    On verifie qu'aucun quart du plan ne concentre le mouvement, et qu'aucun
    quart n'est fige.
    """
    quarters = [
        progressive(0.25) - progressive(0.00),
        progressive(0.50) - progressive(0.25),
        progressive(0.75) - progressive(0.50),
        progressive(1.00) - progressive(0.75),
    ]
    assert all(part > 0.12 for part in quarters), quarters
    assert all(part < 0.40 for part in quarters), quarters


# --------------------------------------------------------------------------- #
# Zoom lance / ballon
# --------------------------------------------------------------------------- #
def test_ballistic_part_vite():
    """Un ballon lance fait l'essentiel du chemin tout de suite."""
    assert ballistic(0.10) > 0.50
    assert ballistic(0.25) > 0.90


def test_ballistic_depasse_la_cible_puis_revient():
    """L'inertie : le zoom va au-dela, puis se pose exactement sur la cible."""
    samples = [ballistic(i / 500) for i in range(501)]
    peak = max(samples)
    assert peak > 1.05, "il faut un depassement visible"
    assert ballistic(1.0) == pytest.approx(1.0, abs=1e-9)
    peak_time = samples.index(peak) / 500
    assert 0.3 < peak_time < 0.8, peak_time


def test_ballistic_ralentit_progressivement():
    """La vitesse doit decroitre : c'est la deceleration du ballon."""
    step = 0.02
    speeds = [
        (ballistic(t + step) - ballistic(t)) / step
        for t in (0.0, 0.05, 0.10, 0.20, 0.30)
    ]
    assert all(b < a for a, b in zip(speeds, speeds[1:], strict=False)), speeds
    assert speeds[0] > 4.0, "le depart doit etre franc"


def test_ballistic_sans_depassement_reste_monotone():
    values = [ballistic(i / 200, overshoot=0.0) for i in range(201)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:], strict=False))
    assert values[-1] == pytest.approx(1.0)


def test_ease_out_expo_et_smootherstep():
    assert ease_out_expo(0.0) == pytest.approx(0.0)
    assert ease_out_expo(1.0) == pytest.approx(1.0)
    assert ease_out_expo(0.25) > 0.25, "ease-out : rapide au debut"
    assert smootherstep(0.5) == pytest.approx(0.5)
    assert smootherstep(0.1) < 0.1, "smootherstep : lent au debut"


def test_curve_by_name():
    assert curve_by_name("lance") is ballistic
    assert curve_by_name("progressif") is progressive
    assert curve_by_name("inconnu") is progressive


# --------------------------------------------------------------------------- #
# Echantillonnage en keyframes
# --------------------------------------------------------------------------- #
def test_sample_curve_commence_et_finit_sur_les_valeurs_exactes():
    keyframes = sample_curve(progressive, 6.0, 100.0, 112.0, fps=25)
    assert keyframes[0] == (0.0, 100.0)
    assert keyframes[-1][0] == pytest.approx(6.0)
    assert keyframes[-1][1] == pytest.approx(112.0)


def test_sample_curve_reste_dans_le_budget_de_keyframes():
    keyframes = sample_curve(
        lambda t: ballistic(t), 8.0, 100.0, 140.0, fps=60, max_keyframes=20
    )
    assert len(keyframes) <= 20


def test_sample_curve_reste_fidele_a_la_courbe():
    """L'interpolation lineaire des keyframes doit reproduire la courbe.

    C'est la garantie que le rendu Premiere (qui interpole lineairement) et la
    preview ffmpeg donnent bien la courbe voulue.
    """
    duration, start, end = 1.05, 100.0, 112.0
    keyframes = sample_curve(
        lambda t: ballistic(t), duration, start, end, fps=25, tolerance=0.22
    )

    def interpolate(time: float) -> float:
        for index in range(len(keyframes) - 1):
            t0, v0 = keyframes[index]
            t1, v1 = keyframes[index + 1]
            if t0 <= time <= t1:
                if t1 == t0:
                    return v0
                factor = (time - t0) / (t1 - t0)
                return v0 + (v1 - v0) * factor
        return keyframes[-1][1]

    worst = 0.0
    for step in range(106):
        time = step * duration / 105
        expected = start + (end - start) * ballistic(time / duration)
        worst = max(worst, abs(interpolate(time) - expected))
    assert worst < 0.30, f"ecart maximal {worst:.3f} point de zoom"


def test_sample_curve_duree_nulle():
    assert sample_curve(progressive, 0.0, 100.0, 110.0) == [(0.0, 110.0)]


def test_une_courbe_lineaire_ne_demande_que_deux_keyframes():
    keyframes = sample_curve(lambda t: t, 10.0, 100.0, 120.0, fps=25)
    assert len(keyframes) == 2
