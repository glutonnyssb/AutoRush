"""Placement automatique des zooms.

Objectif : un mouvement qui souligne le propos, jamais un mouvement decoratif.
Le placement se fait en quatre temps.

1. **Notation** de chaque plan : montee d'energie a l'attaque, changement
   d'idee ("mais", "donc", "en fait"), mots d'insistance, plan long et statique,
   debut de phrase. Les plans courts, muets ou issus d'une hesitation sont
   penalises.
2. **Selection** des meilleurs plans, avec un ecart minimal entre deux zooms
   (anti-fatigue) et un quota par minute. Les deux dependent de l'intensite
   choisie par l'utilisateur.
3. **Anti-monotonie** : un zoom est force si un long passage reste statique.
4. **Attribution des types** : direct, progressif ou lance, en evitant deux
   fois le meme type d'affilee et en respectant la duree minimale de chaque
   type.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from autorush.analysis.lexicon import EMPHASIS_WORDS, IDEA_SHIFT_WORDS
from autorush.config import ZoomSettings
from autorush.editing.timeline import Shot, Timeline
from autorush.utils import clamp, format_timecode, remap
from autorush.zoom.curves import ballistic, progressive, sample_curve

#: types de zoom
ZOOM_DIRECT = "direct"
ZOOM_PROGRESSIVE = "progressif"
ZOOM_LAUNCHED = "lance"

TYPE_LABELS: dict[str, str] = {
    ZOOM_DIRECT: "Zoom direct",
    ZOOM_PROGRESSIVE: "Zoom progressif",
    ZOOM_LAUNCHED: "Zoom lance",
}

#: ponderation de la note d'un plan
W_ONSET = 1.00
W_ENERGY = 0.55
W_IDEA_SHIFT = 0.50
W_EMPHASIS = 0.45
W_SENTENCE_START = 0.30
W_LONG_SHOT = 0.50
W_AFTER_CUT = 0.20

#: un zoom direct est un saut : on reduit un peu son amplitude
DIRECT_AMPLITUDE_FACTOR = 0.85
#: le zoom lance vise un peu plus loin (l'inertie rend le mouvement plus doux)
LAUNCHED_AMPLITUDE_FACTOR = 1.05


@dataclass
class ZoomEvent:
    """Un zoom place sur un plan."""

    index: int
    shot_index: int
    kind: str
    #: position dans le montage final
    timeline_start: float
    timeline_end: float
    #: position dans le rush d'origine
    source_start: float
    source_end: float
    start_scale: float
    end_scale: float
    #: duree de l'animation (pour le zoom lance, plus courte que le plan)
    animation_duration: float
    score: float = 0.0
    reason: str = ""
    focus_x: float = 0.5
    focus_y: float = 0.5
    peak_scale: float = 0.0
    tags: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return TYPE_LABELS.get(self.kind, self.kind)

    @property
    def shot_duration(self) -> float:
        return max(0.0, self.timeline_end - self.timeline_start)

    @property
    def is_animated(self) -> bool:
        return self.kind in (ZOOM_PROGRESSIVE, ZOOM_LAUNCHED)

    # ------------------------------------------------------------------ #
    def keyframes(
        self, fps: float = 30.0, max_keyframes: int = 90, tolerance: float = 0.22
    ) -> list[tuple[float, float]]:
        """Keyframes ``(temps depuis le debut du plan, echelle en %)``.

        Une liste vide signifie "echelle fixe" : c'est le cas du zoom direct.
        """
        if self.kind == ZOOM_DIRECT:
            return []

        if self.kind == ZOOM_PROGRESSIVE:
            return sample_curve(
                progressive,
                self.animation_duration,
                self.start_scale,
                self.end_scale,
                fps=fps,
                max_keyframes=max_keyframes,
                tolerance=tolerance,
            )

        # zoom lance : animation courte, puis maintien jusqu'a la fin du plan
        samples = sample_curve(
            lambda t: ballistic(t, self.overshoot, self.stiffness),
            self.animation_duration,
            self.start_scale,
            self.end_scale,
            fps=fps,
            max_keyframes=max_keyframes,
            tolerance=tolerance,
        )
        hold_until = self.shot_duration
        if hold_until > self.animation_duration + 1.0 / max(fps, 1.0):
            samples.append((hold_until, self.end_scale))
        return samples

    #: parametres de la courbe ballistique (remplis par le planificateur)
    overshoot: float = 0.11
    stiffness: float = 5.6

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "shot_index": self.shot_index,
            "kind": self.kind,
            "label": self.label,
            "timecode": format_timecode(self.timeline_start),
            "timeline_start": round(self.timeline_start, 3),
            "timeline_end": round(self.timeline_end, 3),
            "source_start": round(self.source_start, 3),
            "source_end": round(self.source_end, 3),
            "start_scale": round(self.start_scale, 2),
            "end_scale": round(self.end_scale, 2),
            "peak_scale": round(self.peak_scale, 2),
            "animation_duration": round(self.animation_duration, 3),
            "score": round(self.score, 3),
            "reason": self.reason,
            "focus_x": round(self.focus_x, 4),
            "focus_y": round(self.focus_y, 4),
        }


@dataclass
class ZoomPlan:
    """Ensemble des zooms d'un montage."""

    events: list[ZoomEvent] = field(default_factory=list)
    #: note de chaque plan (diagnostic)
    scores: dict[int, float] = field(default_factory=dict)
    settings_intensity: float = 0.0

    def by_shot(self) -> dict[int, ZoomEvent]:
        return {event.shot_index: event for event in self.events}

    def count_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return counts

    def per_minute(self, duration: float) -> float:
        if duration <= 0:
            return 0.0
        return len(self.events) * 60.0 / duration

    def as_dict(self) -> dict:
        return {
            "intensity": round(self.settings_intensity, 1),
            "count": len(self.events),
            "by_kind": self.count_by_kind(),
            "events": [event.as_dict() for event in self.events],
        }


