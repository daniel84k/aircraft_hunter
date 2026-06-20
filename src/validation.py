from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Callable
from zoneinfo import ZoneInfo

from ephemeris import get_body_states
from geo import angular_separation_deg, topocentric_aircraft_position
from models import CelestialBodyState


@dataclass(frozen=True)
class ActualObservation:
    timestamp: datetime
    lat: float
    lon: float
    altitude_ft: float | None


@dataclass(frozen=True)
class ValidationResult:
    result: str
    actual_closest_time_utc: datetime
    actual_offset_body_diameters: float
    actual_separation_deg: float
    vertical_offset_body_diameters: float
    horizontal_offset_body_diameters: float


BodyStateProvider = Callable[[float, float, datetime], list[CelestialBodyState]]


def validate_actual_transit(
    observations: list[ActualObservation],
    *,
    body_name: str,
    observer_lat: float,
    observer_lon: float,
    hit_uncertainty_diameters: float = 0.10,
    interpolation_step_seconds: float = 0.2,
    max_observation_gap_seconds: float = 45.0,
    body_state_provider: BodyStateProvider = get_body_states,
) -> ValidationResult | None:
    """Estimate the closest post-event alignment from bracketing ADS-B samples."""
    ordered = sorted(observations, key=lambda item: item.timestamp)
    best: tuple[float, datetime, float, float, float] | None = None

    for before, after in zip(ordered, ordered[1:]):
        span = (after.timestamp - before.timestamp).total_seconds()
        if span <= 0 or span > max_observation_gap_seconds:
            continue
        if before.altitude_ft is None or after.altitude_ft is None:
            continue

        body_before = _select_body(body_state_provider(observer_lat, observer_lon, before.timestamp), body_name)
        body_after = _select_body(body_state_provider(observer_lat, observer_lon, after.timestamp), body_name)
        if body_before is None or body_after is None:
            continue

        steps = max(1, math.ceil(span / max(0.05, interpolation_step_seconds)))
        for index in range(steps + 1):
            fraction = index / steps
            timestamp = before.timestamp + timedelta(seconds=span * fraction)
            lat = _lerp(before.lat, after.lat, fraction)
            lon = _lerp(before.lon, after.lon, fraction)
            altitude_ft = _lerp(before.altitude_ft, after.altitude_ft, fraction)
            aircraft_azimuth, aircraft_elevation, _range_km = topocentric_aircraft_position(
                observer_lat,
                observer_lon,
                lat,
                lon,
                altitude_ft,
            )
            body_azimuth = _lerp_angle(body_before.azimuth_deg, body_after.azimuth_deg, fraction)
            body_elevation = _lerp(body_before.elevation_deg, body_after.elevation_deg, fraction)
            body_radius = _lerp(body_before.angular_radius_deg, body_after.angular_radius_deg, fraction)
            separation = angular_separation_deg(
                aircraft_azimuth,
                aircraft_elevation,
                body_azimuth,
                body_elevation,
            )
            body_diameter = max(1e-9, 2 * body_radius)
            offset = separation / body_diameter
            vertical_offset = (aircraft_elevation - body_elevation) / body_diameter
            horizontal_delta_deg = (aircraft_azimuth - body_azimuth + 180.0) % 360.0 - 180.0
            horizontal_offset = (
                horizontal_delta_deg * math.cos(math.radians(body_elevation)) / body_diameter
            )
            if best is None or offset < best[0]:
                best = (offset, timestamp, separation, vertical_offset, horizontal_offset)

    if best is None:
        return None

    offset, timestamp, separation, vertical_offset, horizontal_offset = best
    uncertainty = max(0.0, min(0.49, hit_uncertainty_diameters))
    if offset <= 0.5 - uncertainty:
        result = "HIT"
    elif offset >= 0.5 + uncertainty:
        result = "MISS"
    else:
        result = "UNCERTAIN"
    return ValidationResult(
        result,
        timestamp,
        offset,
        separation,
        vertical_offset,
        horizontal_offset,
    )


def format_validation_message(event: dict, result: ValidationResult | None) -> str:
    labels = {
        "HIT": "TRAFIONY ✅",
        "MISS": "CHYBIONY ❌",
        "UNCERTAIN": "NIEPEWNY ⚠️",
        "NO_DATA": "BRAK DANYCH ⚠️",
    }
    result_name = result.result if result else "NO_DATA"
    warsaw = ZoneInfo("Europe/Warsaw")
    body_labels = {"sun": "Słońce", "moon": "Księżyc"}
    predicted = event["transit_time_utc"].astimezone(warsaw).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "===",
        f"WYNIK TRANZYTU: {labels[result_name]}",
        f"Obiekt        : {body_labels.get(event['body'].lower(), event['body'])}",
        f"Samolot       : {event.get('callsign') or '-'} / ICAO {event['icao']}",
        f"Czas prognozy : {predicted}",
        f"Offset prognozy: {event['predicted_offset_body_diameters']:.2f}",
    ]
    if result:
        actual = result.actual_closest_time_utc.astimezone(warsaw).strftime("%Y-%m-%d %H:%M:%S.%f")[:-5]
        timezone_name = result.actual_closest_time_utc.astimezone(warsaw).tzname()
        delta = (result.actual_closest_time_utc - event["transit_time_utc"]).total_seconds()
        vertical_label = "góra ↑" if result.vertical_offset_body_diameters > 0 else "dół ↓"
        horizontal_label = "prawo →" if result.horizontal_offset_body_diameters > 0 else "lewo ←"
        lines.extend(
            [
                f"Czas po ADS-B  : {actual} {timezone_name} ({delta:+.1f} s)",
                f"Offset po ADS-B: {result.actual_offset_body_diameters:.3f} średnicy",
                f"Pion względem tarczy: {vertical_label} {abs(result.vertical_offset_body_diameters):.3f}",
                f"Poziom względem tarczy: {horizontal_label} {abs(result.horizontal_offset_body_diameters):.3f}",
                f"Separacja kątowa: {result.actual_separation_deg:.4f}°",
            ]
        )
        if event.get("observation_count") is not None:
            lines.append(f"Próbki ADS-B   : {event['observation_count']}")
    else:
        lines.append("Brak pozycji ADS-B pozwalających ocenić zdarzenie.")
    lines.extend(["Metoda: interpolacja pozycji ADS-B po zdarzeniu", "==="])
    return "\n".join(lines)


def _select_body(states: list[CelestialBodyState], body_name: str) -> CelestialBodyState | None:
    return next((state for state in states if state.body.lower() == body_name.lower()), None)


def _lerp(start: float, end: float, fraction: float) -> float:
    return start + (end - start) * fraction


def _lerp_angle(start: float, end: float, fraction: float) -> float:
    delta = (end - start + 180.0) % 360.0 - 180.0
    return (start + delta * fraction) % 360.0
