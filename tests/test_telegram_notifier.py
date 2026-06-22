from datetime import datetime, timedelta, timezone

from models import AircraftState, TransitCandidate
from telegram import SentCandidate, TelegramNotifier


def _candidate(*, status="OBSERVATION_CANDIDATE", distance=1.0, offset=0.2, transit_time=None):
    now = datetime.now(timezone.utc)
    aircraft = AircraftState("abc123", "TEST1", 52.0, 21.0, 30000, 400, 90, 0, None, None, None, now, {})
    return TransitCandidate(
        aircraft=aircraft,
        body="Sun",
        transit_time_utc=transit_time or now + timedelta(minutes=15),
        observer_lat=52.0,
        observer_lon=21.0,
        observer_distance_km=distance,
        google_maps_url="https://maps.google.com/?q=52,21",
        google_nav_url="https://www.google.com/maps/dir/?api=1&destination=52,21",
        angular_separation_deg=offset * 0.5332,
        body_radius_deg=0.2666,
        offset_body_diameters=offset,
        score=0.5,
        confidence=0.5,
        aircraft_range_km=80,
        aircraft_altitude_ft=30000,
        body_elevation_deg=20,
        status=status,
        rejection_reason=None,
        dedupe_key="abc123:sun:test",
    )


def test_notifier_updates_when_point_becomes_much_closer() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    previous = SentCandidate(0, "OBSERVATION_CANDIDATE", 1.0, 0.2)
    candidate = _candidate(distance=0.4, offset=0.2)

    assert notifier._should_update(previous, candidate, 300, 900, 180, 0.5, 0.3)


def test_notifier_does_not_update_for_minor_improvement() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    previous = SentCandidate(0, "ALERT_READY", 0.5, 0.08)
    candidate = _candidate(status="ALERT_READY", distance=0.55, offset=0.07)

    assert not notifier._should_update(previous, candidate, 300, 900, 180, 0.5, 0.3)


def test_notifier_updates_when_candidate_becomes_alert() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    previous = SentCandidate(0, "OBSERVATION_CANDIDATE", 0.5, 0.2)
    candidate = _candidate(status="ALERT_READY", distance=0.5, offset=0.2)

    assert notifier._should_update(previous, candidate, 300, 900, 180, 0.5, 0.3)


def test_notifier_confirms_early_forecast_without_waiting_for_cooldown() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    previous = SentCandidate(290, "EARLY", 0.5, 0.1)
    candidate = _candidate(status="ALERT_READY", distance=0.5, offset=0.1)

    assert notifier._should_update(previous, candidate, 300, 900, 180, 0.5, 0.3, "CONFIRMED")


def test_notifier_suppresses_same_aircraft_body_within_event_window() -> None:
    notifier = TelegramNotifier(token="", chat_id="")
    base_time = datetime(2026, 6, 18, 19, 28, 50, tzinfo=timezone.utc)
    first = _candidate(transit_time=base_time, distance=6.0, offset=0.06)
    second = _candidate(transit_time=base_time + timedelta(seconds=15), distance=7.5, offset=0.27)

    assert notifier.send_candidate(first, "first", 900, 180, 0.5, 0.3, 600)
    assert not notifier.send_candidate(second, "second", 900, 180, 0.5, 0.3, 600)
