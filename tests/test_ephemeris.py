from datetime import datetime, timedelta, timezone

import pytest

from ephemeris import get_body_states, get_body_states_many


def test_batch_ephemeris_matches_scalar_calculation() -> None:
    start = datetime(2026, 6, 23, 14, 30, 0, 123456, tzinfo=timezone.utc)
    timestamps = [start + timedelta(seconds=offset) for offset in (0, 1, 17, 600)]

    batched = get_body_states_many(52.19359, 20.42513, timestamps)

    assert list(batched) == timestamps
    for timestamp in timestamps:
        scalar = get_body_states(52.19359, 20.42513, timestamp)
        vector = batched[timestamp]
        assert [state.body for state in vector] == [state.body for state in scalar]
        for actual, expected in zip(vector, scalar):
            assert actual.timestamp == expected.timestamp
            assert actual.azimuth_deg == pytest.approx(expected.azimuth_deg, abs=1e-10)
            assert actual.elevation_deg == pytest.approx(expected.elevation_deg, abs=1e-10)
            assert actual.angular_radius_deg == expected.angular_radius_deg
            assert actual.illumination == pytest.approx(expected.illumination, abs=1e-12)
