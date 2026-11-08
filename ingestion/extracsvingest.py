"""Extra CSV ingestion with header drift detection and optional failure injection.

``FAILURE_INJECT=1`` + ``FAIL_AFTER=N`` makes row N raise; the run records it as an error.
"""

import csv
import datetime as dt
import logging
import os
from collections.abc import Iterator
from typing import Any

from core.db import SessionLocal
from ingestion.common import IngestResult, finish_run, ingest_records, load_checkpoint_meta
from schemas.coin import CsvCoinRecord

log = logging.getLogger(__name__)

SOURCE = "extra_csv"
DEFAULT_EXTRA_CSV_FILE = "data/extra_coins.csv"
EXPECTED_COLUMNS = {"symbol", "name", "price_usd", "market_cap"}


def _resolve_extra_csv_path() -> str:
    extra_csv_path = os.getenv("EXTRA_CSV_PATH")
    if extra_csv_path:
        return extra_csv_path
    for candidate in (DEFAULT_EXTRA_CSV_FILE, "data/extra_coins.csv.csv"):
        if os.path.exists(candidate):
            return candidate
    return DEFAULT_EXTRA_CSV_FILE


def schema_confidence(columns: set[str]) -> float:
    confidence = len(EXPECTED_COLUMNS & columns) / len(EXPECTED_COLUMNS)
    missing, extra = EXPECTED_COLUMNS - columns, columns - EXPECTED_COLUMNS
    if confidence < 0.8:
        log.warning("Schema drift detected! Confidence: %.2f. Missing: %s. Extra: %s", confidence, missing, extra)
    else:
        log.info("Schema match confidence: %.2f", confidence)
    return confidence


def ingest_extra_csv() -> IngestResult:
    log.info("Starting Extra CSV ingestion")
    result = IngestResult(source=SOURCE)
    started_at = dt.datetime.now(dt.UTC)
    fail_after = int(os.getenv("FAIL_AFTER", "0")) if os.getenv("FAILURE_INJECT", "0") == "1" else 0
    run_meta: dict[str, Any] = {"fail_injected": False}

    with SessionLocal() as db:
        meta = load_checkpoint_meta(db, SOURCE)
        run_meta["resume"] = bool(meta)
        processed_ids = set(meta.get("processed_ids", []))
        path = _resolve_extra_csv_path()
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                run_meta["schema_confidence"] = schema_confidence(set(reader.fieldnames or []))

                def rows() -> Iterator[dict[str, Any]]:
                    for idx, row in enumerate(reader):
                        if fail_after and idx == fail_after:
                            log.error("Injected ETL failure after %d records", fail_after)
                            run_meta["fail_injected"] = True
                            raise RuntimeError("Injected ETL failure for testing recovery")
                        yield row

                ingest_records(db, SOURCE, CsvCoinRecord, rows(), result, skip_ids=processed_ids)
        except (OSError, RuntimeError) as exc:
            log.error("Extra CSV ingestion stopped: %s", exc)
            result.errors += 1

        processed_ids.update(result.stored_ids)
        return finish_run(
            db, result, started_at, {"processed_ids": sorted(processed_ids), "run_meta": run_meta}
        )


if __name__ == "__main__":
    ingest_extra_csv()
