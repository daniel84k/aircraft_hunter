from __future__ import annotations

from dataclasses import dataclass

from geo import bearing_between_points, haversine_distance_km, nm_to_km
from models import AircraftState


@dataclass(frozen=True)
class Airport:
    code: str
    lat: float
    lon: float


@dataclass(frozen=True)
class AirportFilterProfile:
    airport: Airport
    mode: str
    radius_nm: float


@dataclass(frozen=True)
class AirportTrafficMatch:
    airport_code: str
    mode: str
    phase: str
    distance_nm: float
    track_delta_deg: float


AIRPORTS = {
    "EPWA": Airport("EPWA", 52.1658, 20.9675),
    "EPML": Airport("EPML", 50.3223, 21.4621),
    "EPMO": Airport("EPMO", 52.4511, 20.6518),
}


def angle_diff_deg(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def parse_airport_filter_profiles(value: str) -> list[AirportFilterProfile]:
    profiles: list[AirportFilterProfile] = []
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        chunks = [chunk.strip() for chunk in part.split(":")]
        if len(chunks) != 3:
            continue
        code, mode, radius_nm = chunks
        airport = AIRPORTS.get(code.upper())
        if airport is None:
            continue
        mode = mode.lower()
        if mode not in {"strict", "soft"}:
            continue
        try:
            radius = float(radius_nm)
        except ValueError:
            continue
        if radius <= 0:
            continue
        profiles.append(AirportFilterProfile(airport, mode, radius))
    return profiles


def classify_airport_traffic(
    aircraft: AircraftState,
    profiles: list[AirportFilterProfile],
    *,
    bearing_threshold_deg: float = 35.0,
    terminal_altitude_ft: float = 12000.0,
    vertical_rate_threshold_fpm: float = 300.0,
) -> AirportTrafficMatch | None:
    if aircraft.track_deg is None:
        return None
    altitude_ft = aircraft.altitude_ft or 0.0
    vertical_rate = aircraft.vertical_rate_fpm or 0.0
    terminal_profile = altitude_ft <= terminal_altitude_ft

    for profile in profiles:
        airport = profile.airport
        distance_km = haversine_distance_km(aircraft.lat, aircraft.lon, airport.lat, airport.lon)
        distance_nm = distance_km / 1.852
        if distance_km > nm_to_km(profile.radius_nm):
            continue

        to_airport = bearing_between_points(aircraft.lat, aircraft.lon, airport.lat, airport.lon)
        from_airport = bearing_between_points(airport.lat, airport.lon, aircraft.lat, aircraft.lon)
        approach_delta = angle_diff_deg(aircraft.track_deg, to_airport)
        departure_delta = angle_diff_deg(aircraft.track_deg, from_airport)

        if approach_delta <= bearing_threshold_deg and (vertical_rate <= -vertical_rate_threshold_fpm or terminal_profile):
            return AirportTrafficMatch(airport.code, profile.mode, "APP", distance_nm, approach_delta)
        if departure_delta <= bearing_threshold_deg and (vertical_rate >= vertical_rate_threshold_fpm or terminal_profile):
            return AirportTrafficMatch(airport.code, profile.mode, "DEP", distance_nm, departure_delta)

    return None
