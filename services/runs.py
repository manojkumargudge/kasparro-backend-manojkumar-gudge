"""Read side of ``etl_runs``: run listings, stats, run comparison and anomaly detection."""

import statistics
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Coin, EtlRun, PriceSnapshot
from core.redact import redact

COMPLETED = ("success", "partial")
ANOMALY_METRICS = ("records_in", "duration_seconds")


def run_to_dict(run: EtlRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_group": run.run_group,
        "source": run.source,
        "status": run.status,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "duration_seconds": run.duration_seconds,
        "records_in": run.records_in,
        "records_inserted": run.records_inserted,
        "records_updated": run.records_updated,
        "records_unchanged": run.records_unchanged,
        "records_invalid": run.records_invalid,
        "error": redact(run.error),
        "checkpoint": run.checkpoint,
        "drift": run.drift,
        "meta": run.meta,
    }


def list_runs(db: Session, limit: int, source: str | None = None) -> list[EtlRun]:
    q = select(EtlRun).order_by(EtlRun.started_at.desc(), EtlRun.id.desc()).limit(limit)
    if source:
        q = q.where(EtlRun.source == source)
    return list(db.execute(q).scalars())


def latest_per_source(db: Session) -> list[EtlRun]:
    q = select(EtlRun).distinct(EtlRun.source).order_by(EtlRun.source, EtlRun.started_at.desc(), EtlRun.id.desc())
    return list(db.execute(q).scalars())


def _last_at(db: Session, source: str, statuses: tuple[str, ...]) -> datetime | None:
    return db.execute(
        select(func.max(EtlRun.ended_at)).where(EtlRun.source == source, EtlRun.status.in_(statuses))
    ).scalar()


def source_summaries(db: Session) -> list[dict[str, Any]]:
    totals = {
        row.source: row
        for row in db.execute(
            select(
                EtlRun.source,
                func.count().label("runs"),
                func.coalesce(func.sum(EtlRun.records_in), 0).label("records_in"),
                func.coalesce(func.sum(EtlRun.records_inserted), 0).label("records_inserted"),
                func.coalesce(func.sum(EtlRun.records_updated), 0).label("records_updated"),
                func.coalesce(func.sum(EtlRun.records_invalid), 0).label("records_invalid"),
                func.avg(EtlRun.duration_seconds).label("avg_duration"),
            ).group_by(EtlRun.source)
        )
    }
    out = []
    for run in latest_per_source(db):
        t = totals[run.source]
        out.append(
            {
                "source": run.source,
                "runs": t.runs,
                "records_processed": int(t.records_in),
                "records_inserted": int(t.records_inserted),
                "records_updated": int(t.records_updated),
                "records_invalid": int(t.records_invalid),
                "avg_duration_seconds": round(float(t.avg_duration), 3) if t.avg_duration is not None else None,
                "last_success_at": _last_at(db, run.source, ("success",)),
                "last_failure_at": _last_at(db, run.source, ("failed",)),
                "last_run": run_to_dict(run),
            }
        )
    return out


def stats(db: Session) -> dict[str, Any]:
    coins, min_p, max_p, avg_p = db.execute(
        select(func.count(Coin.id), func.min(Coin.price_usd), func.max(Coin.price_usd), func.avg(Coin.price_usd))
    ).one()
    sources = source_summaries(db)
    last_runs = [s["last_run"] for s in sources]
    successes = [s["last_success_at"] for s in sources if s["last_success_at"]]
    failures = [s["last_failure_at"] for s in sources if s["last_failure_at"]]
    return {
        "total_records": coins,
        "observations": db.execute(select(func.count(PriceSnapshot.id))).scalar(),
        "price_stats": {
            "min": float(min_p) if min_p is not None else None,
            "max": float(max_p) if max_p is not None else None,
            "avg": float(avg_p) if avg_p is not None else None,
        },
        "etl": {
            "records_processed": sum(s["records_processed"] for s in sources),
            "total_runs": sum(s["runs"] for s in sources),
            "last_success_at": max(successes) if successes else None,
            "last_failure_at": max(failures) if failures else None,
            "last_duration_seconds": {r["source"]: r["duration_seconds"] for r in last_runs},
            "sources": sources,
        },
    }


