from datetime import datetime, timezone

from airport_filter import classify_airport_traffic, parse_airport_filter_profiles
from models import AircraftState


def _aircraft(*, lat, lon, track, altitude=6000, vertical_rate=-800):
    return AircraftState(
        "abc123",
        "TEST1",
        lat,
        lon,
        altitude,
        250,
        track,
        vertical_rate,
        "A320",
        None,
        None,
        datetime.now(timezone.utc),
        {},
    )


def test_epwa_strict_approach_is_matched() -> None:
    profiles = parse_airport_filter_profiles("EPWA:strict:20,EPMO:soft:12")
    aircraft = _aircraft(lat=52.30, lon=20.97, track=180, vertical_rate=-900)

    match = classify_airport_traffic(aircraft, profiles)

    assert match is not None
    assert match.airport_code == "EPWA"
    assert match.mode == "strict"
    assert match.phase == "APP"


def test_epwa_overflight_is_not_terminal_traffic() -> None:
    profiles = parse_airport_filter_profiles("EPWA:strict:20")
    aircraft = _aircraft(lat=52.30, lon=20.97, track=180, altitude=35000, vertical_rate=0)

    assert classify_airport_traffic(aircraft, profiles) is None


def test_epmo_soft_departure_is_matched_without_strict_mode() -> None:
    profiles = parse_airport_filter_profiles("EPMO:soft:12")
    aircraft = _aircraft(lat=52.50, lon=20.65, track=0, altitude=3000, vertical_rate=700)

    match = classify_airport_traffic(aircraft, profiles)

    assert match is not None
    assert match.airport_code == "EPMO"
    assert match.mode == "soft"
    assert match.phase == "DEP"
