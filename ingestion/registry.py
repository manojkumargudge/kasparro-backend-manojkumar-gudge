"""The sources the ETL runs, in order. ``ETL_SOURCES`` (comma-separated names) narrows it."""

import os

from ingestion.base import Source
from ingestion.coingecko import CoinGeckoSource
from ingestion.coinpaprika import CoinPaprikaSource
from ingestion.csv_source import CsvSource


def all_sources() -> list[Source]:
    return [
        CoinGeckoSource(),
        CsvSource("csv", "CSV_PATH", ("data/coins.csv", "data/sample_coins.csv")),
        CsvSource("quirky_csv", "QUIRKY_CSV_PATH", ("data/quirky_coins.csv",)),
        CoinPaprikaSource(),
    ]


def selected_sources() -> list[Source]:
    wanted = {s.strip() for s in os.getenv("ETL_SOURCES", "").split(",") if s.strip()}
    sources = all_sources()
    return [s for s in sources if s.name in wanted] if wanted else sources
