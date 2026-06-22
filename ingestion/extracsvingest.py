import csv
import os
import datetime
import json as _json
import hashlib
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from core.models import Coin, RawCoin, Checkpoint, get_or_create_coin, link_source
from core.logger import get_logger

log = get_logger(__name__)

DEFAULT_EXTRA_CSV_FILE = "data/extra_coins.csv"

def ingest_extra_csv():
    log.info("Starting Extra CSV ingestion")
    engine = create_engine(os.getenv("DATABASE_URL"))
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        linked = 0
        processed_ids = set()

        cp = db.execute(
            select(Checkpoint).filter_by(source="extra_csv")
        ).scalar_one_or_none()
        if cp and cp.last_value:
            try:
                meta = _json.loads(cp.last_value)
                processed_ids = set(meta.get("processed_ids", []))
            except Exception:
                processed_ids = set()

        errors = 0
        expected_schema = {"symbol", "name", "price_usd", "market_cap"}
        failure_inject = os.getenv("FAILURE_INJECT", "0") == "1"
        fail_after = int(os.getenv("FAIL_AFTER", "0"))  # fail after N records
        run_meta = {
            "start_time": datetime.datetime.utcnow().isoformat(),
            "linked": 0,
            "errors": 0,
            "fail_injected": False,
            "resume": bool(cp),
        }
        extra_csv_path = os.getenv("EXTRA_CSV_PATH") or DEFAULT_EXTRA_CSV_FILE
        with open(extra_csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            actual_schema = set(reader.fieldnames or [])
            matched = expected_schema & actual_schema
            missing = expected_schema - actual_schema
            extra = actual_schema - expected_schema
            confidence = len(matched) / len(expected_schema) if expected_schema else 0
            if confidence < 0.8:
                log.warning(f"Schema drift detected! Confidence: {confidence:.2f}. Missing: {missing}. Extra: {extra}")
            else:
                log.info(f"Schema match confidence: {confidence:.2f}. Matched: {matched}")
            for idx, row in enumerate(reader):
                try:
                    # persist raw row
                    raw = _json.dumps(row)
                    row_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    exists_raw = db.query(RawCoin).filter_by(row_hash=row_hash).first()
                    if not exists_raw:
                        rc = RawCoin(source="extra_csv", row_hash=row_hash, raw=raw, processed=False)
                        db.add(rc)
                        db.commit()

                    if failure_inject and fail_after > 0 and idx == fail_after:
                        log.error("Injected ETL failure after %d records", fail_after)
                        run_meta["fail_injected"] = True
                        raise RuntimeError("Injected ETL failure for testing recovery")
                    symbol = (row.get("symbol") or "").strip().upper()
                    name = row.get("name")
                    source_coin_id = row.get("id") or row.get("symbol")

                    if not symbol or source_coin_id in processed_ids:
                        continue

                    coin = get_or_create_coin(
                        db=db,
                        symbol=symbol,
                        name=name
                    )

                    link_source(
                        db=db,
                        coin_id=coin.id,
                        source="extra_csv",
                        source_coin_id=source_coin_id
                    )
                    # mark raw payload processed
                    db.execute(
                        update(RawCoin).where(RawCoin.row_hash == row_hash).values(processed=True)
                    )

                    db.commit()
                    linked += 1
                    processed_ids.add(source_coin_id)
                except Exception as rec_err:
                    db.rollback()
                    log.error(f"Failed to ingest Extra CSV record: {rec_err}")
                    errors += 1
            log.info(f"Extra CSV ingestion: {linked} records linked, {errors} errors.")

            run_meta["linked"] = linked
            run_meta["errors"] = errors
            run_meta["end_time"] = datetime.datetime.utcnow().isoformat()

            meta = _json.dumps({
                "linked": linked,
                "processed_ids": list(processed_ids),
                "run_meta": run_meta
            })

            if cp:
                db.execute(
                    update(Checkpoint)
                    .where(Checkpoint.id == cp.id)
                    .values(
                        last_run=datetime.datetime.utcnow(),
                        last_value=meta
                    )
                )
            else:
                db.add(Checkpoint(source="extra_csv", last_value=meta))

            db.commit()
            log.info(f"Extra CSV ingestion complete ({linked} coins linked)")

    except Exception as e:
        db.rollback()
        log.error(f"Extra CSV ingestion failed: {e}")

    finally:
        db.close()

