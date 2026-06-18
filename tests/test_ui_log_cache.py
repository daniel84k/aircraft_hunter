from datetime import datetime, timedelta, timezone

import ui


def _line(ts: str, event: str, aircraft: str = "abc123") -> str:
    return (
        f"{ts} | INFO | cycle=1 step=3/5 {event} aircraft={aircraft} "
        "closest_body=Sun closest_offset_diameters=0.5\n"
    )


def test_parse_log_events_appends_new_log_data(tmp_path) -> None:
    log_dir = tmp_path
    path = log_dir / "aircraft-transit-test.log"
    path.write_text(
        _line("2026-06-18 10:00:00.000+0000", "GEOMETRY_SELECTED", "abc123"),
        encoding="utf-8",
    )
    ui._LOG_EVENTS_CACHE.clear()

    start = datetime(2026, 6, 18, 9, 59, tzinfo=timezone.utc)
    end = datetime(2026, 6, 18, 10, 5, tzinfo=timezone.utc)
    first = ui._parse_log_events(str(log_dir), start, end)

    with path.open("a", encoding="utf-8") as fh:
        fh.write(_line("2026-06-18 10:01:00.000+0000", "GEOMETRY_SKIPPED", "def456"))

    second = ui._parse_log_events(str(log_dir), start, end)

    assert [event["aircraft"] for event in first] == ["abc123"]
    assert [event["aircraft"] for event in second] == ["abc123", "def456"]


def test_parse_log_events_recovers_after_log_truncation(tmp_path) -> None:
    log_dir = tmp_path
    path = log_dir / "aircraft-transit-test.log"
    path.write_text(
        _line("2026-06-18 10:00:00.000+0000", "GEOMETRY_SELECTED", "abc123")
        + _line("2026-06-18 10:01:00.000+0000", "GEOMETRY_SKIPPED", "def456"),
        encoding="utf-8",
    )
    ui._LOG_EVENTS_CACHE.clear()

    start = datetime(2026, 6, 18, 9, 59, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)
    assert len(ui._parse_log_events(str(log_dir), start, end)) == 2

    path.write_text(
        _line("2026-06-18 10:02:00.000+0000", "GEOMETRY_NO_ALIGNMENT", "ghi789"),
        encoding="utf-8",
    )

    events = ui._parse_log_events(str(log_dir), start, end)

    assert [event["aircraft"] for event in events] == ["ghi789"]


def test_map_aircraft_includes_visibility_skipped(tmp_path) -> None:
    log_dir = tmp_path
    path = log_dir / "aircraft-transit-test.log"
    path.write_text(
        _line("2026-06-18 10:00:00.000+0000", "VISIBILITY_SKIPPED", "far123")
        + _line("2026-06-18 10:00:01.000+0000", "GEOMETRY_SELECTED", "geo456"),
        encoding="utf-8",
    )
    ui._LOG_EVENTS_CACHE.clear()

    start = datetime(2026, 6, 18, 9, 59, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)

    aircraft = ui._latest_analyzed_aircraft_from_logs(str(log_dir), start, end)

    assert aircraft == [
        {"icao": "far123", "event": "VISIBILITY_SKIPPED", "reason": None, "body": "Sun"},
        {"icao": "geo456", "event": "GEOMETRY_SELECTED", "reason": None, "body": "Sun"},
    ]
