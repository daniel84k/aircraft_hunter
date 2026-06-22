from dataclasses import replace
from datetime import datetime, timedelta, timezone

from config import load_settings
from airport_filter import AirportTrafficMatch
from models import AircraftState, TransitCandidate
from main import REJECTION_REASONS
from main import candidate_notification_phase, classify_candidate, is_better_notification, notification_event_key, notification_sort_key, reachable_relocation_km, suppress_airport_traffic_alert, update_candidate_convergence


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
        "TOO_EARLY_FOR_ALERT",
        "NEAR_ORIGIN_AIRPORT",
        "NEAR_DESTINATION_AIRPORT",
        "NEAR_EPWA_APPROACH",
        "NEAR_EPWA_DEPARTURE",
        "NEAR_EPMO_APPROACH",
        "NEAR_EPMO_DEPARTURE",
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
        observation_candidate_min_score=0.1,
        observation_candidate_max_lead_seconds=1200,
        min_body_elevation_deg_for_candidate=0,
    )

    candidate = classify_candidate(_candidate(settings), settings, stable=True)

    assert candidate.status == "OBSERVATION_CANDIDATE"
    assert candidate.rejection_reason == "LOW_SCORE"


def test_high_score_candidate_waits_until_ready_window() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.8,
        alert_ready_lead_time_seconds=300,
        max_observer_relocation_km=12,
        travel_speed_kmh=50,
        reach_safety=0.8,
        observation_candidate_max_separation_deg=0.5,
        observation_candidate_min_score=0.5,
        observation_candidate_max_lead_seconds=1200,
        min_body_elevation_deg_for_candidate=0,
    )

    candidate = classify_candidate(
        _candidate(settings, score=0.95, separation=0.01, distance=1.0, body_elevation=20),
        settings,
        stable=True,
    )

    assert candidate.status == "OBSERVATION_CANDIDATE"
    assert candidate.rejection_reason == "TOO_EARLY_FOR_ALERT"


def test_high_score_candidate_becomes_ready_inside_ready_window() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.8,
        alert_ready_lead_time_seconds=300,
        max_observer_relocation_km=12,
        travel_speed_kmh=50,
        reach_safety=0.8,
        observation_candidate_max_separation_deg=0.5,
        observation_candidate_min_score=0.5,
        min_body_elevation_deg_for_candidate=0,
    )
    candidate = _candidate(settings, score=0.95, separation=0.01, distance=1.0, body_elevation=20)
    candidate.transit_time_utc = datetime.now(timezone.utc) + timedelta(seconds=240)

    candidate = classify_candidate(candidate, settings, stable=True)

    assert candidate.status == "ALERT_READY"
    assert candidate.rejection_reason is None


def test_low_score_below_candidate_floor_is_rejected() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.8,
        max_observer_relocation_km=12,
        travel_speed_kmh=50,
        reach_safety=0.8,
        observation_candidate_max_separation_deg=0.5,
        observation_candidate_min_score=0.5,
        min_body_elevation_deg_for_candidate=0,
    )

    candidate = classify_candidate(
        _candidate(settings, score=0.4, separation=0.12, distance=3.0, body_elevation=20),
        settings,
        stable=True,
    )

    assert candidate.status == "REJECTED"
    assert candidate.rejection_reason == "LOW_SCORE"


def test_observation_candidate_beyond_notification_horizon_is_rejected() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.8,
        observation_candidate_min_score=0.5,
        observation_candidate_max_lead_seconds=600,
        observation_candidate_max_separation_deg=0.5,
        min_body_elevation_deg_for_candidate=0,
    )
    candidate = _candidate(settings, score=0.7, separation=0.05, distance=1.0, body_elevation=20)
    candidate.transit_time_utc = datetime.now(timezone.utc) + timedelta(seconds=900)

    candidate = classify_candidate(candidate, settings, stable=True)

    assert candidate.status != "OBSERVATION_CANDIDATE"


