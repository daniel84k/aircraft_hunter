from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import ui


def test_overview_exposes_operational_analysis(monkeypatch) -> None:
    now = datetime(2026, 6, 23, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("ALERT_MIN_SCORE", "0.70")
    monkeypatch.setattr(ui, "_window", lambda params: (now, now))
    monkeypatch.setattr(
        ui,
        "_validations",
        lambda database_url, params, limit: {"summary": {"total": 0}, "items": []},
    )

    def fake_query(database_url, sql, params=()):
        if "AS geometry_cycles" in sql:
            return [{
                "candidates": 120,
                "alerts": 0,
                "aircraft_analyzed": 34,
                "geometry_cycles": 12,
                "near_alert_events": 1,
            }]
        if "date_bin" in sql:
            return [{"candidate_count": 8}]
        if "WITH event_stats" in sql:
            return [{
                "icao": "abc123",
                "callsign": "LOT123",
                "body": "Sun",
                "score": 0.8,
                "cycle_count": 4,
                "qualifying_cycles": 1,
            }]
        if "GROUP BY rejection_reason" in sql:
            return [{"rejection_reason": "LOW_SCORE", "count": 119}]
        if "ORDER BY started_at DESC" in sql:
            return [{"finished_at": now, "aircraft_count_analyzed": 2}]
        raise AssertionError(f"Unexpected query: {sql}")

    monkeypatch.setattr(ui, "_query", fake_query)

    result = ui._overview("postgresql://test", "/tmp", {"range": ["today"]})

    assert result["alert_min_score"] == 0.7
    assert result["totals"]["geometry_cycles"] == 12
    assert result["top_events"][0]["qualifying_cycles"] == 1
    assert result["rejection_summary"][0]["rejection_reason"] == "LOW_SCORE"
    assert result["latest_run"]["finished_at"] == now


def test_dashboard_navigation_prioritizes_analysis() -> None:
    assert "Codzienna analiza" in ui.INDEX_HTML
    assert "Najlepsze zdarzenia" in ui.INDEX_HTML
    assert "Gdzie odpadają kandydaci" in ui.INDEX_HTML
    assert "collapsible:true" in ui.INDEX_HTML


def test_sky_track_returns_offsets_in_body_diameters(monkeypatch) -> None:
    timestamp = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        ui,
        "get_body_state",
        lambda lat, lon, at, body: SimpleNamespace(
            body="Sun", azimuth_deg=100.0, elevation_deg=20.0, angular_radius_deg=0.25,
        ),
    )
    monkeypatch.setattr(
        ui,
        "topocentric_aircraft_position",
        lambda observer_lat, observer_lon, lat, lon, altitude: (100.5, 20.25, 40.0),
    )

    result = ui._sky_track(
        [{"timestamp": timestamp, "lat": 52.0, "lon": 21.0, "altitude_ft": 30_000}],
        "Sun",
        52.0,
        21.0,
        timestamp,
        window_seconds=60,
    )

    assert len(result) == 1
    assert round(result[0]["vertical"], 3) == 0.5
    assert 0.93 < result[0]["horizontal"] < 0.95


def test_closest_sky_result_interpolates_crossing_between_adsb_samples() -> None:
    timestamp = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    result = ui._closest_sky_result([
        {"timestamp": timestamp, "horizontal": -1.0, "vertical": 0.2},
        {"timestamp": timestamp + timedelta(seconds=10), "horizontal": 1.0, "vertical": 0.2},
    ])

    assert result is not None
    assert result["result"] == "HIT"
    assert round(result["offset_body_diameters"], 3) == 0.2
    assert result["closest_time_utc"] == timestamp + timedelta(seconds=5)
