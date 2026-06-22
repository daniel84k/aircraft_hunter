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


def has_stable_vertical_trend(
    history: Sequence[AircraftState],
    *,
    level_rate_fpm: float = 500,
    max_rate_fpm: float = 3000,
    max_variation_fpm: float = 600,
    min_points: int = 4,
    window_seconds: int = 90,
) -> bool:
    if not history:
        return False
    latest = history[-1]
    cutoff = latest.timestamp - timedelta(seconds=max(30, window_seconds))
    rates = [
        float(point.vertical_rate_fpm)
        for point in history
        if point.timestamp >= cutoff and point.vertical_rate_fpm is not None
    ]
    if len(rates) < max(3, min_points):
        return False
    if abs(rates[-1]) <= max(0.0, level_rate_fpm):
        return False
    if any(abs(rate) > max_rate_fpm for rate in rates):
        return False
    directions = {1 if rate > 0 else -1 if rate < 0 else 0 for rate in rates}
    if len(directions) != 1 or 0 in directions:
        return False
    return max(rates) - min(rates) <= max(0.0, max_variation_fpm)


def stability_score(
    ac: AircraftState,
    history: Sequence[AircraftState],
    *,
    vertical_rate_stable_fpm: float = 500,
    stable_vertical_trend: bool = False,
) -> float:
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

    if ac.vertical_rate_fpm is not None and not stable_vertical_trend:
        vr = abs(ac.vertical_rate_fpm)
        if vr > vertical_rate_stable_fpm:
            score *= 0.55
        if vr > 2 * vertical_rate_stable_fpm:
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


def rejection_reason_for_unstable(
    ac: AircraftState,
    history: Sequence[AircraftState],
    *,
    vertical_rate_stable_fpm: float = 500,
    stable_vertical_trend: bool = False,
) -> str | None:
    if ac.altitude_ft is None or ac.altitude_ft < 5000:
        return "LOW_ALTITUDE"
    if (
        ac.vertical_rate_fpm is not None
        and abs(ac.vertical_rate_fpm) > 2 * vertical_rate_stable_fpm
        and not stable_vertical_trend
    ):
        return "HIGH_VERTICAL_RATE"
    if calculate_track_change(history, 60) > 30:
        return "UNSTABLE_TRACK"
    if len(history) < 2 and stability_score(ac, history) < 0.65:
        return "INSUFFICIENT_DATA"
    return None
