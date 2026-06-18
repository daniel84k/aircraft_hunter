from __future__ import annotations

import logging
import time

import psycopg


LOG = logging.getLogger(__name__)


def connect(database_url: str, attempts: int = 30, delay_seconds: float = 2.0):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(database_url)
        except psycopg.OperationalError as exc:
            last_error = exc
            LOG.warning("Database connection failed attempt=%s/%s error=%s", attempt, attempts, exc)
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not connect to database after {attempts} attempts") from last_error


def run_migrations(conn, migration_path: str = "migrations/001_init.sql") -> None:
    with open(migration_path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    LOG.info("Database migrations applied")
