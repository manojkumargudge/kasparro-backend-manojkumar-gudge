from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.db import SessionLocal
from services.runs import health_summary

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> JSONResponse:
    """DB connectivity + the latest ETL run per source. 503 when the DB is unreachable;
    ``degraded`` when any source's latest run failed."""
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            etl = health_summary(db)
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "down", "db": False, "etl": None})
    failed = [s["source"] for s in etl["sources"] if s["status"] == "failed"]
    body: dict[str, Any] = {"status": "degraded" if failed else "ok", "db": True, "failed_sources": failed, "etl": etl}
    return JSONResponse(content=jsonable_encoder(body))
