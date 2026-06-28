from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging

from geo import haversine_distance_km
from models import AircraftState, PredictedPoint, TransitCandidate
from observer_solver import google_maps_url, google_nav_url
from validation import ActualObservation, ValidationResult


LOG = logging.getLogger(__name__)


def _event_slot(transit_time_utc: datetime, event_window_seconds: int) -> int:
    return int(transit_time_utc.timestamp()) // max(60, int(event_window_seconds))


def serialize_prediction_path(
    path: list[PredictedPoint],
    sample_interval_seconds: int = 5,
) -> dict:
    """Build a compact, interpolation-friendly snapshot of a predicted path."""
    if not path:
        return {"version": 1, "points": []}
    interval = max(1, int(sample_interval_seconds))
    sampled: list[PredictedPoint] = []
    last_timestamp: datetime | None = None
    for point in path:
        if (
            last_timestamp is None
            or (point.timestamp - last_timestamp).total_seconds() >= interval
            or point is path[-1]
        ):
            sampled.append(point)
            last_timestamp = point.timestamp
    return {
        "version": 1,
        "points": [
            [
                round(point.timestamp.timestamp(), 3),
                round(point.lat, 7),
                round(point.lon, 7),
                round(point.altitude_ft, 1) if point.altitude_ft is not None else None,
            ]
            for point in sampled
        ],
    }


