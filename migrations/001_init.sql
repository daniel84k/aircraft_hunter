CREATE TABLE IF NOT EXISTS aircraft_observations (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    icao TEXT NOT NULL,
    callsign TEXT,
    aircraft_type TEXT,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    altitude_ft DOUBLE PRECISION,
    ground_speed_kt DOUBLE PRECISION,
    track_deg DOUBLE PRECISION,
    vertical_rate_fpm DOUBLE PRECISION,
    origin TEXT,
    destination TEXT,
    raw_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    user_lat DOUBLE PRECISION NOT NULL,
    user_lon DOUBLE PRECISION NOT NULL,
    search_radius_nm DOUBLE PRECISION NOT NULL,
    prediction_horizon_seconds INTEGER NOT NULL,
    aircraft_count_total INTEGER NOT NULL DEFAULT 0,
    aircraft_count_analyzed INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    alert_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transit_candidates (
    id BIGSERIAL PRIMARY KEY,
    prediction_run_id BIGINT REFERENCES prediction_runs(id),
    icao TEXT NOT NULL,
    callsign TEXT,
    aircraft_type TEXT,
    body TEXT NOT NULL,
    transit_time_utc TIMESTAMPTZ NOT NULL,
    time_to_transit_seconds INTEGER NOT NULL,
    observer_lat DOUBLE PRECISION NOT NULL,
    observer_lon DOUBLE PRECISION NOT NULL,
    observer_distance_km DOUBLE PRECISION NOT NULL,
    google_maps_url TEXT NOT NULL,
    google_nav_url TEXT NOT NULL,
    angular_separation_deg DOUBLE PRECISION NOT NULL,
    body_radius_deg DOUBLE PRECISION NOT NULL,
    offset_body_diameters DOUBLE PRECISION NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    stability_score DOUBLE PRECISION NOT NULL,
    alignment_score DOUBLE PRECISION NOT NULL,
    altitude_score DOUBLE PRECISION NOT NULL,
    body_elevation_score DOUBLE PRECISION NOT NULL,
    aircraft_range_score DOUBLE PRECISION NOT NULL,
    lead_time_score DOUBLE PRECISION NOT NULL,
    observer_distance_score DOUBLE PRECISION NOT NULL,
    aircraft_altitude_ft DOUBLE PRECISION,
    aircraft_range_km DOUBLE PRECISION,
    aircraft_track_deg DOUBLE PRECISION,
    aircraft_ground_speed_kt DOUBLE PRECISION,
    aircraft_vertical_rate_fpm DOUBLE PRECISION,
    body_azimuth_deg DOUBLE PRECISION,
    body_elevation_deg DOUBLE PRECISION,
    status TEXT NOT NULL,
    rejection_reason TEXT,
    alert_sent BOOLEAN NOT NULL DEFAULT false,
    alerted_at TIMESTAMPTZ,
    dedupe_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    transit_candidate_id BIGINT REFERENCES transit_candidates(id),
    alert_type TEXT NOT NULL,
    printed_at TIMESTAMPTZ NOT NULL,
    message TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_aircraft_observations_icao_time
ON aircraft_observations (icao, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_aircraft_observations_observed_at
ON aircraft_observations (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_transit_candidates_time
ON transit_candidates (transit_time_utc);

CREATE INDEX IF NOT EXISTS idx_transit_candidates_created_at
ON transit_candidates (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_transit_candidates_status
ON transit_candidates (status);

CREATE INDEX IF NOT EXISTS idx_prediction_runs_started_at
ON prediction_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_dedupe_key
ON alerts (dedupe_key);

CREATE INDEX IF NOT EXISTS idx_alerts_printed_at
ON alerts (printed_at DESC);

CREATE TABLE IF NOT EXISTS transit_validation_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO transit_validation_state (singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS transit_validations (
    id BIGSERIAL PRIMARY KEY,
    source_alert_id BIGINT NOT NULL REFERENCES alerts(id),
    icao TEXT NOT NULL,
    callsign TEXT,
    body TEXT NOT NULL,
    event_slot BIGINT NOT NULL,
    predicted_transit_time_utc TIMESTAMPTZ NOT NULL,
    actual_closest_time_utc TIMESTAMPTZ,
    observer_lat DOUBLE PRECISION NOT NULL,
    observer_lon DOUBLE PRECISION NOT NULL,
    predicted_offset_body_diameters DOUBLE PRECISION NOT NULL,
    actual_offset_body_diameters DOUBLE PRECISION,
    actual_separation_deg DOUBLE PRECISION,
    vertical_offset_body_diameters DOUBLE PRECISION,
    horizontal_offset_body_diameters DOUBLE PRECISION,
    result TEXT NOT NULL CHECK (result IN ('HIT', 'MISS', 'UNCERTAIN', 'NO_DATA')),
    message TEXT NOT NULL,
    validated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at TIMESTAMPTZ,
    UNIQUE (icao, body, event_slot)
);

CREATE INDEX IF NOT EXISTS idx_transit_validations_notified_at
ON transit_validations (notified_at, validated_at);

ALTER TABLE transit_validations
ADD COLUMN IF NOT EXISTS vertical_offset_body_diameters DOUBLE PRECISION;

ALTER TABLE transit_validations
ADD COLUMN IF NOT EXISTS horizontal_offset_body_diameters DOUBLE PRECISION;
