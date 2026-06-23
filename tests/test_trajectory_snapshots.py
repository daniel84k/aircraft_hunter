from datetime import datetime, timedelta, timezone

from models import PredictedPoint
from storage import serialize_prediction_path


def _point(timestamp: datetime, index: int) -> PredictedPoint:
    return PredictedPoint(
        timestamp=timestamp,
        lat=52.0 + index / 1000,
        lon=21.0 + index / 1000,
        altitude_ft=30_000 + index,
        azimuth_deg=100.0,
        elevation_deg=20.0,
        range_km=40.0,
    )


def test_trajectory_snapshot_is_downsampled_and_keeps_endpoints() -> None:
    start = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    path = [_point(start + timedelta(seconds=index), index) for index in range(13)]

    payload = serialize_prediction_path(path, sample_interval_seconds=5)

    assert payload["version"] == 1
    assert [row[0] for row in payload["points"]] == [
        start.timestamp(),
        (start + timedelta(seconds=5)).timestamp(),
        (start + timedelta(seconds=10)).timestamp(),
        (start + timedelta(seconds=12)).timestamp(),
    ]
    assert payload["points"][0][1:] == [52.0, 21.0, 30_000]
    assert payload["points"][-1][1:] == [52.012, 21.012, 30_012]


def test_empty_trajectory_snapshot_has_versioned_shape() -> None:
    assert serialize_prediction_path([]) == {"version": 1, "points": []}
