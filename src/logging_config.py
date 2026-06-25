from __future__ import annotations

from datetime import date, datetime
import gzip
import logging
import os
import re
import shutil
import sys


LOG_FILE_RE = re.compile(r"^aircraft-transit-(\d{4}-\d{2}-\d{2})\.log(\.gz)?$")


class TZFormatter(logging.Formatter):
    default_msec_format = "%s.%03d"

    def formatTime(self, record, datefmt=None):  # noqa: N802
        from datetime import datetime

        dt = datetime.fromtimestamp(record.created).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + dt.strftime("%z")


class DailyDatedFileHandler(logging.Handler):
    def __init__(
        self,
        log_dir: str,
        *,
        retain_full_days: int = 3,
        retain_compressed_days: int = 10,
        emergency_free_mb: int = 1536,
    ) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.retain_full_days = max(1, retain_full_days)
        self.retain_compressed_days = max(self.retain_full_days, retain_compressed_days)
        self.emergency_free_mb = max(0, emergency_free_mb)
        self.current_date: str | None = None
        self.stream = None
        enforce_log_retention(
            self.log_dir,
            retain_full_days=self.retain_full_days,
            retain_compressed_days=self.retain_compressed_days,
            emergency_free_mb=self.emergency_free_mb,
        )

    def emit(self, record: logging.LogRecord) -> None:
        from datetime import datetime

        date = datetime.fromtimestamp(record.created).astimezone().strftime("%Y-%m-%d")
        if date != self.current_date:
            if self.stream:
                self.stream.close()
            self.current_date = date
            path = os.path.join(self.log_dir, f"aircraft-transit-{date}.log")
            self.stream = open(path, "a", encoding="utf-8")
            enforce_log_retention(
                self.log_dir,
                retain_full_days=self.retain_full_days,
                retain_compressed_days=self.retain_compressed_days,
                emergency_free_mb=self.emergency_free_mb,
                current_date=date,
            )
        self.stream.write(self.format(record) + "\n")
        self.stream.flush()

    def close(self) -> None:
        if self.stream:
            self.stream.close()
        super().close()


def _log_file_date(filename: str) -> tuple[date, bool] | None:
    match = LOG_FILE_RE.match(filename)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date(), bool(match.group(2))
    except ValueError:
        return None


def _log_files(log_dir: str) -> list[tuple[date, bool, str]]:
    files = []
    for filename in os.listdir(log_dir):
        parsed = _log_file_date(filename)
        if parsed:
            log_date, compressed = parsed
            files.append((log_date, compressed, os.path.join(log_dir, filename)))
    return sorted(files, key=lambda item: (item[0], item[1], item[2]))


def _free_mb(path: str) -> int:
    usage = shutil.disk_usage(path)
    return usage.free // (1024 * 1024)


def _compress_log(path: str) -> str:
    gz_path = f"{path}.gz"
    if os.path.exists(gz_path):
        os.remove(path)
        return gz_path
    with open(path, "rb") as source, gzip.open(gz_path, "wb") as target:
        shutil.copyfileobj(source, target)
    os.remove(path)
    return gz_path


def enforce_log_retention(
    log_dir: str,
    *,
    retain_full_days: int = 3,
    retain_compressed_days: int = 10,
    emergency_free_mb: int = 1536,
    current_date: str | None = None,
) -> None:
    if not os.path.isdir(log_dir):
        return
    today = datetime.now().astimezone().date()
    current = datetime.strptime(current_date, "%Y-%m-%d").date() if current_date else today
    full_cutoff = today.toordinal() - max(1, retain_full_days) + 1
    compressed_cutoff = today.toordinal() - max(retain_full_days, retain_compressed_days) + 1

    for log_date, compressed, path in _log_files(log_dir):
        if log_date == current:
            continue
        if compressed:
            if log_date.toordinal() < compressed_cutoff:
                os.remove(path)
        elif log_date.toordinal() < full_cutoff:
            _compress_log(path)

    if emergency_free_mb <= 0 or _free_mb(log_dir) >= emergency_free_mb:
        return

    for log_date, _compressed, path in _log_files(log_dir):
        if log_date == current:
            continue
        os.remove(path)
        if _free_mb(log_dir) >= emergency_free_mb:
            break


def configure_logging(
    level: str,
    log_to_file: bool,
    log_dir: str,
    *,
    retain_full_days: int = 3,
    retain_compressed_days: int = 10,
    emergency_free_mb: int = 1536,
) -> None:
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    formatter = TZFormatter("%(asctime)s | %(levelname)s | %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_to_file:
        handler = DailyDatedFileHandler(
            log_dir,
            retain_full_days=retain_full_days,
            retain_compressed_days=retain_compressed_days,
            emergency_free_mb=emergency_free_mb,
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
