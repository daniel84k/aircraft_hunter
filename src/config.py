from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    user_lat: float
    user_lon: float
    adsbfi_base: str
    search_radius_nm: float
    poll_interval_seconds: int
    prediction_horizon_seconds: int
    prediction_step_seconds: int
    max_observer_relocation_km: float
    travel_speed_kmh: float
    reach_safety: float
    min_lead_time_seconds: int
    preferred_lead_time_seconds: int
    min_altitude_ft: float
    soft_good_altitude_ft: float
    airport_exclusion_nm: float
    airport_traffic_filters: str
    max_vertical_rate_stable_fpm: float
    max_track_change_60s_deg: float
    max_gs_change_60s_kt: float
    alert_min_score: float
    max_offset_body_diameters_for_alert: float
    min_body_elevation_deg: float
    min_body_elevation_deg_for_candidate: float
    observation_candidate_max_separation_deg: float
    telegram_candidate_cooldown_seconds: int
    telegram_max_candidates_per_cycle: int
    telegram_update_cooldown_seconds: int
    telegram_update_min_distance_improvement_km: float
    telegram_update_min_offset_improvement_ratio: float
    standby_body_elevation_deg: float
    min_stability_score_for_geometry: float
    min_aircraft_elevation_deg_for_geometry: float
    max_aircraft_range_km_for_geometry: float
    locked_alert_window_seconds: int
    run_mode: str
    log_level: str
    log_to_file: bool
    log_dir: str
    ui_enabled: bool
    ui_host: str
    ui_port: int
    database_url: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str


def _get_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        user_lat=float(os.getenv("USER_LAT", "52.0")),
        user_lon=float(os.getenv("USER_LON", "21.0")),
        adsbfi_base=os.getenv("ADSBFI_BASE", "https://opendata.adsb.fi/api").rstrip("/"),
        search_radius_nm=float(os.getenv("SEARCH_RADIUS_NM", "120")),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "10")),
        prediction_horizon_seconds=int(os.getenv("PREDICTION_HORIZON_SECONDS", "1800")),
        prediction_step_seconds=int(os.getenv("PREDICTION_STEP_SECONDS", "1")),
        max_observer_relocation_km=float(os.getenv("MAX_OBSERVER_RELOCATION_KM", "12")),
        travel_speed_kmh=float(os.getenv("TRAVEL_SPEED_KMH", "50")),
        reach_safety=float(os.getenv("REACH_SAFETY", "0.8")),
        min_lead_time_seconds=int(os.getenv("MIN_LEAD_TIME_SECONDS", "600")),
        preferred_lead_time_seconds=int(os.getenv("PREFERRED_LEAD_TIME_SECONDS", "900")),
        min_altitude_ft=float(os.getenv("MIN_ALTITUDE_FT", "12000")),
        soft_good_altitude_ft=float(os.getenv("SOFT_GOOD_ALTITUDE_FT", "18000")),
        airport_exclusion_nm=float(os.getenv("AIRPORT_EXCLUSION_NM", "20")),
        airport_traffic_filters=os.getenv("AIRPORT_TRAFFIC_FILTERS", "EPWA:strict:20,EPML:soft:8"),
        max_vertical_rate_stable_fpm=float(os.getenv("MAX_VERTICAL_RATE_STABLE_FPM", "500")),
        max_track_change_60s_deg=float(os.getenv("MAX_TRACK_CHANGE_60S_DEG", "8")),
        max_gs_change_60s_kt=float(os.getenv("MAX_GS_CHANGE_60S_KT", "40")),
        alert_min_score=float(os.getenv("ALERT_MIN_SCORE", "0.80")),
        max_offset_body_diameters_for_alert=float(os.getenv("MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT", "0.25")),
        min_body_elevation_deg=float(os.getenv("MIN_BODY_ELEVATION_DEG", "8")),
        min_body_elevation_deg_for_candidate=float(os.getenv("MIN_BODY_ELEVATION_DEG_FOR_CANDIDATE", "0")),
        observation_candidate_max_separation_deg=float(os.getenv("OBSERVATION_CANDIDATE_MAX_SEPARATION_DEG", "0.25")),
        telegram_candidate_cooldown_seconds=int(os.getenv("TELEGRAM_CANDIDATE_COOLDOWN_SECONDS", "900")),
        telegram_max_candidates_per_cycle=int(os.getenv("TELEGRAM_MAX_CANDIDATES_PER_CYCLE", "2")),
        telegram_update_cooldown_seconds=int(os.getenv("TELEGRAM_UPDATE_COOLDOWN_SECONDS", "180")),
        telegram_update_min_distance_improvement_km=float(os.getenv("TELEGRAM_UPDATE_MIN_DISTANCE_IMPROVEMENT_KM", "0.5")),
        telegram_update_min_offset_improvement_ratio=float(os.getenv("TELEGRAM_UPDATE_MIN_OFFSET_IMPROVEMENT_RATIO", "0.30")),
        standby_body_elevation_deg=float(os.getenv("STANDBY_BODY_ELEVATION_DEG", "5")),
        min_stability_score_for_geometry=float(os.getenv("MIN_STABILITY_SCORE_FOR_GEOMETRY", "0.65")),
        min_aircraft_elevation_deg_for_geometry=float(os.getenv("MIN_AIRCRAFT_ELEVATION_DEG_FOR_GEOMETRY", "7.5")),
        max_aircraft_range_km_for_geometry=float(os.getenv("MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY", "120")),
        locked_alert_window_seconds=int(os.getenv("LOCKED_ALERT_WINDOW_SECONDS", "600")),
        run_mode=os.getenv("RUN_MODE", "quiet"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_to_file=_get_bool("LOG_TO_FILE", True),
        log_dir=os.getenv("LOG_DIR", "./logs"),
        ui_enabled=_get_bool("UI_ENABLED", True),
        ui_host=os.getenv("UI_HOST", "0.0.0.0"),
        ui_port=int(os.getenv("UI_PORT", "9999")),
        database_url=os.getenv("DATABASE_URL", "postgresql://aircraft:aircraft@postgres:5432/aircraft_transit"),
        postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "aircraft_transit"),
        postgres_user=os.getenv("POSTGRES_USER", "aircraft"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "aircraft"),
    )
