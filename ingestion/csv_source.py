"""CSV files. Headers go through drift detection; rows are re-read every run and only
rows whose normalised content changed are written (content checkpoint)."""

import csv
import datetime as dt
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ingestion.base import Source
from ingestion.drift import DriftReport, check_drift
from schemas.coin import CsvCoinRecord


class CsvSource(Source):
    model = CsvCoinRecord
    incremental = "content"

    def __init__(self, name: str, path_env: str, default_paths: tuple[str, ...]):
        self.name = name
        self.path_env = path_env
        self.default_paths = default_paths
        self._report: DriftReport | None = None
        self._file_time: dt.datetime | None = None

    @property
    def path(self) -> Path:
        configured = os.getenv(self.path_env)
        if configured:
            return Path(configured)
        for candidate in self.default_paths:
            if Path(candidate).exists():
                return Path(candidate)
        return Path(self.default_paths[0])

    def prepare(self) -> DriftReport:
        path = self.path
        with path.open(newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f), [])
        self._file_time = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.UTC).replace(microsecond=0)
        self._report = check_drift(self.name, [h.strip() for h in headers])
        return self._report

    def fetch(self) -> Iterator[dict[str, Any]]:
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = [h.strip() for h in next(reader, [])]
            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue
                yield dict(zip(headers, row, strict=False))

    def to_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self._report is not None, "prepare() must run before to_input()"
        mapped = self._report.remap(payload)
        if not (mapped.get("observed_at") or "").strip():
            mapped["observed_at"] = self._file_time
        return mapped
