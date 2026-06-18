from __future__ import annotations

import logging
import os
import sys


class TZFormatter(logging.Formatter):
    default_msec_format = "%s.%03d"

    def formatTime(self, record, datefmt=None):  # noqa: N802
        from datetime import datetime

        dt = datetime.fromtimestamp(record.created).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + dt.strftime("%z")


class DailyDatedFileHandler(logging.Handler):
    def __init__(self, log_dir: str) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.current_date: str | None = None
        self.stream = None

    def emit(self, record: logging.LogRecord) -> None:
        from datetime import datetime

        date = datetime.fromtimestamp(record.created).astimezone().strftime("%Y-%m-%d")
        if date != self.current_date:
            if self.stream:
                self.stream.close()
            self.current_date = date
            path = os.path.join(self.log_dir, f"aircraft-transit-{date}.log")
            self.stream = open(path, "a", encoding="utf-8")
        self.stream.write(self.format(record) + "\n")
        self.stream.flush()

    def close(self) -> None:
        if self.stream:
            self.stream.close()
        super().close()


def configure_logging(level: str, log_to_file: bool, log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    formatter = TZFormatter("%(asctime)s | %(levelname)s | %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_to_file:
        handler = DailyDatedFileHandler(log_dir)
        handler.setFormatter(formatter)
        root.addHandler(handler)