def test_notification_requires_three_converged_cycles() -> None:
    settings = replace(
        load_settings(),
        notification_consecutive_cycles=3,
        notification_max_time_shift_seconds=5,
        notification_max_observer_shift_km=0.5,
        notification_max_offset_worsening_diameters=0.05,
    )
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    candidate = _candidate(settings, score=0.8, separation=0.05, distance=1.0, body_elevation=20)
    candidate.transit_time_utc = now + timedelta(minutes=5)
    tracker = {}

    ready1, count1, _ = update_candidate_convergence(candidate, tracker, settings, now)
    candidate.transit_time_utc += timedelta(seconds=2)
    candidate.observer_lat += 0.0002
    ready2, count2, _ = update_candidate_convergence(candidate, tracker, settings, now + timedelta(seconds=10))
    candidate.transit_time_utc -= timedelta(seconds=1)
    ready3, count3, _ = update_candidate_convergence(candidate, tracker, settings, now + timedelta(seconds=20))

    assert (ready1, count1) == (False, 1)
    assert (ready2, count2) == (False, 2)
    assert (ready3, count3) == (True, 3)


def test_notification_convergence_resets_when_time_moves() -> None:
    settings = replace(load_settings(), notification_consecutive_cycles=2, notification_max_time_shift_seconds=5)
    now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    candidate = _candidate(settings, score=0.8, separation=0.05, distance=1.0, body_elevation=20)
    candidate.transit_time_utc = now + timedelta(minutes=5)
    tracker = {}
    update_candidate_convergence(candidate, tracker, settings, now)
    candidate.transit_time_utc += timedelta(seconds=12)

    ready, count, reason = update_candidate_convergence(candidate, tracker, settings, now + timedelta(seconds=10))

    assert ready is False
    assert count == 1
    assert reason == "TRANSIT_TIME_MOVED"


def test_notification_uses_early_then_confirmed_phase() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.7,
        early_notification_consecutive_cycles=2,
        notification_consecutive_cycles=3,
    )
    candidate = _candidate(settings, score=0.8, separation=0.05, distance=1.0, body_elevation=20)
    candidate.status = "ALERT_READY"

    assert candidate_notification_phase(candidate, 1, settings) is None
    assert candidate_notification_phase(candidate, 2, settings) == "EARLY"
    assert candidate_notification_phase(candidate, 3, settings) == "CONFIRMED"


def test_low_score_observation_candidate_does_not_send_early() -> None:
    settings = replace(
        load_settings(),
        alert_min_score=0.7,
        early_notification_consecutive_cycles=2,
    )
    candidate = _candidate(settings, score=0.65, separation=0.05, distance=1.0, body_elevation=20)
    candidate.status = "OBSERVATION_CANDIDATE"

    assert candidate_notification_phase(candidate, 3, settings) is None


def test_strict_airport_traffic_is_stored_without_notification() -> None:
    settings = replace(load_settings(), alert_min_score=0.8, observation_candidate_max_lead_seconds=1200)
    candidate = _candidate(settings, score=0.95, separation=0.01, distance=1.0, body_elevation=20)
    candidate = classify_candidate(candidate, settings, stable=True)

    suppressed = suppress_airport_traffic_alert(candidate, AirportTrafficMatch("EPWA", "strict", "APP", 24.0, 5.0))

    assert suppressed.status == "CANDIDATE_STORED"
    assert suppressed.rejection_reason == "NEAR_EPWA_APPROACH"


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


def test_better_notification_allows_large_offset_improvement() -> None:
    settings = load_settings()
    candidate = _candidate(settings, score=0.8, separation=0.078 * 2 * 0.2725, distance=0.25, body_elevation=20)
    previous_event = {"best_distance_km": 0.5, "best_offset_body_diameters": 0.113}

    assert is_better_notification(
        candidate,
        previous_event,
        min_distance_improvement_km=0.5,
        min_offset_improvement_ratio=0.30,
    )
