from __future__ import annotations

from dataclasses import replace

import data_retention
from config import Settings


def _settings(**overrides) -> Settings:
    base = Settings(
        user_lat=52.0,
        user_lon=21.0,
        adsbfi_base="http://adsb",
        search_radius_nm=120.0,
        poll_interval_seconds=10,
        prediction_horizon_seconds=1800,
        prediction_step_seconds=1,
        prediction_use_history_fit=True,
        prediction_fit_window_seconds=90,
        prediction_fit_min_points=4,
        max_observer_relocation_km=12.0,
        travel_speed_kmh=50.0,
        reach_safety=0.8,
        min_lead_time_seconds=600,
        preferred_lead_time_seconds=900,
        alert_ready_lead_time_seconds=300,
        min_altitude_ft=12000.0,
        soft_good_altitude_ft=18000.0,
        airport_exclusion_nm=20.0,
        airport_traffic_filters="EPWA:strict:35",
        max_vertical_rate_stable_fpm=500.0,
        allow_stable_vertical_trend=True,
        max_stable_vertical_rate_fpm=3000.0,
        max_vertical_rate_variation_fpm=600.0,
        stable_vertical_trend_min_points=4,
        max_track_change_60s_deg=8.0,
        max_gs_change_60s_kt=40.0,
        alert_min_score=0.7,
        max_offset_body_diameters_for_alert=0.25,
        min_body_elevation_deg=8.0,
        min_body_elevation_deg_for_candidate=0.0,
        observation_candidate_max_separation_deg=0.2,
        observation_candidate_min_score=0.65,
        observation_candidate_max_lead_seconds=600,
        notification_require_convergence=True,
        early_notification_consecutive_cycles=2,
        notification_consecutive_cycles=3,
        notification_max_time_shift_seconds=5.0,
        notification_max_observer_shift_km=0.5,
        notification_max_offset_worsening_diameters=0.05,
        telegram_candidate_cooldown_seconds=900,
        telegram_max_candidates_per_cycle=2,
        telegram_update_cooldown_seconds=180,
        telegram_update_min_distance_improvement_km=0.5,
        telegram_update_min_offset_improvement_ratio=0.3,
        alert_notifications_enabled=True,
        alert_service_poll_interval_seconds=5,
        alert_service_candidate_lookback_seconds=900,
        transit_validation_enabled=True,
        transit_validation_delay_seconds=45,
        transit_validation_observation_window_seconds=90,
        transit_validation_max_wait_seconds=300,
        transit_validation_uncertainty_diameters=0.1,
        standby_body_elevation_deg=5.0,
        min_stability_score_for_geometry=0.65,
        min_aircraft_elevation_deg_for_geometry=7.5,
        max_aircraft_range_km_for_geometry=120.0,
        locked_alert_window_seconds=600,
        run_mode="quiet",
        log_level="INFO",
        log_to_file=True,
        log_dir="./logs",
        log_retain_full_days=3,
        log_retain_compressed_days=10,
        log_emergency_free_mb=1536,
        data_retention_enabled=True,
        data_retention_interval_seconds=3600,
        data_retention_observations_hours=48,
        data_retention_rejected_candidate_days=7,
        data_retention_interesting_candidate_days=30,
        data_retention_prediction_run_days=14,
        data_retention_trajectory_snapshot_days=30,
        data_retention_emergency_free_mb=1536,
        data_retention_emergency_observations_hours=24,
        data_retention_emergency_rejected_candidate_days=3,
        data_retention_delete_batch_size=50000,
        ui_enabled=True,
        ui_host="0.0.0.0",
        ui_port=9999,
        database_url="postgresql://test",
        postgres_host="postgres",
        postgres_port=5432,
        postgres_db="aircraft_transit",
        postgres_user="aircraft",
        postgres_password="aircraft",
    )
    return replace(base, **overrides)


class FakeCursor:
    def __init__(self, rowcounts: list[int]) -> None:
        self.rowcounts = rowcounts
        self.queries: list[tuple[str, tuple]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.queries.append((sql, params))
        self.rowcount = self.rowcounts.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, rowcounts: list[int]) -> None:
        self.cursor_obj = FakeCursor(rowcounts)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_data_retention_uses_normal_thresholds(monkeypatch) -> None:
    conn = FakeConnection([1, 2, 3, 4, 5])
    monkeypatch.setattr(data_retention, "_free_mb", lambda _path: 5000)

    result = data_retention.run_data_retention(conn, _settings(), free_space_path="/tmp")

    assert result is not None
    assert result.emergency is False
    assert result.total_deleted == 15
    assert conn.commits == 5
    assert conn.rollbacks == 0
    params = [params for _sql, params in conn.cursor_obj.queries]
    assert params[0] == (30, 50000)
    assert params[1] == (7, 50000)
    assert params[2][0] == 30
    assert params[2][2] == 50000
    assert params[3] == (14, 50000)
    assert params[4] == (48, 50000)


def test_data_retention_uses_emergency_thresholds(monkeypatch) -> None:
    conn = FakeConnection([0, 7, 0, 0, 11])
    monkeypatch.setattr(data_retention, "_free_mb", lambda _path: 900)

    result = data_retention.run_data_retention(conn, _settings(), free_space_path="/tmp")

    assert result is not None
    assert result.emergency is True
    params = [params for _sql, params in conn.cursor_obj.queries]
    assert params[1] == (3, 50000)
    assert params[4] == (24, 50000)


def test_data_retention_can_be_disabled(monkeypatch) -> None:
    conn = FakeConnection([])
    monkeypatch.setattr(data_retention, "_free_mb", lambda _path: 900)

    assert data_retention.run_data_retention(conn, _settings(data_retention_enabled=False)) is None
    assert conn.cursor_obj.queries == []


def test_retention_due_respects_interval() -> None:
    assert data_retention.retention_due(None, 3600, 100.0) is True
    assert data_retention.retention_due(100.0, 3600, 3699.0) is False
    assert data_retention.retention_due(100.0, 3600, 3700.0) is True
