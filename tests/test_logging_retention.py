from __future__ import annotations

from datetime import date, timedelta
import gzip

import logging_config


def _log_path(tmp_path, day: date):
    path = tmp_path / f"aircraft-transit-{day.isoformat()}.log"
    path.write_text(f"log for {day.isoformat()}\n", encoding="utf-8")
    return path


def test_log_retention_compresses_old_full_logs(tmp_path, monkeypatch) -> None:
    today = date(2026, 6, 25)
    monkeypatch.setattr(logging_config, "_free_mb", lambda _path: 9999)
    _log_path(tmp_path, today)
    old = _log_path(tmp_path, today - timedelta(days=3))

    logging_config.enforce_log_retention(
        str(tmp_path),
        retain_full_days=3,
        retain_compressed_days=10,
        current_date=today.isoformat(),
    )

    assert not old.exists()
    gz_path = tmp_path / f"{old.name}.gz"
    assert gz_path.exists()
    with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
        assert fh.read() == f"log for {(today - timedelta(days=3)).isoformat()}\n"


def test_log_retention_deletes_expired_compressed_logs(tmp_path, monkeypatch) -> None:
    today = date(2026, 6, 25)
    monkeypatch.setattr(logging_config, "_free_mb", lambda _path: 9999)
    expired_day = today - timedelta(days=10)
    gz_path = tmp_path / f"aircraft-transit-{expired_day.isoformat()}.log.gz"
    with gzip.open(gz_path, "wt", encoding="utf-8") as fh:
        fh.write("old\n")

    logging_config.enforce_log_retention(
        str(tmp_path),
        retain_full_days=3,
        retain_compressed_days=10,
        current_date=today.isoformat(),
    )

    assert not gz_path.exists()


def test_log_retention_emergency_keeps_current_log(tmp_path, monkeypatch) -> None:
    today = date(2026, 6, 25)
    old = _log_path(tmp_path, today - timedelta(days=1))
    current = _log_path(tmp_path, today)
    free_values = iter([100, 100, 2000])
    monkeypatch.setattr(logging_config, "_free_mb", lambda _path: next(free_values))

    logging_config.enforce_log_retention(
        str(tmp_path),
        retain_full_days=3,
        retain_compressed_days=10,
        emergency_free_mb=1536,
        current_date=today.isoformat(),
    )

    assert not old.exists()
    assert current.exists()
