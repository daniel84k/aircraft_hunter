from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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
    warsaw = ZoneInfo("Europe/Warsaw")
    local_end = end.astimezone(warsaw)
    expected_start = datetime.combine(local_end.date(), time.min, tzinfo=warsaw).astimezone(timezone.utc)

    assert end == FrozenDatetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    assert start == expected_start


def test_window_supports_historical_warsaw_date(monkeypatch) -> None:
    monkeypatch.setattr(ui, "datetime", FrozenDatetime)

    start, end = ui._window({"range": ["date:2026-01-15"]})

    assert start == datetime(2026, 1, 14, 23, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
