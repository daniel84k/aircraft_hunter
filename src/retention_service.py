from __future__ import annotations

import logging
import os
import time

from config import load_settings
from data_retention import run_data_retention
from db import connect, run_migrations
from logging_config import configure_logging


LOG = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    configure_logging(
        settings.log_level,
        settings.log_to_file,
        settings.log_dir,
        retain_full_days=settings.log_retain_full_days,
        retain_compressed_days=settings.log_retain_compressed_days,
        emergency_free_mb=settings.log_emergency_free_mb,
    )
    conn = connect(settings.database_url)
    run_migrations(conn)
    initial_delay_seconds = max(0, int(os.getenv("DATA_RETENTION_INITIAL_DELAY_SECONDS", "60")))
    LOG.info(
        "Data retention service started interval_seconds=%s initial_delay_seconds=%s",
        settings.data_retention_interval_seconds,
        initial_delay_seconds,
    )
    if initial_delay_seconds:
        time.sleep(initial_delay_seconds)

    while True:
        started = time.monotonic()
        try:
            run_data_retention(conn, settings)
        except Exception as exc:
            LOG.exception("Data retention failed error=%s", exc)
        elapsed = time.monotonic() - started
        time.sleep(max(60.0, settings.data_retention_interval_seconds - elapsed))


if __name__ == "__main__":
    main()
