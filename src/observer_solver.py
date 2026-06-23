from __future__ import annotations

import math
from dataclasses import dataclass

from geo import destination_point, haversine_distance_km, topocentric_aircraft_position, angular_separation_deg
from models import CelestialBodyState, PredictedPoint


@dataclass(frozen=True)
class ObserverSolution:
    lat: float
    lon: float
    distance_km: float
    confidence: float
    angular_separation_deg: float
    offset_body_diameters: float
    reason: str | None = None


def google_maps_url(lat: float, lon: float) -> str:
    return f"https://maps.google.com/?q={lat},{lon}"


def google_nav_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"


def solve_observer_point(
    user_lat: float,
    user_lon: float,
    aircraft_point: PredictedPoint,
    body: CelestialBodyState,
    max_relocation_km: float,
) -> ObserverSolution:
    best = _solution_at(user_lat, user_lon, user_lat, user_lon, aircraft_point, body, 0.70)
    if best.offset_body_diameters <= 0.25:
        return best

    for lat, lon, _radius, _bearing in observer_search_grid(user_lat, user_lon, max_relocation_km)[1:]:
        candidate = _solution_at(user_lat, user_lon, lat, lon, aircraft_point, body, 0.82)
        if candidate.angular_separation_deg < best.angular_separation_deg:
            best = candidate

    if best.distance_km > max_relocation_km:
        return ObserverSolution(
            user_lat,
            user_lon,
            0.0,
            0.30,
            best.angular_separation_deg,
            best.offset_body_diameters,
            "OBSERVER_POINT_TOO_FAR",
        )
    return best


def observer_search_grid(
    user_lat: float,
    user_lon: float,
    max_relocation_km: float,
) -> list[tuple[float, float, float, float | None]]:
    """Return the exact concentric points sampled by the observer solver."""
    points: list[tuple[float, float, float, float | None]] = [
        (user_lat, user_lon, 0.0, None)
    ]
    for radius in _search_radii(max_relocation_km):
        for bearing in range(0, 360, 15):
            lat, lon = destination_point(user_lat, user_lon, bearing, min(radius, max_relocation_km))
            points.append((lat, lon, radius, float(bearing)))
    return points


def _search_radii(max_relocation_km: float) -> list[float]:
    radii = []
    radius = 0.25
    while radius <= min(1.5, max_relocation_km) + 1e-9:
        radii.append(round(radius, 2))
        radius += 0.25
    radius = 3.0
    while radius <= max_relocation_km + 1e-9:
        radii.append(round(radius, 2))
        radius += 1.5
    if max_relocation_km > 0 and (not radii or radii[-1] < max_relocation_km):
        radii.append(max_relocation_km)
    return radii


def _solution_at(
    user_lat: float,
    user_lon: float,
    lat: float,
    lon: float,
    aircraft_point: PredictedPoint,
    body: CelestialBodyState,
    confidence: float,
) -> ObserverSolution:
    az, el, _rng = topocentric_aircraft_position(lat, lon, aircraft_point.lat, aircraft_point.lon, aircraft_point.altitude_ft)
    separation = angular_separation_deg(az, el, body.azimuth_deg, body.elevation_deg)
    offset = separation / max(1e-9, 2 * body.angular_radius_deg)
    distance = haversine_distance_km(user_lat, user_lon, lat, lon)
    confidence *= math.exp(-distance / 10)
    return ObserverSolution(lat, lon, distance, confidence, separation, offset)
