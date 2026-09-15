"""Courbes d'interpolation des zooms.

Trois familles sont utilisees :

``progressive``
    quasi lineaire, avec des extremites adoucies. Le cadrage grossit de facon
    reguliere et **visible pendant tout le plan** : pas de mouvement concentre
    sur les premieres secondes.

``ease_out_expo``
    demarrage rapide, arrivee douce. Sert de base au zoom lance.

``ballistic``
    le zoom "ballon". La courbe est construite en deux temps :

    1. **le lancer** (jusqu'a ``peak_at``) : montee tres rapide qui decelere,
       et qui depasse legerement la cible (l'inertie) ;
    2. **l'amortissement** : retour tranquille vers la valeur cible.

    Le resultat ne ressemble pas a une animation d'interface : la camera part
    vite, continue d'avancer, ralentit, puis se pose.

Toutes les courbes prennent et renvoient une valeur dans [0, 1] (la courbe
``ballistic`` depasse temporairement 1, c'est le principe).
"""

from __future__ import annotations

import math
from collections.abc import Callable

from autorush.utils import clamp

#: part du mouvement effectuee de facon lineaire dans ``progressive``
PROGRESSIVE_LINEAR_MIX = 0.72


def linear(t: float) -> float:
    return clamp(t, 0.0, 1.0)


def smoothstep(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def smootherstep(t: float) -> float:
    """Courbe de Perlin : derivees nulles aux deux extremites."""
    t = clamp(t, 0.0, 1.0)
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


def progressive(t: float) -> float:
    """Montee reguliere du debut a la fin du plan, extremites adoucies."""
    t = clamp(t, 0.0, 1.0)
    return PROGRESSIVE_LINEAR_MIX * t + (1.0 - PROGRESSIVE_LINEAR_MIX) * smootherstep(t)


def ease_out_expo(t: float, stiffness: float = 4.0) -> float:
    """Depart rapide, arrivee douce. ``stiffness`` regle la nervosite."""
    t = clamp(t, 0.0, 1.0)
    stiffness = max(0.5, stiffness)
    denominator = 1.0 - math.exp(-stiffness)
    if denominator <= 1e-9:
        return t
    return (1.0 - math.exp(-stiffness * t)) / denominator


def ballistic(
    t: float,
    overshoot: float = 0.11,
    stiffness: float = 4.0,
    peak_at: float = 0.55,
) -> float:
    """Courbe du zoom lance / ballon.

    ``overshoot`` est le depassement, exprime en fraction de l'amplitude totale
    (0.11 = le zoom passe 11 % au-dela de la cible avant de revenir).
    ``peak_at`` est l'instant du depassement maximal, en fraction de la duree.
    """
    t = clamp(t, 0.0, 1.0)
    overshoot = max(0.0, overshoot)
    peak_at = clamp(peak_at, 0.15, 0.92)

    if t <= peak_at:
        # 1. le lancer : montee rapide qui decelere, jusqu'au depassement
        return (1.0 + overshoot) * ease_out_expo(t / peak_at, stiffness)
    # 2. l'amortissement : retour pose vers la cible
    settle = (t - peak_at) / (1.0 - peak_at)
    return (1.0 + overshoot) - overshoot * smootherstep(settle)


def curve_by_name(name: str) -> Callable[[float], float]:
    """Retourne une courbe par son nom (utilise par la preview)."""
    table: dict[str, Callable[[float], float]] = {
        "linear": linear,
        "lineaire": linear,
        "smoothstep": smoothstep,
        "smootherstep": smootherstep,
        "progressive": progressive,
        "progressif": progressive,
        "ease_out": ease_out_expo,
        "ballistic": ballistic,
        "lance": ballistic,
    }
    return table.get(name, progressive)


# --------------------------------------------------------------------------- #
# Echantillonnage en keyframes
# --------------------------------------------------------------------------- #
def _simplify(
    points: list[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    """Simplification Douglas-Peucker sur une courbe (temps, valeur).

    Retire les points que l'interpolation lineaire des voisins reproduit a
    ``tolerance`` pres. Premiere interpole lineairement entre deux keyframes :
    le rendu reste donc fidele a la courbe d'origine.
    """
    if len(points) <= 2:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        t0, v0 = points[first]
        t1, v1 = points[last]
        span = t1 - t0
        worst = 0.0
        worst_index = -1
        for index in range(first + 1, last):
            t, v = points[index]
            expected = v0 if span <= 0 else v0 + (v1 - v0) * (t - t0) / span
            error = abs(v - expected)
            if error > worst:
                worst = error
                worst_index = index
        if worst > tolerance and worst_index > 0:
            keep[worst_index] = True
            stack.append((first, worst_index))
            stack.append((worst_index, last))

    return [point for point, flag in zip(points, keep, strict=True) if flag]


def sample_curve(
    curve: Callable[[float], float],
    duration: float,
    start_value: float,
    end_value: float,
    fps: float = 30.0,
    max_keyframes: int = 90,
    tolerance: float = 0.22,
) -> list[tuple[float, float]]:
    """Convertit une courbe en keyframes ``(temps_relatif, valeur)``.

    La courbe est d'abord echantillonnee image par image (le rendu est donc
    exact), puis simplifiee pour ne garder que les keyframes utiles. C'est ce
    qui permet d'obtenir dans Premiere une animation lisible et modifiable, et
    non une forêt de keyframes.
    """
    duration = max(0.0, duration)
    if duration <= 0.0:
        return [(0.0, end_value)]

    fps = fps if fps > 0 else 30.0
    steps = max(2, int(round(duration * fps)))
    # une courbe avec depassement demande plus de finesse
    steps = min(steps, 4000)

    amplitude = end_value - start_value
    points: list[tuple[float, float]] = []
    for step in range(steps + 1):
        t = step / steps
        value = start_value + amplitude * curve(t)
        points.append((round(t * duration, 6), value))

    # on force la valeur finale exacte (le zoom doit finir pile sur la cible)
    points[-1] = (round(duration, 6), end_value)

    simplified = _simplify(points, tolerance)
    if len(simplified) > max_keyframes:
        # tolerance croissante jusqu'a tenir dans le budget
        factor = 1.6
        current = tolerance
        while len(simplified) > max_keyframes and current < 50.0:
            current *= factor
            simplified = _simplify(points, current)
    if len(simplified) > max_keyframes:  # pragma: no cover - securite
        stride = max(1, len(simplified) // max_keyframes)
        simplified = simplified[::stride] + [points[-1]]

    return simplified
