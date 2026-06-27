from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


CandidateStatus = Literal["CANDIDATE_STORED", "OBSERVATION_CANDIDATE", "ALERT_READY", "ALERT_SENT", "REJECTED"]


@dataclass(frozen=True)
class AircraftState:
    icao: str
    callsign: str | None
    lat: float
    lon: float
    altitude_ft: float | None
    ground_speed_kt: float | None
    track_deg: float | None
    vertical_rate_fpm: float | None
    aircraft_type: str | None
    origin: str | None
    destination: str | None
    timestamp: datetime
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictedPoint:
    timestamp: datetime
    lat: float
    lon: float
    altitude_ft: float | None
    azimuth_deg: float
    elevation_deg: float
    range_km: float


@dataclass(frozen=True)
class CelestialBodyState:
    body: str
    timestamp: datetime
    azimuth_deg: float
    elevation_deg: float
    angular_radius_deg: float
    illumination: float | None = None


@dataclass
class ScoreBreakdown:
    score: float
    confidence: float
    stability_score: float
    alignment_score: float
    altitude_score: float
    body_elevation_score: float
    aircraft_range_score: float
    lead_time_score: float
    observer_distance_score: float


@dataclass
class TransitCandidate:
    aircraft: AircraftState
    body: str
    transit_time_utc: datetime
    observer_lat: float
    observer_lon: float
    observer_distance_km: float
    google_maps_url: str
    google_nav_url: str
    angular_separation_deg: float
    body_radius_deg: float
    offset_body_diameters: float
    score: float
    confidence: float
    aircraft_range_km: float | None
    aircraft_altitude_ft: float | None
    body_elevation_deg: float
    status: CandidateStatus
    rejection_reason: str | None
    dedupe_key: str
    stability_score: float = 0.0
    alignment_score: float = 0.0
    altitude_score: float = 0.0
    body_elevation_score: float = 0.0
    aircraft_range_score: float = 0.0
    lead_time_score: float = 0.0
    observer_distance_score: float = 0.0
    aircraft_track_deg: float | None = None
    aircraft_ground_speed_kt: float | None = None
    aircraft_vertical_rate_fpm: float | None = None
    body_azimuth_deg: float | None = None
    observer_home_offset_body_diameters: float | None = None
    observer_best_grid_offset_body_diameters: float | None = None
    observer_grid_points_checked: int = 0
    observer_selected_from_home: bool = False
