from datetime import datetime, timezone

from models import AircraftState
from prediction import predict_aircraft_path


def test_prediction_linear_motion_east() -> None:
    now = datetime(2026, 6, 17, tzinfo=timezone.utc)
    aircraft = AircraftState("abc123", None, 0.0, 0.0, 30000, 360, 90, 0, None, None, None, now, {})
    points = predict_aircraft_path(aircraft, 0.0, 0.0, horizon_seconds=60, step_seconds=60)
    assert len(points) == 2
    assert 0.09 < points[-1].lon < 0.11
    assert abs(points[-1].lat) < 0.01
