from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
import time

from config import Settings


LOG = logging.getLogger(__name__)

INTERESTING_STATUSES = ("ALERT_READY", "OBSERVATION_CANDIDATE", "CANDIDATE_STORED")


@dataclass(frozen=True)
class RetentionResult:
    emergency: bool
    free_mb: int
    aircraft_observations: int
    rejected_candidates: int
    interesting_candidates: int
    trajectory_snapshots: int
    prediction_runs: int

    @property
    def total_deleted(self) -> int:
        return (
            self.aircraft_observations
            + self.rejected_candidates
            + self.interesting_candidates
            + self.trajectory_snapshots
            + self.prediction_runs
        )


def retention_due(last_run_monotonic: float | None, interval_seconds: int, now_monotonic: float | None = None) -> bool:
    if last_run_monotonic is None:
        return True
    now = time.monotonic() if now_monotonic is None else now_monotonic
    return now - last_run_monotonic >= max(60, int(interval_seconds))


def _free_mb(path: str) -> int:
    return shutil.disk_usage(path).free // (1024 * 1024)


def _delete_in_batches(conn, sql: str, params: tuple, batch_size: int) -> int:
    total = 0
    size = max(1, int(batch_size))
    while True:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (*params, size))
                deleted = max(0, cur.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        total += deleted
        if deleted < size:
            return total


def run_data_retention(conn, settings: Settings, *, free_space_path: str | None = None) -> RetentionResult | None:
    if not settings.data_retention_enabled:
        return None

    free_path = free_space_path or settings.log_dir or "."
    free_mb = _free_mb(free_path)
    emergency = free_mb < max(0, settings.data_retention_emergency_free_mb)
    observations_hours = (
        settings.data_retention_emergency_observations_hours
        if emergency
        else settings.data_retention_observations_hours
    )
    rejected_days = (
        settings.data_retention_emergency_rejected_candidate_days
        if emergency
        else settings.data_retention_rejected_candidate_days
    )

    batch_size = max(1, settings.data_retention_delete_batch_size)
    trajectory_snapshots = _delete_in_batches(
        conn,
        """
        WITH doomed AS (
          SELECT id
          FROM event_trajectory_snapshots
          WHERE updated_at < now() - (%s * interval '1 day')
          ORDER BY updated_at ASC
          LIMIT %s
        )
        DELETE FROM event_trajectory_snapshots s
        USING doomed
        WHERE s.id = doomed.id
        """,
        (max(1, settings.data_retention_trajectory_snapshot_days),),
        batch_size,
    )
    rejected_candidates = _delete_in_batches(
        conn,
        """
        WITH doomed AS (
          SELECT c.id
          FROM transit_candidates c
          WHERE c.created_at < now() - (%s * interval '1 day')
            AND c.status = 'REJECTED'
            AND NOT EXISTS (
              SELECT 1 FROM alerts a WHERE a.transit_candidate_id = c.id
            )
            AND NOT EXISTS (
              SELECT 1 FROM event_trajectory_snapshots s WHERE s.candidate_id = c.id
            )
          ORDER BY c.created_at ASC
          LIMIT %s
        )
        DELETE FROM transit_candidates c
        USING doomed
        WHERE c.id = doomed.id
        """,
        (max(1, rejected_days),),
        batch_size,
    )
    interesting_candidates = _delete_in_batches(
        conn,
        """
        WITH doomed AS (
          SELECT c.id
          FROM transit_candidates c
          WHERE c.created_at < now() - (%s * interval '1 day')
            AND c.status = ANY(%s)
            AND NOT EXISTS (
              SELECT 1 FROM alerts a WHERE a.transit_candidate_id = c.id
            )
            AND NOT EXISTS (
              SELECT 1 FROM event_trajectory_snapshots s WHERE s.candidate_id = c.id
            )
          ORDER BY c.created_at ASC
          LIMIT %s
        )
        DELETE FROM transit_candidates c
        USING doomed
        WHERE c.id = doomed.id
        """,
        (max(1, settings.data_retention_interesting_candidate_days), list(INTERESTING_STATUSES)),
        batch_size,
    )
    prediction_runs = _delete_in_batches(
        conn,
        """
        WITH doomed AS (
          SELECT r.id
          FROM prediction_runs r
          WHERE r.started_at < now() - (%s * interval '1 day')
            AND NOT EXISTS (
              SELECT 1 FROM transit_candidates c WHERE c.prediction_run_id = r.id
            )
          ORDER BY r.started_at ASC
          LIMIT %s
        )
        DELETE FROM prediction_runs r
        USING doomed
        WHERE r.id = doomed.id
        """,
        (max(1, settings.data_retention_prediction_run_days),),
        batch_size,
    )
    aircraft_observations = _delete_in_batches(
        conn,
        """
        WITH doomed AS (
          SELECT id
          FROM aircraft_observations
          WHERE observed_at < now() - (%s * interval '1 hour')
          ORDER BY observed_at ASC
          LIMIT %s
        )
        DELETE FROM aircraft_observations o
        USING doomed
        WHERE o.id = doomed.id
        """,
        (max(1, observations_hours),),
        batch_size,
    )

    result = RetentionResult(
        emergency=emergency,
        free_mb=free_mb,
        aircraft_observations=aircraft_observations,
        rejected_candidates=rejected_candidates,
        interesting_candidates=interesting_candidates,
        trajectory_snapshots=trajectory_snapshots,
        prediction_runs=prediction_runs,
    )
    LOG.info(
        "Data retention complete emergency=%s free_mb=%s deleted_observations=%s "
        "deleted_rejected_candidates=%s deleted_interesting_candidates=%s "
        "deleted_trajectory_snapshots=%s deleted_prediction_runs=%s",
        result.emergency,
        result.free_mb,
        result.aircraft_observations,
        result.rejected_candidates,
        result.interesting_candidates,
        result.trajectory_snapshots,
        result.prediction_runs,
    )
    return result