# --------------------------------------------------------------------------- #
def _score_shot(shot: Shot, settings: ZoomSettings) -> tuple[float, list[str]]:
    """Note un plan et explique pourquoi."""
    reasons: list[str] = []
    score = 0.0

    if not shot.has_speech:
        return -5.0, ["plan muet"]
    if shot.duration < settings.direct_min_shot:
        return -5.0, ["plan trop court"]

    if shot.onset >= 0:
        contribution = W_ONSET * shot.onset
        if contribution > 0.12:
            reasons.append("montee d'energie a l'attaque")
        score += contribution
    if shot.energy >= 0:
        score += W_ENERGY * shot.energy
        if shot.energy > 0.72:
            reasons.append("plan energique")

    tokens = [w.norm for w in shot.words]
    opening = tokens[:4]
    if any(token in IDEA_SHIFT_WORDS for token in opening):
        score += W_IDEA_SHIFT
        reasons.append("changement d'idee")
    emphasis = [token for token in tokens if token in EMPHASIS_WORDS]
    if emphasis:
        score += W_EMPHASIS
        reasons.append(f"mot d'insistance ({emphasis[0]})")

    if shot.starts_sentence:
        score += W_SENTENCE_START
        reasons.append("debut de phrase")

    if shot.duration >= 5.0:
        score += min(W_LONG_SHOT, W_LONG_SHOT * (shot.duration - 5.0) / 12.0 + 0.16)
        reasons.append("plan long a dynamiser")

    if "apres_coupe" in shot.tags:
        if settings.avoid_after_disfluency:
            score -= 0.45
            reasons.append("juste apres une suppression (evite)")
        else:
            score += W_AFTER_CUT

    # un plan sans aucune analyse audio garde une note utilisable
    if shot.energy < 0 and shot.onset < 0:
        score += 0.25 * min(1.0, shot.duration / 6.0)

    return score, reasons


def _pick_type(
    shot: Shot,
    previous_kind: str | None,
    settings: ZoomSettings,
    rng: random.Random,
) -> str:
    """Choisit un type de zoom compatible avec la duree du plan."""
    weights: list[tuple[str, float]] = []
    if shot.duration >= settings.progressive_min_shot:
        weights.append((ZOOM_PROGRESSIVE, max(0.0, settings.weight_progressive)))
    if shot.duration >= settings.launched_min_shot:
        weights.append((ZOOM_LAUNCHED, max(0.0, settings.weight_launched)))
    if shot.duration >= settings.direct_min_shot:
        weights.append((ZOOM_DIRECT, max(0.0, settings.weight_direct)))

    if not weights:
        return ZOOM_DIRECT

    if settings.avoid_repeating_type and previous_kind and len(weights) > 1:
        filtered = [(kind, weight) for kind, weight in weights if kind != previous_kind]
        if filtered and sum(w for _, w in filtered) > 0:
            weights = filtered

    total = sum(weight for _, weight in weights)
    if total <= 0:
        return weights[0][0]
    draw = rng.random() * total
    accumulated = 0.0
    for kind, weight in weights:
        accumulated += weight
        if draw <= accumulated:
            return kind
    return weights[-1][0]


