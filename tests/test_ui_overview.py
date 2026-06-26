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


def test_dashboard_filter_funnel_is_clickable() -> None:
    assert "setFunnelFocus('status'" in ui.INDEX_HTML
    assert "showTab('logs')" in ui.INDEX_HTML


def test_dashboard_marks_unsent_alert_ready_as_waiting_for_confirmation() -> None:
    assert "Czeka na potwierdzenie" in ui.INDEX_HTML
    assert "notification_block_reason" in ui.INDEX_HTML


def test_alert_dashboard_focuses_on_stage_time_and_result() -> None:
    assert "Historia powiadomień" in ui.INDEX_HTML
    assert "Czas na reakcję" in ui.INDEX_HTML
    assert "po dojeździe" in ui.INDEX_HTML
    assert "validation_result" in ui.INDEX_HTML


def test_validation_dashboard_explains_hit_and_miss() -> None:
    assert "TRAFIONY" in ui.INDEX_HTML
    assert "CHYBIONY" in ui.INDEX_HTML
    assert "Minięcie głównie" in ui.INDEX_HTML
    assert "Prognoza → ADS-B" in ui.INDEX_HTML
    assert "Pełna analiza" in ui.INDEX_HTML


def test_mobile_dashboard_uses_labeled_cards_and_full_screen_dialog() -> None:
    assert 'data-label="${esc(h.name)}"' in ui.INDEX_HTML
    assert "table,tbody,tr,td{display:block" in ui.INDEX_HTML
    assert "height:100dvh" in ui.INDEX_HTML
    assert ".life-cycle-rail{grid-template-columns:1fr" in ui.INDEX_HTML


def test_alerts_expose_travel_margin_and_validation(monkeypatch) -> None:
    sent_at = datetime(2026, 6, 23, 14, 43, 46, tzinfo=timezone.utc)
    monkeypatch.setattr(ui, "_window", lambda params: (sent_at, sent_at))
    monkeypatch.setenv("TRAVEL_SPEED_KMH", "50")
    monkeypatch.setenv("REACH_SAFETY", "0.8")
    monkeypatch.setattr(
        ui,
        "_query",
        lambda database_url, sql, params=(): [{
            "alert_id": 1,
            "alert_type": "EARLY",
            "printed_at": sent_at,
            "candidate_id": 7,
            "icao": "89630c",
            "callsign": "UAE158",
            "body": "Moon",
            "score": 0.8,
            "transit_time_utc": sent_at + timedelta(seconds=209),
            "lead_seconds": 209,
            "predicted_offset_body_diameters": 0.097,
            "observer_distance_km": 1.25,
            "google_maps_url": "https://maps.example/point",
            "event_slot": 123,
            "validation_result": "MISS",
            "actual_offset_body_diameters": 1.368,
            "time_error_seconds": 4.8,
            "validated_at": sent_at + timedelta(minutes=5),
        }],
    )

    result = ui._alerts("postgresql://test", {"range": ["today"]})

    assert result["summary"] == {
        "alerts": 1,
        "events": 1,
        "early": 1,
        "confirmed": 0,
        "better": 0,
        "hit": 0,
        "miss": 1,
        "avg_lead_seconds": 209.0,
    }
    assert result["items"][0]["travel_seconds"] == 112.5
    assert result["items"][0]["preparation_margin_seconds"] == 96.5


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


def test_event_detail_exposes_confirmation_threshold(monkeypatch) -> None:
    transit_time = datetime(2026, 6, 23, 10, 15, tzinfo=timezone.utc)
    created_at = transit_time - timedelta(minutes=4)
    candidate_row = {
        "id": 7,
        "prediction_run_id": 3,
        "icao": "abc123",
        "callsign": "LOT123",
        "aircraft_type": "A320",
        "body": "sun",
        "transit_time_utc": transit_time,
        "created_at": created_at,
        "observer_lat": 52.0,
        "observer_lon": 21.0,
        "observer_distance_km": 2.5,
        "score": 0.81,
        "confidence": 0.9,
        "offset_body_diameters": 0.2,
        "angular_separation_deg": 0.1,
        "body_radius_deg": 0.25,
        "body_azimuth_deg": 120.0,
        "body_elevation_deg": 18.0,
        "status": "OBSERVATION_CANDIDATE",
        "rejection_reason": None,
        "stability_score": 0.9,
        "alignment_score": 0.8,
        "altitude_score": 0.7,
        "body_elevation_score": 0.6,
        "aircraft_range_score": 0.8,
        "lead_time_score": 0.9,
        "observer_distance_score": 0.8,
        "snapshot_id": None,
        "source_observed_at": created_at,
        "path_start_utc": None,
        "path_end_utc": None,
        "sample_interval_seconds": None,
        "point_count": None,
        "points": {"version": 1, "points": []},
        "home_lat": 52.0,
        "home_lon": 21.0,
    }

    def fake_query(database_url, sql, params=()):
        if "FROM transit_candidates c" in sql:
            return [candidate_row]
        if "WITH ranked AS" in sql:
            return [{
                "created_at": created_at,
                "transit_time_utc": transit_time,
                "score": 0.81,
                "offset_body_diameters": 0.2,
                "observer_lat": 52.0,
                "observer_lon": 21.0,
                "status": "OBSERVATION_CANDIDATE",
                "rejection_reason": None,
            }]
        if "FROM aircraft_observations" in sql:
            return []
        raise AssertionError(f"Unexpected query: {sql}")

    monkeypatch.setenv("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", "2")
    monkeypatch.setattr(ui, "_query", fake_query)

    result = ui._event_detail("postgresql://test", 7)

    assert result["required_early_cycles"] == 2
    assert len(result["event_series"]) == 1
    assert result["candidate"]["notification_block_reason"] == "ONLY_1_CONVERGED_CYCLE"
    assert result["actual_result"] is None


def test_notification_block_analysis_detects_moving_transit_time(monkeypatch) -> None:
    first = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    second = first + timedelta(seconds=20)
    candidate = {
        "status": "ALERT_READY",
        "score": 1.0,
        "offset_body_diameters": 0.02,
        "created_at": second,
    }
    series = [
        {
            "created_at": first,
            "transit_time_utc": first + timedelta(minutes=5),
            "offset_body_diameters": 0.02,
            "observer_lat": 52.0,
            "observer_lon": 21.0,
            "status": "ALERT_READY",
        },
        {
            "created_at": second,
            "transit_time_utc": first + timedelta(minutes=5, seconds=12),
            "offset_body_diameters": 0.02,
            "observer_lat": 52.0,
            "observer_lon": 21.0,
            "status": "ALERT_READY",
        },
    ]
    monkeypatch.setenv("ALERT_MIN_SCORE", "0.70")
    monkeypatch.setenv("MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT", "0.25")
    monkeypatch.setenv("EARLY_NOTIFICATION_CONSECUTIVE_CYCLES", "2")
    monkeypatch.setenv("NOTIFICATION_MAX_TIME_SHIFT_SECONDS", "5")

    result = ui._notification_block_analysis(candidate, series)

    assert result["notification_consecutive_cycles"] == 1
    assert result["notification_required_cycles"] == 2
    assert result["notification_block_reason"] == "TRANSIT_TIME_MOVED"