def _metric_check(latest: float | None, baseline: list[float], min_samples: int, sigma: float) -> dict[str, Any]:
    check: dict[str, Any] = {"latest": latest, "samples": len(baseline), "mean": None, "stdev": None, "z": None}
    check["anomaly"] = False
    if latest is None or len(baseline) < min_samples:
        check["reason"] = "insufficient history"
        return check
    mean = statistics.fmean(baseline)
    stdev = statistics.stdev(baseline) if len(baseline) > 1 else 0.0
    check.update(mean=round(mean, 3), stdev=round(stdev, 3))
    if stdev == 0:
        # Flat history: any deviation is infinitely many sigmas away (z reported as null).
        check["anomaly"] = latest != mean
        check["z"] = 0.0 if latest == mean else None
        if check["anomaly"]:
            check["reason"] = "zero variance in history"
        return check
    check["z"] = round((latest - mean) / stdev, 2)
    check["anomaly"] = abs(check["z"]) > sigma
    return check


def compare_runs(
    db: Session, source: str | None = None, window: int = 20, min_samples: int = 5, sigma: float = 3.0
) -> dict[str, Any]:
    """Compare each source's latest completed run with its recent history.

    A metric is anomalous when ``|z| > sigma`` against the previous ``window`` completed runs,
    and only once at least ``min_samples`` of them exist.
    """
    sources = [source] if source else list(db.execute(select(EtlRun.source).distinct()).scalars())
    report: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    for name in sorted(sources):
        latest_any = list_runs(db, 1, name)
        completed = list(
            db.execute(
                select(EtlRun)
                .where(EtlRun.source == name, EtlRun.status.in_(COMPLETED))
                .order_by(EtlRun.started_at.desc(), EtlRun.id.desc())
                .limit(window + 1)
            ).scalars()
        )
        entry: dict[str, Any] = {"source": name, "history": [], "metrics": {}, "anomalies": []}
        entry["history"] = [
            {
                "id": r.id,
                "started_at": r.started_at,
                "status": r.status,
                "records_in": r.records_in,
                "records_inserted": r.records_inserted,
                "records_invalid": r.records_invalid,
                "duration_seconds": r.duration_seconds,
            }
            for r in completed
        ]
        anomalies: list[dict[str, Any]] = []
        if latest_any and latest_any[0].status == "failed":
            anomalies.append(
                {
                    "metric": "status",
                    "anomaly": "latest run failed",
                    "run_id": latest_any[0].id,
                    "error": redact(latest_any[0].error),
                }
            )
        if completed:
            latest, baseline = completed[0], completed[1:]
            entry["latest_run_id"] = latest.id
            if baseline:
                prev = baseline[0]
                entry["delta_vs_previous"] = {
                    "records_in": latest.records_in - prev.records_in,
                    "duration_seconds": round((latest.duration_seconds or 0) - (prev.duration_seconds or 0), 3),
                }
            for metric in ANOMALY_METRICS:
                values = [float(getattr(r, metric)) for r in baseline if getattr(r, metric) is not None]
                value = getattr(latest, metric)
                check = _metric_check(float(value) if value is not None else None, values, min_samples, sigma)
                entry["metrics"][metric] = check
                if check["anomaly"]:
                    anomalies.append(
                        {
                            "metric": metric,
                            "run_id": latest.id,
                            "anomaly": f"{metric}={value} is {check['z']}σ from mean {check['mean']}",
                            "z": check["z"],
                        }
                    )
        entry["anomalies"] = anomalies
        report.append(entry)
        for a in anomalies:
            started = next((h["started_at"] for h in entry["history"] if h["id"] == a.get("run_id")), None)
            flat.append(
                {"source": name, "last_run": started or (latest_any[0].started_at if latest_any else None), **a}
            )
    return {
        "params": {"window": window, "min_samples": min_samples, "sigma": sigma},
        "sources": report,
        "anomalies": flat,
    }


def health_summary(db: Session) -> dict[str, Any]:
    latest = latest_per_source(db)
    sources = [
        {
            "source": r.source,
            "status": r.status,
            "last_run": r.started_at,
            "ended_at": r.ended_at,
            "duration_seconds": r.duration_seconds,
            "records_in": r.records_in,
            "records_invalid": r.records_invalid,
            "error": redact(r.error),
        }
        for r in latest
    ]
    newest = max(latest, key=lambda r: r.started_at, default=None)
    return {
        "latest_source": newest.source if newest else None,
        "latest_status": newest.status if newest else None,
        "latest_run_at": newest.started_at if newest else None,
        "sources": sources,
    }