def _candidate_from_notification_row(row) -> TransitCandidate:
    aircraft = AircraftState(
        icao=row[1],
        callsign=row[2],
        aircraft_type=row[3],
        lat=0.0,
        lon=0.0,
        altitude_ft=row[14],
        ground_speed_kt=row[17],
        track_deg=row[16],
        vertical_rate_fpm=row[18],
        origin=None,
        destination=None,
        timestamp=datetime.now(tz=row[5].tzinfo),
        raw_json={},
    )
    return TransitCandidate(
        aircraft=aircraft,
        body=row[4],
        transit_time_utc=row[5],
        observer_lat=row[6],
        observer_lon=row[7],
        observer_distance_km=row[8],
        google_maps_url=google_maps_url(row[6], row[7]),
        google_nav_url=google_nav_url(row[6], row[7]),
        angular_separation_deg=row[9],
        body_radius_deg=row[10],
        offset_body_diameters=row[11],
        score=row[12],
        confidence=row[13],
        aircraft_altitude_ft=row[14],
        aircraft_range_km=row[15],
        aircraft_track_deg=row[16],
        aircraft_ground_speed_kt=row[17],
        aircraft_vertical_rate_fpm=row[18],
        body_azimuth_deg=row[19],
        body_elevation_deg=row[20],
        status=row[21],
        rejection_reason=row[22],
        dedupe_key=row[23],
        stability_score=row[24],
        alignment_score=row[25],
        altitude_score=row[26],
        body_elevation_score=row[27],
        aircraft_range_score=row[28],
        lead_time_score=row[29],
        observer_distance_score=row[30],
        observer_home_offset_body_diameters=row[31],
        observer_best_grid_offset_body_diameters=row[32],
        observer_grid_points_checked=row[33],
        observer_selected_from_home=row[34],
    )


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
                    alerted_at, dedupe_key, observer_home_offset_body_diameters,
                    observer_best_grid_offset_body_diameters, observer_grid_points_checked,
                    observer_selected_from_home
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
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
                    candidate.observer_home_offset_body_diameters,
                    candidate.observer_best_grid_offset_body_diameters,
                    candidate.observer_grid_points_checked,
                    candidate.observer_selected_from_home,
                ),
            )
            candidate_id = cur.fetchone()[0]
        self.conn.commit()
        return candidate_id

    def insert_radar_event(
        self,
        run_id: int,
        candidate: TransitCandidate,
        *,
        reachable_now: bool,
    ) -> int:
        ac = candidate.aircraft
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO radar_events (
                    prediction_run_id, icao, callsign, aircraft_type, body,
                    transit_time_utc, time_to_transit_seconds,
                    observer_lat, observer_lon, observer_distance_km,
                    angular_separation_deg, body_radius_deg, offset_body_diameters,
                    home_offset_body_diameters, best_grid_offset_body_diameters,
                    grid_points_checked, selected_from_home, reachable_now,
                    score, confidence, stability_score,
                    aircraft_altitude_ft, aircraft_range_km, aircraft_track_deg,
                    aircraft_ground_speed_kt, aircraft_vertical_rate_fpm,
                    body_azimuth_deg, body_elevation_deg,
                    alert_status, alert_rejection_reason
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
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
                    candidate.angular_separation_deg,
                    candidate.body_radius_deg,
                    candidate.offset_body_diameters,
                    candidate.observer_home_offset_body_diameters,
                    candidate.observer_best_grid_offset_body_diameters,
                    candidate.observer_grid_points_checked,
                    candidate.observer_selected_from_home,
                    reachable_now,
                    candidate.score,
                    candidate.confidence,
                    candidate.stability_score,
                    candidate.aircraft_altitude_ft,
                    candidate.aircraft_range_km,
                    candidate.aircraft_track_deg,
                    candidate.aircraft_ground_speed_kt,
                    candidate.aircraft_vertical_rate_fpm,
                    candidate.body_azimuth_deg,
                    candidate.body_elevation_deg,
                    candidate.status,
                    candidate.rejection_reason,
                ),
            )
            radar_event_id = cur.fetchone()[0]
        self.conn.commit()
        return radar_event_id

    def link_radar_event_candidate(
        self,
        radar_event_id: int,
        candidate_id: int,
        candidate: TransitCandidate,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_events
                SET transit_candidate_id=%s, alert_status=%s, alert_rejection_reason=%s
                WHERE id=%s
                """,
                (candidate_id, candidate.status, candidate.rejection_reason, radar_event_id),
            )
        self.conn.commit()

    def upsert_event_trajectory(
        self,
        candidate_id: int,
        candidate: TransitCandidate,
        payload: dict,
        *,
        event_window_seconds: int,
        sample_interval_seconds: int = 5,
    ) -> bool:
        points = payload.get("points") or []
        if not points:
            return False
        event_slot = _event_slot(candidate.transit_time_utc, event_window_seconds)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_trajectory_snapshots (
                    candidate_id, icao, body, event_slot, score, offset_body_diameters,
                    source_observed_at, path_start_utc, path_end_utc,
                    sample_interval_seconds, point_count, points
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),to_timestamp(%s),%s,%s,%s::jsonb)
                ON CONFLICT (icao, body, event_slot) DO UPDATE SET
                    candidate_id=EXCLUDED.candidate_id,
                    score=EXCLUDED.score,
                    offset_body_diameters=EXCLUDED.offset_body_diameters,
                    source_observed_at=EXCLUDED.source_observed_at,
                    path_start_utc=EXCLUDED.path_start_utc,
                    path_end_utc=EXCLUDED.path_end_utc,
                    sample_interval_seconds=EXCLUDED.sample_interval_seconds,
                    point_count=EXCLUDED.point_count,
                    points=EXCLUDED.points,
                    updated_at=now()
                WHERE EXCLUDED.score > event_trajectory_snapshots.score
                   OR (
                       EXCLUDED.score = event_trajectory_snapshots.score
                       AND EXCLUDED.offset_body_diameters < event_trajectory_snapshots.offset_body_diameters
                   )
                RETURNING id
                """,
                (
                    candidate_id,
                    candidate.aircraft.icao.lower(),
                    candidate.body.lower(),
                    event_slot,
                    candidate.score,
                    candidate.offset_body_diameters,
                    candidate.aircraft.timestamp,
                    points[0][0],
                    points[-1][0],
                    max(1, int(sample_interval_seconds)),
                    len(points),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            stored = cur.fetchone() is not None
        self.conn.commit()
        return stored

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
        alert_type: str | None = None,
    ) -> bool:
        return self.alert_event_summary(
            icao=icao,
            body=body,
            transit_time_utc=transit_time_utc,
            event_window_seconds=event_window_seconds,
            confirmed_only=confirmed_only,
            alert_type=alert_type,
        ) is not None

    def alert_event_summary(
        self,
        *,
        icao: str,
        body: str,
        transit_time_utc: datetime,
        event_window_seconds: int,
        confirmed_only: bool = False,
        alert_type: str | None = None,
    ) -> dict[str, float] | None:
        window = max(60, int(event_window_seconds))
        half_window = timedelta(seconds=window / 2)
        window_start = transit_time_utc - half_window
        window_end = transit_time_utc + half_window
        confirmed_filter = (
            "AND a.alert_type <> 'EARLY' AND tc.rejection_reason IS NULL"
            if confirmed_only
            else ""
        )
        alert_type_filter = "AND a.alert_type = %s" if alert_type else ""
        params = [icao, body, window_start, window_end]
        if alert_type:
            params.append(alert_type)
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
                  AND tc.transit_time_utc BETWEEN %s AND %s
                  {confirmed_filter}
                  {alert_type_filter}
                """,
                params,
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

    def mark_candidate_rejected(self, candidate_id: int, reason: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE transit_candidates
                SET status='REJECTED', rejection_reason=%s
                WHERE id=%s AND alert_sent=false
                """,
                (reason, candidate_id),
            )
        self.conn.commit()

    def pending_notification_candidates(
        self,
        *,
        now: datetime,
        event_window_seconds: int,
        lookback_seconds: int,
        limit: int = 50,
    ) -> list[tuple[int, TransitCandidate]]:
        window = max(60, int(event_window_seconds))
        created_after = now - timedelta(seconds=max(60, int(lookback_seconds)))
        with self.conn.cursor() as cur:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        tc.*,
                        floor(extract(epoch from tc.transit_time_utc) / %s)::bigint AS event_slot,
                        row_number() OVER (
                            PARTITION BY lower(tc.icao), lower(tc.body),
                                         floor(extract(epoch from tc.transit_time_utc) / %s)
                            ORDER BY
                                CASE WHEN tc.status = 'ALERT_READY' THEN 0 ELSE 1 END,
                                tc.score DESC,
                                tc.offset_body_diameters ASC,
                                tc.confidence DESC,
                                tc.stability_score DESC,
                                tc.observer_distance_km ASC,
                                tc.angular_separation_deg ASC,
                                tc.created_at DESC
                        ) AS rank
                    FROM transit_candidates tc
                    WHERE tc.status IN ('ALERT_READY', 'OBSERVATION_CANDIDATE')
                      AND tc.alert_sent = false
                      AND tc.transit_time_utc >= %s
                      AND tc.created_at >= %s
                )
                SELECT
                    id, icao, callsign, aircraft_type, body, transit_time_utc,
                    observer_lat, observer_lon, observer_distance_km,
                    angular_separation_deg, body_radius_deg, offset_body_diameters,
                    score, confidence, aircraft_altitude_ft, aircraft_range_km,
                    aircraft_track_deg, aircraft_ground_speed_kt, aircraft_vertical_rate_fpm,
                    body_azimuth_deg, body_elevation_deg, status, rejection_reason,
                    dedupe_key, stability_score, alignment_score, altitude_score,
                    body_elevation_score, aircraft_range_score, lead_time_score,
                    observer_distance_score, observer_home_offset_body_diameters,
                    observer_best_grid_offset_body_diameters, observer_grid_points_checked,
                    observer_selected_from_home
                FROM ranked
                WHERE rank = 1
                ORDER BY
                    CASE WHEN status = 'ALERT_READY' THEN 0 ELSE 1 END,
                    score DESC,
                    offset_body_diameters ASC,
                    confidence DESC,
                    stability_score DESC,
                    observer_distance_km ASC,
                    angular_separation_deg ASC
                LIMIT %s
                """,
                (window, window, now, created_after, max(1, limit)),
            )
            rows = cur.fetchall()
        self.conn.commit()
        return [(row[0], _candidate_from_notification_row(row)) for row in rows]

    def stored_candidate_convergence_count(
        self,
        candidate: TransitCandidate,
        *,
        event_window_seconds: int,
        max_gap_seconds: float,
        max_time_shift_seconds: float,
        max_observer_shift_km: float,
        max_offset_worsening_diameters: float,
        limit: int = 10,
    ) -> tuple[int, str]:
        window = max(60, int(event_window_seconds))
        event_slot = _event_slot(candidate.transit_time_utc, window)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT created_at, transit_time_utc, observer_lat, observer_lon, offset_body_diameters
                FROM transit_candidates
                WHERE lower(icao) = lower(%s)
                  AND lower(body) = lower(%s)
                  AND floor(extract(epoch from transit_time_utc) / %s)::bigint = %s
                  AND status IN ('ALERT_READY', 'OBSERVATION_CANDIDATE')
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (
                    candidate.aircraft.icao,
                    candidate.body,
                    window,
                    event_slot,
                    max(1, limit),
                ),
            )
            rows = cur.fetchall()
        self.conn.commit()
        if not rows:
            return 0, "NO_STORED_CANDIDATES"

        count = 1
        reason = "FIRST_OBSERVATION"
        previous = rows[0]
        for current in rows[1:]:
            gap_seconds = abs((previous[0] - current[0]).total_seconds())
            time_shift = abs((previous[1] - current[1]).total_seconds())
            observer_shift = haversine_distance_km(previous[2], previous[3], current[2], current[3])
            offset_worsening = previous[4] - current[4]
            if gap_seconds > max(30, max_gap_seconds):
                reason = "CYCLE_GAP"
                break
            if time_shift > max_time_shift_seconds:
                reason = "TRANSIT_TIME_MOVED"
                break
            if observer_shift > max_observer_shift_km:
                reason = "OBSERVER_POINT_MOVED"
                break
            if offset_worsening > max_offset_worsening_diameters:
                reason = "OFFSET_WORSENED"
                break
            count += 1
            reason = "CONVERGED"
            previous = current
        return count, reason

    def pending_validation_events(
        self,
        *,
        now: datetime,
        delay_seconds: int,
        event_window_seconds: int,
        limit: int = 20,
    ) -> list[dict]:
        window = max(60, int(event_window_seconds))
        ready_before = now - timedelta(seconds=max(0, delay_seconds))
        with self.conn.cursor() as cur:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        a.id AS alert_id,
                        lower(tc.icao) AS normalized_icao,
                        tc.icao,
                        tc.callsign,
                        tc.body,
                        floor(extract(epoch from tc.transit_time_utc) / %s)::bigint AS event_slot,
                        tc.transit_time_utc,
                        tc.observer_lat,
                        tc.observer_lon,
                        tc.offset_body_diameters,
                        row_number() OVER (
                            PARTITION BY lower(tc.icao), lower(tc.body),
                                         floor(extract(epoch from tc.transit_time_utc) / %s)
                            ORDER BY a.printed_at DESC, tc.offset_body_diameters ASC
                        ) AS rank
                    FROM alerts a
                    JOIN transit_candidates tc ON tc.id = a.transit_candidate_id
                    JOIN transit_validation_state state ON state.singleton = true
                    WHERE a.printed_at >= state.enabled_at
                      AND tc.transit_time_utc <= %s
                )
                SELECT alert_id, icao, callsign, body, event_slot, transit_time_utc,
                       observer_lat, observer_lon, offset_body_diameters
                FROM ranked event
                WHERE rank = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM transit_validations validation
                      WHERE validation.icao = event.normalized_icao
                        AND validation.body = lower(event.body)
                        AND validation.event_slot = event.event_slot
                  )
                ORDER BY transit_time_utc
                LIMIT %s
                """,
                (window, window, ready_before, max(1, limit)),
            )
            rows = cur.fetchall()
        return [
            {
                "alert_id": row[0],
                "icao": row[1],
                "callsign": row[2],
                "body": row[3],
                "event_slot": row[4],
                "transit_time_utc": row[5],
                "observer_lat": row[6],
                "observer_lon": row[7],
                "predicted_offset_body_diameters": row[8],
            }
            for row in rows
        ]

    def observations_around(
        self,
        *,
        icao: str,
        timestamp: datetime,
        window_seconds: int,
    ) -> list[ActualObservation]:
        delta = timedelta(seconds=max(1, window_seconds))
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT observed_at, lat, lon, altitude_ft
                FROM aircraft_observations
                WHERE lower(icao) = lower(%s)
                  AND observed_at BETWEEN %s AND %s
                ORDER BY observed_at
                """,
                (icao, timestamp - delta, timestamp + delta),
            )
            rows = cur.fetchall()
        return [ActualObservation(row[0], row[1], row[2], row[3]) for row in rows]

    def insert_validation(
        self,
        event: dict,
        result: ValidationResult | None,
        message: str,
        validated_at: datetime,
        notified_at: datetime | None = None,
    ) -> bool:
        result_name = result.result if result else "NO_DATA"
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transit_validations (
                    source_alert_id, icao, callsign, body, event_slot,
                    predicted_transit_time_utc, actual_closest_time_utc,
                    observer_lat, observer_lon, predicted_offset_body_diameters,
                    actual_offset_body_diameters, actual_separation_deg,
                    vertical_offset_body_diameters, horizontal_offset_body_diameters,
                    result, message, validated_at, notified_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (icao, body, event_slot) DO NOTHING
                RETURNING id
                """,
                (
                    event["alert_id"],
                    event["icao"].lower(),
                    event.get("callsign"),
                    event["body"].lower(),
                    event["event_slot"],
                    event["transit_time_utc"],
                    result.actual_closest_time_utc if result else None,
                    event["observer_lat"],
                    event["observer_lon"],
                    event["predicted_offset_body_diameters"],
                    result.actual_offset_body_diameters if result else None,
                    result.actual_separation_deg if result else None,
                    result.vertical_offset_body_diameters if result else None,
                    result.horizontal_offset_body_diameters if result else None,
                    result_name,
                    message,
                    validated_at,
                    notified_at,
                ),
            )
            inserted = cur.fetchone() is not None
        self.conn.commit()
        return inserted

    def unsent_validations(self, limit: int = 20) -> list[tuple[int, str]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, message
                FROM transit_validations
                WHERE notified_at IS NULL
                ORDER BY validated_at
                LIMIT %s
                """,
                (max(1, limit),),
            )
            rows = cur.fetchall()
        # Close the read transaction as well. During STANDBY there may be no
        # following write, and an idle transaction would block UI migrations.
        self.conn.commit()
        return [(row[0], row[1]) for row in rows]

    def mark_validation_notified(self, validation_id: int, notified_at: datetime) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE transit_validations SET notified_at=%s WHERE id=%s AND notified_at IS NULL",
                (notified_at, validation_id),
            )
        self.conn.commit()
