from datetime import datetime, timedelta, timezone

from models import AircraftState, TransitCandidate
from telegram import SentCandidate, TelegramNotifier


def _candidate(*, status="OBSERVATION_CANDIDATE", distance=1.0, offset=0.2):
    now = datetime.now(timezone.utc)
    aircraft = AircraftState("abc123", "TEST1", 52.0, 21.0, 30000, 400, 90, 0, None, None, None, now, {})
    return TransitCandidate(
        aircraft=aircraft,
        body="Sun",
        transit_time_utc=now + timedelta(minutes=15),
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
