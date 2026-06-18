from datetime import datetime, timezone

from alerts import dedupe_key, rounded_transit_time


def test_dedupe_key_rounds_time_and_location() -> None:
    ts1 = datetime(2026, 6, 17, 20, 14, 31, tzinfo=timezone.utc)
    ts2 = datetime(2026, 6, 17, 20, 14, 34, tzinfo=timezone.utc)
    key1 = dedupe_key("ABC123", "Moon", ts1, 52.184321, 21.012345)
    key2 = dedupe_key("abc123", "moon", ts2, 52.184349, 21.012390)
    assert key1 == key2


def test_rounded_transit_time_to_ten_seconds() -> None:
    ts = datetime(2026, 6, 17, 20, 14, 34, tzinfo=timezone.utc)
    assert rounded_transit_time(ts).second == 30
