from __future__ import annotations

from datetime import datetime
import json
import logging

from models import AircraftState, TransitCandidate


LOG = logging.getLogger(__name__)


def _event_slot(transit_time_utc: datetime, event_window_seconds: int) -> int:
    return int(transit_time_utc.timestamp()) // max(60, int(event_window_seconds))


class Storage:
    def __init__(self, conn) -> None:
        self.conn = conn

    def insert_observations(self, observations: list[AircraftState]) -> None:
        if not observations:
            return
        rows = [
            (
                ac.timestamp,
                ac.icao,
                ac.callsign,
                ac.aircraft_type,
                ac.lat,
                ac.lon,
                ac.altitude_ft,
                ac.ground_speed_kt,
                ac.track_deg,
                ac.vertical_rate_fpm,
                ac.origin,
                ac.destination,
                json.dumps(ac.raw_json),
            )
            for ac in observations
        ]
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO aircraft_observations (
                    observed_at, icao, callsign, aircraft_type, lat, lon, altitude_ft,
                    ground_speed_kt, track_deg, vertical_rate_fpm, origin, destination, raw_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                rows,
            )
        self.conn.commit()

    def start_prediction_run(self, started_at: datetime, settings, aircraft_count_total: int) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO prediction_runs (
                    started_at, user_lat, user_lon, search_radius_nm, prediction_horizon_seconds,
                    aircraft_count_total
                ) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
                """,
                (
                    started_at,
                    settings.user_lat,
                    settings.user_lon,
                    settings.search_radius_nm,
                    settings.prediction_horizon_seconds,
                    aircraft_count_total,
                ),
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    def finish_prediction_run(self, run_id: int, finished_at: datetime, analyzed: int, candidates: int, alerts: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE prediction_runs
                SET finished_at=%s, aircraft_count_analyzed=%s, candidate_count=%s, alert_count=%s
                WHERE id=%s
                """,
                (finished_at, analyzed, candidates, alerts, run_id),
            )
        self.conn.commit()

    def insert_candidate(self, run_id: int, candidate: TransitCandidate) -> int:
        ac = candidate.aircraft
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transit_candidates (
                    prediction_run_id, icao, callsign, aircraft_type, body, transit_time_utc,
                    time_to_transit_seconds, observer_lat, observer_lon, observer_distance_km,
                    google_maps_url, google_nav_url, angular_separation_deg, body_radius_deg,
                    offset_body_diameters, score, confidence, stability_score, alignment_score,
                    altitude_score, body_elevation_score, aircraft_range_score, lead_time_score,
                    observer_distance_score, aircraft_altitude_ft, aircraft_range_km,
                    aircraft_track_deg, aircraft_ground_speed_kt, aircraft_vertical_rate_fpm,
                    body_azimuth_deg, body_elevation_deg, status, rejection_reason, alert_sent,
                    alerted_at, dedupe_key
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) RETURNING id
                """,
                (
                    run_id,
                    ac.icao,
                    ac.callsign,
                    ac.aircraft_type,
                    candidate.body,
                    candidate.transit_time_utc,
                    int((candidate.transit_time_utc - ac.timestamp).total_seconds()),
                    candidate.observer_lat,
                    candidate.observer_lon,
                    candidate.observer_distance_km,
                    candidate.google_maps_url,
                    candidate.google_nav_url,
                    candidate.angular_separation_deg,
                    candidate.body_radius_deg,
                    candidate.offset_body_diameters,
                    candidate.score,
                    candidate.confidence,
                    candidate.stability_score,
                    candidate.alignment_score,
                    candidate.altitude_score,
                    candidate.body_elevation_score,
                    candidate.aircraft_range_score,
                    candidate.lead_time_score,
                    candidate.observer_distance_score,
                    candidate.aircraft_altitude_ft,
                    candidate.aircraft_range_km,
                    candidate.aircraft_track_deg,
                    candidate.aircraft_ground_speed_kt,
                    candidate.aircraft_vertical_rate_fpm,
                    candidate.body_azimuth_deg,
                    candidate.body_elevation_deg,
                    candidate.status,
                    candidate.rejection_reason,
                    candidate.status == "ALERT_SENT",
                    None,
                    candidate.dedupe_key,
                ),
            )
            candidate_id = cur.fetchone()[0]
        self.conn.commit()
        return candidate_id

    def alert_exists(self, dedupe_key: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM alerts WHERE dedupe_key=%s LIMIT 1", (dedupe_key,))
            return cur.fetchone() is not None

    def alert_event_exists(
        self,
        *,
        icao: str,
        body: str,
        transit_time_utc: datetime,
        event_window_seconds: int,
        confirmed_only: bool = False,
    ) -> bool:
        return self.alert_event_summary(
            icao=icao,
            body=body,
            transit_time_utc=transit_time_utc,
            event_window_seconds=event_window_seconds,
            confirmed_only=confirmed_only,
        ) is not None

    def alert_event_summary(
        self,
        *,
        icao: str,
        body: str,
        transit_time_utc: datetime,
        event_window_seconds: int,
        confirmed_only: bool = False,
    ) -> dict[str, float] | None:
        window = max(60, int(event_window_seconds))
        event_slot = _event_slot(transit_time_utc, window)
        confirmed_filter = "AND tc.rejection_reason IS NULL" if confirmed_only else ""
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    min(tc.observer_distance_km) AS best_distance_km,
                    min(tc.offset_body_diameters) AS best_offset_body_diameters
                FROM alerts a
                JOIN transit_candidates tc ON tc.id = a.transit_candidate_id
                WHERE lower(tc.icao) = lower(%s)
                  AND lower(tc.body) = lower(%s)
                  AND floor(extract(epoch from tc.transit_time_utc) / %s) = %s
                  {confirmed_filter}
                """,
                (icao, body, window, event_slot),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            return {
                "best_distance_km": float(row[0]),
                "best_offset_body_diameters": float(row[1]),
            }

    def insert_alert(
        self,
        candidate_id: int,
        message: str,
        dedupe_key: str,
        printed_at: datetime,
        alert_type: str = "CONSOLE",
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (transit_candidate_id, alert_type, printed_at, message, dedupe_key)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (candidate_id, alert_type, printed_at, message, dedupe_key),
            )
            cur.execute(
                """
                UPDATE transit_candidates
                SET alert_sent=true, alerted_at=%s, status='ALERT_SENT'
                WHERE id=%s
                """,
                (printed_at, candidate_id),
            )
        self.conn.commit()
