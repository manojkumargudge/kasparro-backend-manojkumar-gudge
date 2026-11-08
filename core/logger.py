"""Structured JSON logging on stdout. Fields passed via ``extra=`` are included.

Each line also carries ``severity`` and ``time`` so Google Cloud Logging (Cloud Run) maps
it to the right log level and timestamp; the rest lands in ``jsonPayload``.
"""

import datetime as dt
import json
import logging
import sys
from typing import Any

# Python level -> Cloud Logging LogSeverity
SEVERITY = {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING", "ERROR": "ERROR", "CRITICAL": "CRITICAL"}

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(timespec="milliseconds"),
            "severity": SEVERITY.get(record.levelname, "DEFAULT"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    # uvicorn installs its own plain-text handlers before importing the app; route its
    # server and access logs through the JSON handler too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)


configure_logging()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
