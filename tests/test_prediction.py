from datetime import datetime, timedelta, timezone

from models import AircraftState
from prediction import predict_aircraft_path


def test_prediction_linear_motion_east() -> None:
    now = datetime(2026, 6, 17, tzinfo=timezone.utc)
    aircraft = AircraftState("abc123", None, 0.0, 0.0, 30000, 360, 90, 0, None, None, None, now, {})
    points = predict_aircraft_path(aircraft, 0.0, 0.0, horizon_seconds=60, step_seconds=60)
    assert len(points) == 2
    assert 0.09 < points[-1].lon < 0.11
    assert abs(points[-1].lat) < 0.01


def test_prediction_can_fit_motion_from_position_history() -> None:
    now = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    history = []
    for seconds, lon in [(-90, 0.00), (-60, 0.05), (-30, 0.10), (0, 0.15)]:
        history.append(
            AircraftState(
                "abc123", None, 0.0, lon, 30000, 360, 0, 0, None, None, None,
                now + timedelta(seconds=seconds), {},
            )
        )
    aircraft = history[-1]

    points = predict_aircraft_path(
        aircraft,
        0.0,
        0.0,
        horizon_seconds=60,
        step_seconds=60,
        history=history,
        use_history_fit=True,
        fit_window_seconds=90,
        fit_min_points=4,
    )

    assert points[-1].lon > 0.24
    assert abs(points[-1].lat) < 0.01


def test_prediction_falls_back_to_reported_track_with_short_history() -> None:
    now = datetime(2026, 6, 17, tzinfo=timezone.utc)
    aircraft = AircraftState("abc123", None, 0.0, 0.0, 30000, 360, 90, 0, None, None, None, now, {})

    points = predict_aircraft_path(
        aircraft,
        0.0,
        0.0,
        horizon_seconds=60,
        step_seconds=60,
        history=[aircraft],
        use_history_fit=True,
    )

    assert 0.09 < points[-1].lon < 0.11
