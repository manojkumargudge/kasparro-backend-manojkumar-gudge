from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db, read_client
from services import runs as run_service

router = APIRouter(tags=["etl"], dependencies=[Depends(read_client)])


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record counts, price aggregates and per-source ETL summaries from ``etl_runs``."""
    return run_service.stats(db)


@router.get("/runs")
def list_runs(
    limit: int = Query(10, ge=1, le=100),
    source: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Most recent ETL runs (one row per run per source)."""
    return {"runs": [run_service.run_to_dict(r) for r in run_service.list_runs(db, limit, source)]}


@router.get("/compare-runs")
def compare_runs(
    source: str | None = None,
    window: int = Query(20, ge=2, le=200),
    min_samples: int = Query(5, ge=2, le=200),
    sigma: float = Query(3.0, gt=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Latest completed run per source vs. its recent history, with >sigma anomaly flags."""
    return run_service.compare_runs(db, source=source, window=window, min_samples=min_samples, sigma=sigma)
