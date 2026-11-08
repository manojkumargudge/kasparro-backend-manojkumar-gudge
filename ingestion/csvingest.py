"""CSV ingestion with checkpointed source ids (rows already ingested are skipped)."""

import csv
import datetime as dt
import logging
import os

from core.db import SessionLocal
from ingestion.common import IngestResult, finish_run, ingest_records, load_checkpoint_meta
from schemas.coin import CsvCoinRecord

log = logging.getLogger(__name__)

SOURCE = "csv"
DEFAULT_CSV_FILE = "data/coins.csv"


def _resolve_csv_path() -> str:
    csv_path = os.getenv("CSV_PATH")
    if csv_path:
        return csv_path
    for candidate in (DEFAULT_CSV_FILE, "data/sample_coins.csv"):
        if os.path.exists(candidate):
            return candidate
    return DEFAULT_CSV_FILE


def ingest_csv() -> IngestResult:
    log.info("Starting CSV ingestion")
    result = IngestResult(source=SOURCE)
    started_at = dt.datetime.now(dt.UTC)

    with SessionLocal() as db:
        processed_ids = set(load_checkpoint_meta(db, SOURCE).get("processed_ids", []))
        csv_path = _resolve_csv_path()
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                ingest_records(db, SOURCE, CsvCoinRecord, csv.DictReader(f), result, skip_ids=processed_ids)
        except OSError as exc:
            log.error("CSV ingestion could not read %s: %s", csv_path, exc)
            result.errors += 1

        processed_ids.update(result.stored_ids)
        return finish_run(db, result, started_at, {"processed_ids": sorted(processed_ids)})


if __name__ == "__main__":
    ingest_csv()
