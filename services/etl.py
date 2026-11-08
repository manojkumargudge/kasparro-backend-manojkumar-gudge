"""ETL orchestration.

For every source run one ``etl_runs`` row is written (status ``running`` → ``success`` /
``partial`` / ``failed``). Records are processed in batches of ``ETL_BATCH_SIZE``; each batch
commits raw rows, normalised rows, the source checkpoint and the run counters in a single
transaction, so a crash loses at most the current batch and the next run resumes from the
last committed checkpoint. Writes are idempotent upserts, so re-processing never duplicates.

Failure injection: ``FAIL_AFTER_N=<n>`` crashes a source run after ``n`` records
(optionally only for ``FAIL_SOURCES=a,b``). ``FAIL_MODE=raise`` (default) raises inside the
run; ``FAIL_MODE=exit`` kills the process immediately (``os._exit``), like an OOM kill.
Runs left ``running`` by a dead process are marked ``failed`` (abandoned) by the next run.
"""

import datetime as dt
import json
import logging
import os
import time
import uuid
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.config import env_int, env_str
from core.db import SessionLocal, engine
from core.models import ApiRateLimit, Checkpoint, EtlRun
from ingestion.base import Source
from ingestion.drift import DriftRejected
from ingestion.registry import selected_sources
from schemas.coin import SourceRecord
from services import store

log = logging.getLogger(__name__)

CRASH_EXIT_CODE = 70


class InjectedCrash(RuntimeError):
    """Raised by ``FAIL_AFTER_N`` failure injection."""


@dataclass
class SourceRunResult:
    source: str
    status: str
    run_id: int | None = None
    records_in: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_unchanged: int = 0
    records_invalid: int = 0
    duration_seconds: float | None = None
    error: str | None = None


# --------------------------------------------------------------------------- locking
def _lock_key(source: str) -> int:
    return zlib.crc32(f"kasparro-etl:{source}".encode())


@contextmanager
def source_lock(source: str) -> Iterator[bool]:
    """Session-level advisory lock so two ETL processes never run the same source at once."""
    with engine.connect() as conn:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(source)}).scalar())
        conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(source)})
                conn.commit()


# --------------------------------------------------------------------------- checkpoints
def load_tokens(db: Session, source: str) -> dict[str, str]:
    value = db.execute(select(Checkpoint.last_value).where(Checkpoint.source == source)).scalar()
    try:
        state = json.loads(value or "{}")
    except ValueError:
        return {}
    tokens = state.get("tokens") if isinstance(state, dict) else None
    return {str(k): str(v) for k, v in tokens.items()} if isinstance(tokens, dict) else {}


def save_tokens(db: Session, source: str, tokens: dict[str, str], run_id: int) -> None:
    value = json.dumps({"version": 2, "run_id": run_id, "tokens": tokens}, sort_keys=True)
    stmt = insert(Checkpoint).values(source=source, last_value=value, last_run=func.now())
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[Checkpoint.source], set_={"last_value": value, "last_run": func.now()}
        )
    )


# --------------------------------------------------------------------------- run
def _validate(source: Source, payload: dict[str, Any], index: int) -> SourceRecord | None:
    try:
        return source.model.model_validate(source.to_input(payload))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors())
        log.warning(
            "%s record %d rejected by validation: %s",
            source.name,
            index,
            problems,
            extra={"source": source.name, "event": "record_invalid"},
        )
        return None


def _crash_after(source: str) -> int:
    targets = {s.strip() for s in env_str("FAIL_SOURCES", "").split(",") if s.strip()}
    if targets and source not in targets:
        return 0
    return env_int("FAIL_AFTER_N", 0)


def _inject_crash(source: str, processed: int) -> None:
    log.error("Injected crash in %s after %d records", source, processed, extra={"source": source})
    if env_str("FAIL_MODE", "raise") == "exit":
        logging.shutdown()
        os._exit(CRASH_EXIT_CODE)
    raise InjectedCrash(f"injected crash after {processed} records")


def _abandon_stale_runs(db: Session, source: str) -> None:
    stale = db.execute(
        update(EtlRun)
        .where(EtlRun.source == source, EtlRun.status == "running")
        .values(status="failed", ended_at=func.now(), error="abandoned: the process exited before the run finished")
        .returning(EtlRun.id)
    ).all()
    if stale:
        log.warning("Marked %d abandoned %s run(s) as failed", len(stale), source, extra={"source": source})
    db.commit()


