from datetime import datetime, timedelta, timezone

from alerts import flightradar_url, format_alert
from models import AircraftState, TransitCandidate


def _candidate(callsign="QTR17Q"):
    now = datetime.now(timezone.utc)
    aircraft = AircraftState("abc123", callsign, 52.0, 21.0, 30000, 400, 90, 0, "A359", None, None, now, {})
    return TransitCandidate(
        aircraft=aircraft,
        body="Sun",
        transit_time_utc=now + timedelta(minutes=15),
        observer_lat=52.0,
        observer_lon=21.0,
        observer_distance_km=1.0,
        google_maps_url="https://maps.google.com/?q=52,21",
        google_nav_url="https://www.google.com/maps/dir/?api=1&destination=52,21",
        angular_separation_deg=0.05,
        body_radius_deg=0.2666,
        offset_body_diameters=0.1,
        score=0.8,
        confidence=0.7,
        aircraft_range_km=80,
        aircraft_altitude_ft=30000,
        body_elevation_deg=20,
        status="ALERT_READY",
        rejection_reason=None,
        dedupe_key="abc123:sun:test",
    )


def test_flightradar_url_uses_callsign() -> None:
    assert flightradar_url(_candidate()) == "https://www.flightradar24.com/QTR17Q"


def test_format_alert_includes_flightradar_link() -> None:
    message = format_alert(_candidate())

    assert "Flightradar24 : https://www.flightradar24.com/QTR17Q" in message


def test_format_alert_labels_warsaw_time() -> None:
    message = format_alert(_candidate())

    assert "Transit Warsaw:" in message


def test_format_alert_can_label_better_update() -> None:
    message = format_alert(_candidate(), better=True)

    assert "BETTER TRANSIT ALERT" in message
