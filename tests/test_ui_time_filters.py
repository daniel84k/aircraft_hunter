from datetime import datetime, time, timedelta, timezone

import ui


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
        if tz is None:
            return value.replace(tzinfo=None)
        return value.astimezone(tz)


def test_window_supports_minute_and_hour_ranges(monkeypatch) -> None:
    monkeypatch.setattr(ui, "datetime", FrozenDatetime)

    expected_end = FrozenDatetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    cases = {
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "unknown": timedelta(minutes=30),
    }

    for value, expected_delta in cases.items():
        start, end = ui._window({"range": [value]})
        assert end == expected_end
        assert end - start == expected_delta


def test_window_today_starts_at_local_midnight(monkeypatch) -> None:
    monkeypatch.setattr(ui, "datetime", FrozenDatetime)

    start, end = ui._window({"range": ["today"]})
    local_end = end.astimezone()
    expected_start = datetime.combine(local_end.date(), time.min, tzinfo=local_end.tzinfo).astimezone(timezone.utc)

    assert end == FrozenDatetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    assert start == expected_start
