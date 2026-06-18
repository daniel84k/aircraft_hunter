from datetime import datetime, timezone

from models import AircraftState, CelestialBodyState, PredictedPoint
from transit_detector import detect_transit_candidates


def test_detects_candidate_inside_body_margin() -> None:
    ts = datetime(2026, 6, 17, tzinfo=timezone.utc)
    aircraft = AircraftState("abc123", None, 52, 21, 30000, 400, 90, 0, None, None, None, ts, {})
    point = PredictedPoint(ts, 52, 21, 30000, 100.0, 30.0, 20.0)
    body = CelestialBodyState("Moon", ts, 100.1, 30.0, 0.2725, 0.5)
    candidates = detect_transit_candidates(aircraft, [point], {ts: [body]})
    assert len(candidates) == 1
    assert candidates[0][4] < 0.25


def test_detects_candidate_reachable_by_observer_relocation() -> None:
    ts = datetime(2026, 6, 17, tzinfo=timezone.utc)
    aircraft = AircraftState("abc123", None, 52, 21, 30000, 400, 90, 0, None, None, None, ts, {})
    point = PredictedPoint(ts, 52, 21, 30000, 100.0, 30.0, 100.0)
    body = CelestialBodyState("Moon", ts, 102.0, 30.0, 0.2725, 0.5)

    assert detect_transit_candidates(aircraft, [point], {ts: [body]}) == []

    candidates = detect_transit_candidates(
        aircraft,
        [point],
        {ts: [body]},
        max_observer_relocation_km=5.0,
    )

    assert len(candidates) == 1
