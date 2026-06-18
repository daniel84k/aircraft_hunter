from __future__ import annotations

import math

from geo import angular_separation_deg
from models import AircraftState, CelestialBodyState, PredictedPoint


def detect_transit_candidates(
    aircraft: AircraftState,
    points: list[PredictedPoint],
    bodies_by_time: dict,
    margin: float = 1.5,
    max_observer_relocation_km: float = 0.0,
) -> list[tuple[AircraftState, PredictedPoint, CelestialBodyState, float, float]]:
    candidates = []
    for point in points:
        bodies = bodies_by_time.get(point.timestamp)
        if not bodies:
            continue
        for body in bodies:
            search_radius_deg = body.angular_radius_deg * margin + _relocation_allowance_deg(
                max_observer_relocation_km,
                point.range_km,
            )
            separation = angular_separation_deg(
                point.azimuth_deg,
                point.elevation_deg,
                body.azimuth_deg,
                body.elevation_deg,
            )
            if separation < search_radius_deg:
                offset = separation / (2 * body.angular_radius_deg)
                candidates.append((aircraft, point, body, separation, offset))
    return candidates


def _relocation_allowance_deg(max_observer_relocation_km: float, range_km: float | None) -> float:
    if max_observer_relocation_km <= 0:
        return 0.0
    return math.degrees(math.atan2(max_observer_relocation_km, max(range_km or 1.0, 1.0)))


def closest_alignment(
    points: list[PredictedPoint],
    bodies_by_time: dict,
) -> tuple[str, float, float, PredictedPoint | None, CelestialBodyState | None] | None:
    best: tuple[str, float, float, PredictedPoint | None, CelestialBodyState | None] | None = None
    for point in points:
        bodies = bodies_by_time.get(point.timestamp)
        if not bodies:
            continue
        for body in bodies:
            separation = angular_separation_deg(
                point.azimuth_deg,
                point.elevation_deg,
                body.azimuth_deg,
                body.elevation_deg,
            )
            offset = separation / max(1e-9, 2 * body.angular_radius_deg)
            if best is None or separation < best[1]:
                best = (body.body, separation, offset, point, body)
    return best
