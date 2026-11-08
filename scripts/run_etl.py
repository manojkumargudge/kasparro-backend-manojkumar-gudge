"""One-shot ETL runner: migrate the schema, then ingest every source.

Exit code is non-zero when any source reports fetch/database errors and
``FAIL_ON_ERROR`` is truthy (default). Records rejected by validation are logged
and counted but do not fail the run.
"""

import os
import sys
from collections.abc import Callable

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def run() -> int:
    try:
        from core.migrations import run_migrations
        from ingestion.coingecko import ingest_coingecko
        from ingestion.coinpaprika import ingest_coins
        from ingestion.common import IngestResult
        from ingestion.csvingest import ingest_csv

        run_migrations()
    except Exception:
        log.exception("ETL setup failed (imports or migrations)")
        return 1

    log.info("Starting ETL job")
    sources: list[tuple[str, Callable[[], IngestResult]]] = [
        ("coingecko", ingest_coingecko),
        ("csv", ingest_csv),
        ("coinpaprika", ingest_coins),
    ]
    linked = invalid = errors = 0
    for name, ingest in sources:
        try:
            result = ingest()
        except Exception:
            log.exception(f"{name} ingestion raised")
            errors += 1
            continue
        linked += result.linked
        invalid += result.invalid
        errors += result.errors

    log.info(f"ETL completed: linked={linked} invalid={invalid} errors={errors}")
    if errors and os.getenv("FAIL_ON_ERROR", "1").lower() in {"1", "true", "yes"}:
        log.error("ETL finished with errors and FAIL_ON_ERROR is set")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
