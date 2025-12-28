import sys
import os

# Add project root to Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

from ingestion.coingecko import ingest_coingecko
from ingestion.csvingest import ingest_csv
from core.logger import get_logger

log = get_logger(__name__)

def run():
    try:
        log.info("Starting scheduled ETL job")
        ingest_coingecko()
        ingest_csv()
        log.info("Scheduled ETL completed successfully")
    except Exception as e:
        log.error(f"Scheduled ETL failed: {e}")

if __name__ == "__main__":
    run()
