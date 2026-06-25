from __future__ import annotations

from config import load_settings
from db import connect, run_migrations
from logging_config import configure_logging
from ui import run_ui_server


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
    with connect(settings.database_url) as conn:
        run_migrations(conn)
    run_ui_server(settings)


if __name__ == "__main__":
    main()
