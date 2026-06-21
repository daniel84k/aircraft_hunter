from __future__ import annotations

from models import ScoreBreakdown


def alignment_score(offset_body_diameters: float) -> float:
    if offset_body_diameters <= 0.05:
        return 1.0
    if offset_body_diameters <= 0.15:
        return 0.8
    if offset_body_diameters <= 0.35:
        return 0.5
    if offset_body_diameters <= 0.75:
        return 0.15
    return 0.0


def lead_time_score(seconds: int) -> float:
    minutes = seconds / 60.0
    if seconds < 0:
        return 0.0
    if minutes <= 5:
        return 1.0
    if minutes <= 10:
        return 0.8
    if minutes <= 15:
        return 0.45
    if minutes <= 20:
        return 0.20
    return 0.05


def observer_distance_score(distance_km: float) -> float:
    if distance_km <= 2.4:
        return 1.0
    if distance_km <= 4.8:
        return 0.8
    if distance_km <= 6:
        return 0.4
    return 0.0


def body_elevation_score(elevation_deg: float) -> float:
    if elevation_deg < 5:
        return 0.0
    if elevation_deg < 8:
        return 0.3
    if elevation_deg < 10:
        return 0.7
    if elevation_deg <= 60:
        return 1.0
    return 0.8


def altitude_score(altitude_ft: float | None) -> float:
    if altitude_ft is None or altitude_ft < 5000:
        return 0.0
    if altitude_ft < 12000:
        return 0.2
    if altitude_ft < 18000:
        return 0.65
    if altitude_ft < 25000:
        return 0.9
    return 1.0


def aircraft_range_score(range_km: float | None) -> float:
    if range_km is None:
        return 0.5
    if range_km < 2:
        return 0.15
    if range_km < 5:
        return 0.55
    if range_km <= 80:
        return 1.0
    if range_km <= 120:
        return 0.65
    return 0.25


def final_score(
    *,
    offset_body_diameters: float,
    stability: float,
    altitude_ft: float | None,
    body_elevation_deg: float,
    aircraft_range_km: float | None,
    lead_time_seconds: int,
    observer_distance_km: float,
    solver_confidence: float,
) -> ScoreBreakdown:
    a = alignment_score(offset_body_diameters)
    alt = altitude_score(altitude_ft)
    body = body_elevation_score(body_elevation_deg)
    rng = aircraft_range_score(aircraft_range_km)
    lead = lead_time_score(lead_time_seconds)
    obs = observer_distance_score(observer_distance_km)
    score = a * stability * alt * body * rng * lead * obs
    confidence = max(0.0, min(1.0, solver_confidence * stability * min(1.0, a + 0.2)))
    return ScoreBreakdown(score, confidence, stability, a, alt, body, rng, lead, obs)
