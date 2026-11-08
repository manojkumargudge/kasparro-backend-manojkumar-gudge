"""Prometheus metrics. HTTP metrics are in-process; ETL/data metrics are read from Postgres
at scrape time, so every instance reports the same ETL state. Labels carry only source
names, statuses, HTTP methods and route templates: never keys or request data."""

from collections.abc import Iterator

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from core.db import SessionLocal
from core.models import Coin, EtlRun, PriceSnapshot
from services.runs import latest_per_source

REGISTRY = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "kasparro_http_requests",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "kasparro_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ["route"],
    registry=REGISTRY,
)
RATE_LIMITED = Counter(
    "kasparro_api_rate_limited",
    "Requests rejected with 429 by the API rate limiter.",
    registry=REGISTRY,
)


class DatabaseCollector(Collector):
    def collect(self) -> Iterator[GaugeMetricFamily | CounterMetricFamily]:
        up = GaugeMetricFamily("kasparro_db_up", "1 if the database answered the metrics query.")
        try:
            families = list(self._collect_db())
        except SQLAlchemyError:
            up.add_metric([], 0)
            yield up
            return
        up.add_metric([], 1)
        yield up
        yield from families

    def _collect_db(self) -> Iterator[GaugeMetricFamily | CounterMetricFamily]:
        with SessionLocal() as db:
            coins = GaugeMetricFamily("kasparro_coins", "Canonical coins stored.")
            coins.add_metric([], db.execute(select(func.count(Coin.id))).scalar_one())
            observations = GaugeMetricFamily("kasparro_observations", "Price observations stored.")
            observations.add_metric([], db.execute(select(func.count(PriceSnapshot.id))).scalar_one())

            runs = CounterMetricFamily(
                "kasparro_etl_runs", "ETL runs by source and status.", labels=["source", "status"]
            )
            for source, status, n in db.execute(
                select(EtlRun.source, EtlRun.status, func.count()).group_by(EtlRun.source, EtlRun.status)
            ):
                runs.add_metric([source, status], n)

            success = GaugeMetricFamily(
                "kasparro_etl_last_run_success", "1 if the latest run succeeded (success/partial).", labels=["source"]
            )
            started = GaugeMetricFamily(
                "kasparro_etl_last_run_timestamp_seconds", "Start time of the latest run.", labels=["source"]
            )
            duration = GaugeMetricFamily(
                "kasparro_etl_last_run_duration_seconds", "Duration of the latest run.", labels=["source"]
            )
            records = GaugeMetricFamily(
                "kasparro_etl_last_run_records", "Records in the latest run by outcome.", labels=["source", "outcome"]
            )
            drift = GaugeMetricFamily(
                "kasparro_etl_last_run_schema_confidence",
                "Schema-drift confidence of the latest run.",
                labels=["source"],
            )
            for run in latest_per_source(db):
                success.add_metric([run.source], 1 if run.status in ("success", "partial") else 0)
                started.add_metric([run.source], run.started_at.timestamp())
                duration.add_metric([run.source], run.duration_seconds or 0.0)
                for outcome in ("in", "inserted", "updated", "unchanged", "invalid"):
                    records.add_metric([run.source, outcome], getattr(run, f"records_{outcome}"))
                if run.drift and "confidence" in run.drift:
                    drift.add_metric([run.source], float(run.drift["confidence"]))
            yield from (coins, observations, runs, success, started, duration, records, drift)


REGISTRY.register(DatabaseCollector())
