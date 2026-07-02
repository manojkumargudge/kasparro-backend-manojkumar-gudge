import sys
import os
import traceback

# Add project root to Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from core.logger import get_logger

log = get_logger(__name__)


def run():
    try:
        # Import ingestion modules inside run() so import-time errors are caught
        from ingestion.coingecko import ingest_coingecko
        from ingestion.csvingest import ingest_csv
        from ingestion.coinpaprika import ingest_coins

        log.info("Starting scheduled ETL job")

        total_linked = 0
        total_errors = 0

        # run each ingestion and collect results (linked, errors)
        try:
            linked, errors = ingest_coingecko()
        except Exception as ex:
            log.error(f"CoinGecko ingestion raised exception: {ex}")
            linked, errors = 0, 1
        total_linked += linked or 0
        total_errors += errors or 0

        try:
            linked, errors = ingest_csv()
        except Exception as ex:
            log.error(f"CSV ingestion raised exception: {ex}")
            linked, errors = 0, 1
        total_linked += linked or 0
        total_errors += errors or 0

        try:
            linked, errors = ingest_coins()
        except Exception as ex:
            log.error(f"CoinPaprika ingestion raised exception: {ex}")
            linked, errors = 0, 1
        total_linked += linked or 0
        total_errors += errors or 0

        log.info(f"Scheduled ETL completed: {total_linked} records linked, {total_errors} errors")

        # Decide whether to fail the process based on environment flag
        fail_on_error = os.getenv("FAIL_ON_ERROR", "1").lower() in ("1", "true", "yes")
        if total_errors > 0:
            log.error("ETL finished with errors")
            if fail_on_error:
                log.error("FAIL_ON_ERROR is set — failing run")
                sys.exit(1)
            else:
                log.warning("FAIL_ON_ERROR not set — completing with success despite errors")
        else:
            log.info("ETL finished successfully")
    except Exception as e:
        log.error(f"Scheduled ETL failed: {e}")
        tb = traceback.format_exc()
        log.error(tb)
        # Exit non-zero so CI/cron runners receive a failure signal for real errors
        sys.exit(1)


if __name__ == "__main__":
    run()
