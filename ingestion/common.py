"""Shared ETL plumbing: HTTP fetch with rate limit + backoff, raw persistence,
record validation, normalisation and checkpointing."""

import datetime as dt
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import requests
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.models import Checkpoint, RawCoin, get_or_create_coin, link_source, record_price_snapshot
from schemas.coin import NormalizedCoin, SourceRecord

log = logging.getLogger(__name__)

_LAST_CALL: dict[str, float] = {}

T = TypeVar("T", bound=SourceRecord)


class FetchError(RuntimeError):
    """Raised when a source API cannot be fetched after all retries."""


@dataclass
class IngestResult:
    source: str
    linked: int = 0
    invalid: int = 0  # records rejected by validation
    errors: int = 0  # fetch / database failures
    source_count: int = 0
    stored_ids: list[str] = field(default_factory=list)  # source ids stored this run

    @property
    def status(self) -> str:
        if self.errors and not self.linked:
            return "failed"
        if self.errors or self.invalid:
            return "partial"
        return "success"


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def fetch_json(
    source: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
) -> Any:
    """GET ``url`` with a per-source minimum interval and exponential backoff.

    Tunables (per source, upper-cased): ``<SOURCE>_MAX_RETRIES``, ``<SOURCE>_MIN_INTERVAL``.
    """
    prefix = source.upper()
    max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", "5"))
    min_interval = _env_float(f"{prefix}_MIN_INTERVAL", 1.0)

    last = _LAST_CALL.get(source)
    if last is not None:
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            log.info("%s rate limit: sleeping %.2fs", source, wait)
            time.sleep(wait)

    for attempt in range(1, max_retries + 1):
        _LAST_CALL[source] = time.monotonic()
        try:
            response = requests.get(url, headers=dict(headers or {}), params=params, timeout=10)
            response.raise_for_status()
            log.info("%s API success (attempt %d)", source, attempt)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            # Log the exception type/status only: request headers may carry API keys.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            log.error("%s API failed (attempt %d): %s status=%s", source, attempt, type(exc).__name__, status)
            if attempt == max_retries:
                raise FetchError(f"{source}: giving up after {max_retries} attempts") from exc
            backoff = min_interval * (2 ** (attempt - 1))
            log.info("%s retrying after %.2fs", source, backoff)
            time.sleep(backoff)
    raise FetchError(f"{source}: no attempts made (MAX_RETRIES={max_retries})")


def persist_raw(db: Session, source: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str)
    row_hash = hashlib.sha256(f"{source}:{raw}".encode()).hexdigest()
    if not db.query(RawCoin.id).filter_by(row_hash=row_hash).first():
        db.add(RawCoin(source=source, row_hash=row_hash, raw=raw, processed=False))
        db.commit()
    return row_hash


def validate_record(model: type[T], payload: Any, *, source: str, index: int, result: IngestResult) -> T | None:
    """Return the validated record, or ``None`` after logging and counting it as invalid."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        result.invalid += 1
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors())
        log.warning("%s record %d rejected by validation: %s", source, index, problems)
        return None


def store_coin(db: Session, source: str, coin: NormalizedCoin, row_hash: str) -> None:
    canonical = get_or_create_coin(db=db, symbol=coin.symbol, name=coin.name)
    canonical.price_usd = coin.price_usd
    canonical.market_cap = coin.market_cap
    link_source(db=db, coin_id=canonical.id, source=source, source_coin_id=coin.source_coin_id)
    record_price_snapshot(db, canonical.id, coin.price_usd, coin.market_cap)
    db.execute(update(RawCoin).where(RawCoin.row_hash == row_hash).values(processed=True))
    db.commit()


def ingest_records(
    db: Session,
    source: str,
    model: type[T],
    payloads: Iterable[Any],
    result: IngestResult,
    *,
    skip_ids: set[str] | None = None,
) -> None:
    """Persist raw, validate, normalise and store each payload (ids go to ``result.stored_ids``)."""
    for index, payload in enumerate(payloads):
        result.source_count += 1
        try:
            row_hash = persist_raw(db, source, payload if isinstance(payload, Mapping) else {"value": payload})
            record = validate_record(model, payload, source=source, index=index, result=result)
            if record is None:
                continue
            coin = record.to_coin()
            if skip_ids is not None and coin.source_coin_id in skip_ids:
                continue
            store_coin(db, source, coin, row_hash)
            result.linked += 1
            result.stored_ids.append(coin.source_coin_id)
        except (SQLAlchemyError, ValidationError) as exc:
            db.rollback()
            result.errors += 1
            log.error("%s record %d failed to store: %s", source, index, exc)


def load_checkpoint_meta(db: Session, source: str) -> dict[str, Any]:
    cp = db.execute(select(Checkpoint).filter_by(source=source)).scalar_one_or_none()
    if not cp or not cp.last_value:
        return {}
    try:
        meta = json.loads(cp.last_value)
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def save_checkpoint(
    db: Session,
    result: IngestResult,
    started_at: dt.datetime,
    extra: Mapping[str, Any] | None = None,
) -> None:
    ended_at = dt.datetime.now(dt.UTC)
    run_meta: dict[str, Any] = {
        "start_time": started_at.isoformat(),
        "end_time": ended_at.isoformat(),
        "duration_seconds": round((ended_at - started_at).total_seconds(), 3),
        "linked": result.linked,
        "invalid": result.invalid,
        "errors": result.errors,
        "status": result.status,
    }
    meta: dict[str, Any] = {
        "source_count": result.source_count,
        "linked": result.linked,
        "invalid": result.invalid,
        "errors": result.errors,
        **(extra or {}),
    }
    meta["run_meta"] = {**run_meta, **meta.get("run_meta", {})}
    value = json.dumps(meta)

    cp = db.execute(select(Checkpoint).filter_by(source=result.source)).scalar_one_or_none()
    if cp:
        cp.last_run = ended_at
        cp.last_value = value
    else:
        db.add(Checkpoint(source=result.source, last_run=ended_at, last_value=value))
    db.commit()


def finish_run(
    db: Session,
    result: IngestResult,
    started_at: dt.datetime,
    extra: Mapping[str, Any] | None = None,
) -> IngestResult:
    try:
        save_checkpoint(db, result, started_at, extra)
    except SQLAlchemyError as exc:
        db.rollback()
        result.errors += 1
        log.error("%s checkpoint save failed: %s", result.source, exc)
    log.info(
        "%s ingestion done: linked=%d invalid=%d errors=%d status=%s",
        result.source,
        result.linked,
        result.invalid,
        result.errors,
        result.status,
    )
    return result
