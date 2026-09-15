"""Zooms automatiques : courbes et placement."""

from autorush.zoom.curves import (
    ballistic,
    ease_out_expo,
    progressive,
    sample_curve,
    smootherstep,
)
from autorush.zoom.planner import ZoomEvent, ZoomPlan, plan_zooms

__all__ = [
    "ballistic",
    "ease_out_expo",
    "progressive",
    "smootherstep",
    "sample_curve",
    "ZoomEvent",
    "ZoomPlan",
    "plan_zooms",
]
