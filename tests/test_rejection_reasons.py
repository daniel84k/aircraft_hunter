from dataclasses import replace
from datetime import datetime, timedelta, timezone

from config import load_settings
from models import AircraftState, TransitCandidate
from main import REJECTION_REASONS
from main import classify_candidate, notification_event_key, notification_sort_key, reachable_relocation_km


def test_rejection_reasons_include_required_values() -> None:
    assert REJECTION_REASONS == {
        "LOW_SCORE",
        "TOO_LATE",
        "OBSERVER_POINT_TOO_FAR",
        "UNSTABLE_TRACK",
        "HIGH_VERTICAL_RATE",
        "LOW_ALTITUDE",
        "BODY_TOO_LOW",
        "OFFSET_TOO_LARGE",
        "NEAR_ORIGIN_AIRPORT",
        "NEAR_DESTINATION_AIRPORT",
        "DUPLICATE_ALERT",
        "INSUFFICIENT_DATA",
    }


def _candidate(settings, *, score=0.2, separation=0.45, distance=6.0, body_elevation=3.0):
    now = datetime.now(timezone.utc)
    aircraft = AircraftState("abc123", "TEST1", 52.0, 21.0, 30000, 400, 90, 0, None, None, None, now, {})
    return TransitCandidate(
        aircraft=aircraft,
        body="Moon",
        transit_time_utc=now + timedelta(seconds=settings.min_lead_time_seconds + 60),
        observer_lat=52.0,
        observer_lon=21.0,
        observer_distance_km=distance,
        google_maps_url="https://maps.google.com/?q=52,21",
        google_nav_url="https://www.google.com/maps/dir/?api=1&destination=52,21",
        angular_separation_deg=separation,
        body_radius_deg=0.2725,
        offset_body_diameters=separation / (2 * 0.2725),
        score=score,
        confidence=0.5,
        aircraft_range_km=90.0,
        aircraft_altitude_ft=30000.0,
        body_elevation_deg=body_elevation,
        status="CANDIDATE_STORED",
        rejection_reason=None,
        dedupe_key="abc123:moon:test",
    )


def test_reachable_relocation_uses_travel_time_and_cap() -> None:
    settings = replace(load_settings(), max_observer_relocation_km=12, travel_speed_kmh=50, reach_safety=0.8)

    assert round(reachable_relocation_km(settings, 600), 2) == 6.67
    assert reachable_relocation_km(settings, 1800) == 12


def test_low_score_near_candidate_becomes_observation_candidate() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.8,
        max_observer_relocation_km=12,
        travel_speed_kmh=50,
        reach_safety=0.8,
        observation_candidate_max_separation_deg=0.5,
        min_body_elevation_deg_for_candidate=0,
    )

    candidate = classify_candidate(_candidate(settings), settings, stable=True)

    assert candidate.status == "OBSERVATION_CANDIDATE"
    assert candidate.rejection_reason == "LOW_SCORE"


def test_notification_prefers_nearest_point_for_same_event() -> None:
    settings = load_settings()
    farther = _candidate(settings, score=0.8, separation=0.05, distance=1.5, body_elevation=20)
    farther.status = "ALERT_READY"
    nearer = _candidate(settings, score=0.8, separation=0.08, distance=0.4, body_elevation=20)
    nearer.status = "ALERT_READY"

    ordered = sorted([farther, nearer], key=notification_sort_key)

    assert ordered[0] is nearer
    assert notification_event_key(farther) == notification_event_key(nearer)


def test_notification_event_key_uses_wide_event_window() -> None:
    settings = load_settings()
    first = _candidate(settings, score=0.8, separation=0.05, distance=1.0, body_elevation=20)
    second = _candidate(settings, score=0.8, separation=0.05, distance=1.0, body_elevation=20)
    first.transit_time_utc = datetime(2026, 6, 18, 19, 28, 50, tzinfo=timezone.utc)
    second.transit_time_utc = datetime(2026, 6, 18, 19, 29, 5, tzinfo=timezone.utc)

    assert notification_event_key(first) == notification_event_key(second)