def plan_zooms(
    timeline: Timeline, settings: ZoomSettings, seed: int = 0
) -> ZoomPlan:
    """Place les zooms sur la timeline."""
    plan = ZoomPlan(settings_intensity=settings.intensity)
    if not settings.enabled or not timeline.shots:
        return plan

    rng = random.Random(seed)
    intensity = clamp(settings.intensity, 0.0, 100.0)

    # l'intensite pilote a la fois l'amplitude, l'ecart minimal et le quota
    spacing = settings.min_spacing * remap(intensity, 0, 100, 1.55, 0.62)
    density = remap(intensity, 0, 100, 0.40, 1.30)
    quota_per_minute = settings.max_per_minute * density
    base_delta = remap(intensity, 0, 100, settings.scale_min_delta, settings.scale_max_delta)

    duration = timeline.duration
    max_events = max(1, int(round(quota_per_minute * duration / 60.0)))

    # ------------------------------------------------------------------ #
    # 1. notation
    # ------------------------------------------------------------------ #
    scored: list[tuple[float, Shot, list[str]]] = []
    for shot in timeline.shots:
        score, reasons = _score_shot(shot, settings)
        plan.scores[shot.index] = round(score, 3)
        if score > -1.0:
            scored.append((score, shot, reasons))

    if not scored:
        return plan

    # ------------------------------------------------------------------ #
    # 2. selection par note decroissante, avec ecart minimal
    # ------------------------------------------------------------------ #
    selected: list[tuple[Shot, float, list[str]]] = []
    chosen_times: list[float] = []

    def far_enough(shot: Shot) -> bool:
        return all(
            abs(shot.timeline_start - other) >= spacing for other in chosen_times
        )

    for score, shot, reasons in sorted(scored, key=lambda item: -item[0]):
        if len(selected) >= max_events:
            break
        if not far_enough(shot):
            continue
        selected.append((shot, score, reasons))
        chosen_times.append(shot.timeline_start)

    # ------------------------------------------------------------------ #
    # 3. anti-monotonie : casser les longues zones statiques
    # ------------------------------------------------------------------ #
    if settings.max_static_duration > 0:
        selected = _fill_static_gaps(
            selected, scored, settings, spacing * 0.7, duration
        )

    selected.sort(key=lambda item: item[0].timeline_start)

    # ------------------------------------------------------------------ #
    # 4. attribution des types et des amplitudes
    # ------------------------------------------------------------------ #
    previous_kind: str | None = None
    for shot, score, reasons in selected:
        kind = _pick_type(shot, previous_kind, settings, rng)
        previous_kind = kind

        # amplitude : base + jitter + bonus de note (un plan fort bouge plus)
        jitter = 1.0 + rng.uniform(-0.18, 0.18)
        emphasis_bonus = 1.0 + clamp(score, 0.0, 2.0) * 0.10
        delta = base_delta * jitter * emphasis_bonus

        if kind == ZOOM_DIRECT:
            delta *= DIRECT_AMPLITUDE_FACTOR
        elif kind == ZOOM_LAUNCHED:
            delta *= LAUNCHED_AMPLITUDE_FACTOR

        start_scale = settings.scale_floor
        end_scale = clamp(
            settings.scale_floor + delta, settings.scale_floor, settings.scale_ceiling
        )

        if kind == ZOOM_DIRECT:
            # saut instantane au point de coupe : echelle fixe sur tout le plan
            start_scale = end_scale
            animation = 0.0
            peak = end_scale
        elif kind == ZOOM_PROGRESSIVE:
            animation = shot.duration
            peak = end_scale
        else:
            wanted = settings.launched_duration + rng.uniform(
                -settings.launched_duration_jitter, settings.launched_duration_jitter
            )
            animation = clamp(wanted, 0.55, max(0.55, shot.duration))
            peak = clamp(
                start_scale + (end_scale - start_scale) * (1.0 + settings.launched_overshoot),
                settings.scale_floor,
                settings.scale_ceiling + 6.0,
            )

        event = ZoomEvent(
            index=len(plan.events),
            shot_index=shot.index,
            kind=kind,
            timeline_start=shot.timeline_start,
            timeline_end=shot.timeline_end,
            source_start=shot.source_start,
            source_end=shot.source_end,
            start_scale=start_scale,
            end_scale=end_scale,
            animation_duration=animation,
            score=score,
            reason=", ".join(reasons) or "dynamisation du plan",
            focus_x=settings.focus_x,
            focus_y=settings.focus_y,
            peak_scale=peak,
            overshoot=settings.launched_overshoot,
            stiffness=settings.launched_stiffness,
        )
        plan.events.append(event)

    return plan


def _fill_static_gaps(
    selected: list[tuple[Shot, float, list[str]]],
    scored: list[tuple[float, Shot, list[str]]],
    settings: ZoomSettings,
    min_spacing: float,
    duration: float,
) -> list[tuple[Shot, float, list[str]]]:
    """Ajoute un zoom la ou le montage reste statique trop longtemps."""
    if not scored:
        return selected

    by_time = sorted(selected, key=lambda item: item[0].timeline_start)
    chosen_ids = {shot.index for shot, _, _ in by_time}
    available = sorted(
        (item for item in scored if item[1].index not in chosen_ids),
        key=lambda item: -item[0],
    )

    guard = 0
    while guard < 200:
        guard += 1
        times = [0.0] + [shot.timeline_start for shot, _, _ in by_time] + [duration]
        widest = 0.0
        window: tuple[float, float] | None = None
        for index in range(len(times) - 1):
            span = times[index + 1] - times[index]
            if span > widest:
                widest = span
                window = (times[index], times[index + 1])
        if window is None or widest <= settings.max_static_duration:
            break

        low, high = window
        candidate = None
        for score, shot, reasons in available:
            if not (low < shot.timeline_start < high):
                continue
            if any(
                abs(shot.timeline_start - other.timeline_start) < min_spacing
                for other, _, _ in by_time
            ):
                continue
            candidate = (score, shot, reasons)
            break
        if candidate is None:
            break
        score, shot, reasons = candidate
        by_time.append((shot, score, reasons + ["casse un long plan statique"]))
        by_time.sort(key=lambda item: item[0].timeline_start)
        available = [item for item in available if item[1].index != shot.index]

    return by_time
