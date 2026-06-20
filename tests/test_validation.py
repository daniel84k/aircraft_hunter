from datetime import datetime, timedelta, timezone

import pytest

import validation
from models import CelestialBodyState
from validation import ActualObservation, format_validation_message, validate_actual_transit


BASE = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)


def _body_states(_lat, _lon, timestamp):
    return [CelestialBodyState("Sun", timestamp, 0.0, 0.0, 0.25)]


def _observations(start_azimuth: float, end_azimuth: float) -> list[ActualObservation]:
    return [
        ActualObservation(BASE, start_azimuth, 0.0, 30000),
        ActualObservation(BASE + timedelta(seconds=10), end_azimuth, 0.0, 30000),
    ]


@pytest.fixture(autouse=True)
def aircraft_azimuth_follows_lat(monkeypatch):
    monkeypatch.setattr(
        validation,
        "topocentric_aircraft_position",
        lambda _observer_lat, _observer_lon, aircraft_lat, _aircraft_lon, _altitude: (
            aircraft_lat,
            0.0,
            20.0,
        ),
    )


def test_validation_marks_track_crossing_body_as_hit() -> None:
    result = validate_actual_transit(
        _observations(-1.0, 1.0),
        body_name="Sun",
        observer_lat=52.0,
        observer_lon=21.0,
        body_state_provider=_body_states,
    )

    assert result is not None
    assert result.result == "HIT"
    assert result.actual_offset_body_diameters == pytest.approx(0.0, abs=1e-6)
    assert result.actual_closest_time_utc == BASE + timedelta(seconds=5)
    assert result.vertical_offset_body_diameters == pytest.approx(0.0, abs=1e-6)
    assert result.horizontal_offset_body_diameters == pytest.approx(0.0, abs=1e-6)


def test_validation_marks_clear_miss() -> None:
    result = validate_actual_transit(
        _observations(0.4, 0.6),
        body_name="Sun",
        observer_lat=52.0,
        observer_lon=21.0,
        body_state_provider=_body_states,
    )

    assert result is not None
    assert result.result == "MISS"
    assert result.actual_offset_body_diameters == pytest.approx(0.8, rel=1e-3)


def test_validation_marks_limb_result_as_uncertain() -> None:
    result = validate_actual_transit(
        _observations(0.25, 0.4),
        body_name="Sun",
        observer_lat=52.0,
        observer_lon=21.0,
        body_state_provider=_body_states,
    )

    assert result is not None
    assert result.result == "UNCERTAIN"
    assert result.actual_offset_body_diameters == pytest.approx(0.5, rel=1e-3)


def test_validation_requires_bracketing_observations() -> None:
    result = validate_actual_transit(
        [ActualObservation(BASE, 0.0, 0.0, 30000)],
        body_name="Sun",
        observer_lat=52.0,
        observer_lon=21.0,
        body_state_provider=_body_states,
    )

    assert result is None


def test_validation_message_contains_polish_result() -> None:
    result = validate_actual_transit(
        _observations(-1.0, 1.0),
        body_name="Sun",
        observer_lat=52.0,
        observer_lon=21.0,
        body_state_provider=_body_states,
    )
    event = {
        "icao": "abc123",
        "callsign": "TEST1",
        "body": "Sun",
        "transit_time_utc": BASE + timedelta(seconds=5),
        "predicted_offset_body_diameters": 0.1,
    }

    message = format_validation_message(event, result)

    assert "WYNIK TRANZYTU: TRAFIONY" in message
    assert "Offset po ADS-B: 0.000 średnicy" in message
    assert "Pion względem tarczy: dół ↓ 0.000" in message
    assert "Poziom względem tarczy: lewo ← 0.000" in message


def test_validation_reports_vertical_and_horizontal_miss_direction(monkeypatch) -> None:
    monkeypatch.setattr(
        validation,
        "topocentric_aircraft_position",
        lambda _observer_lat, _observer_lon, aircraft_lat, aircraft_lon, _altitude: (
            aircraft_lat,
            aircraft_lon,
            20.0,
        ),
    )
    observations = [
        ActualObservation(BASE, 0.5, 0.5, 30000),
        ActualObservation(BASE + timedelta(seconds=10), 0.5, 0.5, 30000),
    ]

    result = validate_actual_transit(
        observations,
        body_name="Sun",
        observer_lat=52.0,
        observer_lon=21.0,
        body_state_provider=_body_states,
    )

    assert result is not None
    assert result.vertical_offset_body_diameters == pytest.approx(1.0)
    assert result.horizontal_offset_body_diameters == pytest.approx(1.0)

    event = {
        "icao": "abc123",
        "callsign": "TEST1",
        "body": "Sun",
        "transit_time_utc": BASE,
        "predicted_offset_body_diameters": 0.1,
        "observation_count": 2,
    }
    message = format_validation_message(event, result)
    assert "Pion względem tarczy: góra ↑ 1.000" in message
    assert "Poziom względem tarczy: prawo → 1.000" in message
    assert "Próbki ADS-B   : 2" in message
