from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from models import AircraftState


def _angle_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def calculate_track_change(history: Sequence[AircraftState], seconds: int = 60) -> float:
    points = [p for p in history if p.track_deg is not None]
    if len(points) < 2:
        return 0.0
    latest = points[-1]
    cutoff = latest.timestamp - timedelta(seconds=seconds)
    baseline = next((p for p in points if p.timestamp >= cutoff), points[0])
    return _angle_diff_deg(latest.track_deg or 0.0, baseline.track_deg or 0.0)


def calculate_gs_change(history: Sequence[AircraftState], seconds: int = 60) -> float:
    points = [p for p in history if p.ground_speed_kt is not None]
    if len(points) < 2:
        return 0.0
    latest = points[-1]
    cutoff = latest.timestamp - timedelta(seconds=seconds)
    baseline = next((p for p in points if p.timestamp >= cutoff), points[0])
    return abs((latest.ground_speed_kt or 0.0) - (baseline.ground_speed_kt or 0.0))


def stability_score(ac: AircraftState, history: Sequence[AircraftState]) -> float:
    score = 1.0

    if ac.altitude_ft is None:
        return 0.0

    if ac.altitude_ft < 5000:
        return 0.0
    if ac.altitude_ft < 12000:
        score *= 0.15
    elif ac.altitude_ft < 18000:
        score *= 0.60
    elif ac.altitude_ft < 25000:
        score *= 0.85

    if ac.vertical_rate_fpm is not None:
        vr = abs(ac.vertical_rate_fpm)
        if vr > 500:
            score *= 0.55
        if vr > 1000:
            score *= 0.20

    track_change_60s = calculate_track_change(history, seconds=60)
    gs_change_60s = calculate_gs_change(history, seconds=60)

    if track_change_60s > 8:
        score *= 0.45
    if track_change_60s > 15:
        score *= 0.10
    if track_change_60s > 30:
        return 0.0

    if gs_change_60s > 40:
        score *= 0.70

    return max(0.0, min(1.0, score))


def rejection_reason_for_unstable(ac: AircraftState, history: Sequence[AircraftState]) -> str | None:
    if ac.altitude_ft is None or ac.altitude_ft < 5000:
        return "LOW_ALTITUDE"
    if ac.vertical_rate_fpm is not None and abs(ac.vertical_rate_fpm) > 1000:
        return "HIGH_VERTICAL_RATE"
    if calculate_track_change(history, 60) > 30:
        return "UNSTABLE_TRACK"
    if len(history) < 2 and stability_score(ac, history) < 0.65:
        return "INSUFFICIENT_DATA"
    return None
