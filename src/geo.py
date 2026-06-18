from __future__ import annotations

import math


EARTH_RADIUS_KM = 6371.0088
FT_TO_KM = 0.0003048


def nm_to_km(nm: float) -> float:
    return nm * 1.852


def km_to_nm(km: float) -> float:
    return km / 1.852


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def destination_point(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    bearing = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    delta = distance_km / EARTH_RADIUS_KM

    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(bearing)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    y = math.sin(bearing) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * math.sin(phi2)
    lambda2 = lambda1 + math.atan2(y, x)
    lon2 = (math.degrees(lambda2) + 540) % 360 - 180
    return math.degrees(phi2), lon2


def bearing_between_points(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angular_separation_deg(az1: float, el1: float, az2: float, el2: float) -> float:
    az1r, el1r = math.radians(az1), math.radians(el1)
    az2r, el2r = math.radians(az2), math.radians(el2)
    cos_sep = (
        math.sin(el1r) * math.sin(el2r)
        + math.cos(el1r) * math.cos(el2r) * math.cos(az1r - az2r)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def topocentric_aircraft_position(
    observer_lat: float,
    observer_lon: float,
    aircraft_lat: float,
    aircraft_lon: float,
    aircraft_altitude_ft: float | None,
) -> tuple[float, float, float]:
    ground_range_km = haversine_distance_km(observer_lat, observer_lon, aircraft_lat, aircraft_lon)
    altitude_km = max(0.0, (aircraft_altitude_ft or 0.0) * FT_TO_KM)
    slant_range_km = math.hypot(ground_range_km, altitude_km)
    elevation_deg = math.degrees(math.atan2(altitude_km, max(ground_range_km, 1e-6)))
    azimuth_deg = bearing_between_points(observer_lat, observer_lon, aircraft_lat, aircraft_lon)
    return azimuth_deg, elevation_deg, slant_range_km