def _result(run: EtlRun) -> SourceRunResult:
    return SourceRunResult(
        source=run.source,
        status=run.status,
        run_id=run.id,
        records_in=run.records_in,
        records_inserted=run.records_inserted,
        records_updated=run.records_updated,
        records_unchanged=run.records_unchanged,
        records_invalid=run.records_invalid,
        duration_seconds=run.duration_seconds,
        error=run.error,
    )


def _run_locked(source: Source, run_group: str) -> SourceRunResult:
    batch_size = max(1, env_int("ETL_BATCH_SIZE", 25))
    crash_after = _crash_after(source.name)
    with SessionLocal() as db:
        _abandon_stale_runs(db, source.name)
        run = EtlRun(
            run_group=run_group,
            source=source.name,
            status="running",
            started_at=dt.datetime.now(dt.UTC),
            meta={"batch_size": batch_size, "incremental": source.incremental, "fail_after_n": crash_after or None},
        )
        db.add(run)
        db.commit()
        clock = time.monotonic()
        tokens = load_tokens(db, source.name)
        counts = {"in": 0, "inserted": 0, "updated": 0, "unchanged": 0, "invalid": 0}
        batches = 0

        def commit_batch() -> None:
            nonlocal batches
            batches += 1
            save_tokens(db, source.name, tokens, run.id)
            run.records_in = counts["in"]
            run.records_inserted = counts["inserted"]
            run.records_updated = counts["updated"]
            run.records_unchanged = counts["unchanged"]
            run.records_invalid = counts["invalid"]
            run.checkpoint = {"batches_committed": batches, "records_committed": counts["in"], "keys": len(tokens)}
            db.commit()

        try:
            report = source.prepare()
            if report is not None:
                run.drift = report.model_dump(mode="json")
                db.commit()
            for index, payload in enumerate(source.fetch()):
                if crash_after and counts["in"] >= crash_after:
                    _inject_crash(source.name, counts["in"])
                counts["in"] += 1
                row_hash = store.store_raw(db, source.name, payload)
                record = _validate(source, payload, index)
                if record is None:
                    counts["invalid"] += 1
                else:
                    coin = record.to_coin()
                    token = source.change_token(coin)
                    if source.is_changed(tokens.get(coin.source_coin_id), token):
                        counts[store.store_coin(db, source.name, coin)] += 1
                        tokens[coin.source_coin_id] = token
                    else:
                        counts["unchanged"] += 1
                    store.mark_raw_processed(db, row_hash)
                if counts["in"] % batch_size == 0:
                    commit_batch()
            commit_batch()
            run.status = "partial" if counts["invalid"] else "success"
        except Exception as exc:
            db.rollback()
            if isinstance(exc, DriftRejected):
                run.drift = exc.report.model_dump(mode="json")
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
            log.error("%s run failed: %s", source.name, run.error, extra={"source": source.name})
        run.ended_at = dt.datetime.now(dt.UTC)
        run.duration_seconds = round(time.monotonic() - clock, 3)
        db.commit()
        result = _result(run)

    log.info(
        "%s run %s: in=%d inserted=%d updated=%d unchanged=%d invalid=%d in %.2fs",
        result.source,
        result.status,
        result.records_in,
        result.records_inserted,
        result.records_updated,
        result.records_unchanged,
        result.records_invalid,
        result.duration_seconds or 0.0,
        extra={"event": "etl_run_finished", **asdict(result)},
    )
    return result


def run_source(source: Source, run_group: str | None = None) -> SourceRunResult:
    with source_lock(source.name) as acquired:
        if not acquired:
            log.warning("%s is already running elsewhere; skipped", source.name, extra={"source": source.name})
            return SourceRunResult(source=source.name, status="skipped")
        return _run_locked(source, run_group or str(uuid.uuid4()))


def prune_rate_limits(db: Session) -> None:
    db.execute(delete(ApiRateLimit).where(ApiRateLimit.window_start < func.now() - dt.timedelta(days=1)))
    db.commit()


def run_all(sources: Sequence[Source] | None = None) -> list[SourceRunResult]:
    run_group = str(uuid.uuid4())
    with SessionLocal() as db:
        prune_rate_limits(db)
    results = [run_source(source, run_group) for source in (sources or selected_sources())]
    log.info(
        "ETL run group %s finished: %s",
        run_group,
        {r.source: r.status for r in results},
        extra={"event": "etl_group_finished", "run_group": run_group},
    )
    return results
