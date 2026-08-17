import csv
import os
import datetime
import json as _json
import hashlib
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from core.models import Coin, RawCoin, Checkpoint, get_or_create_coin, link_source, record_price_snapshot
from core.logger import get_logger

log = get_logger(__name__)

DEFAULT_CSV_FILE = "data/coins.csv"


def _to_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_csv_path() -> str:
    csv_path = os.getenv("CSV_PATH")
    if csv_path:
        return csv_path

    for candidate in (DEFAULT_CSV_FILE, "data/sample_coins.csv"):
        if os.path.exists(candidate):
            return candidate

    return DEFAULT_CSV_FILE

def ingest_csv():
    log.info("Starting CSV ingestion")
    engine = create_engine(os.getenv("DATABASE_URL"))
    Session = sessionmaker(bind=engine)
    db = Session()
    started_at = datetime.datetime.utcnow()
    try:
        linked = 0
        processed_ids = set()

        # Load last checkpoint (for idempotency)
        cp = db.execute(
            select(Checkpoint).filter_by(source="csv")
        ).scalar_one_or_none()
        if cp and cp.last_value:
            try:
                meta = _json.loads(cp.last_value)
                processed_ids = set(meta.get("processed_ids", []))
            except Exception:
                processed_ids = set()

        errors = 0
        csv_path = _resolve_csv_path()
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    raw = _json.dumps(row)
                    row_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                    # persist raw payload if not exists
                    exists_raw = db.query(RawCoin).filter_by(row_hash=row_hash).first()
                    if not exists_raw:
                        rc = RawCoin(source="csv", row_hash=row_hash, raw=raw, processed=False)
                        db.add(rc)
                        db.commit()

                    symbol = (row.get("symbol") or "").strip().upper()
                    name = row.get("name")
                    source_coin_id = row.get("id") or row.get("symbol")

                    if not symbol or source_coin_id in processed_ids:
                        continue

                    # 🔹 NORMALIZATION
                    coin = get_or_create_coin(
                        db=db,
                        symbol=symbol,
                        name=name
                    )

                    coin.price_usd = _to_float(row.get("price_usd"))
                    coin.market_cap = _to_float(row.get("market_cap"))

                    link_source(
                        db=db,
                        coin_id=coin.id,
                        source="csv",
                        source_coin_id=source_coin_id
                    )
                    record_price_snapshot(db, coin.id, coin.price_usd, coin.market_cap)

                    # mark raw payload processed
                    db.execute(
                        update(RawCoin).where(RawCoin.row_hash == row_hash).values(processed=True)
                    )

                    db.commit()
                    linked += 1
                    processed_ids.add(source_coin_id)
                except Exception as rec_err:
                    db.rollback()
                    log.error(f"Failed to ingest CSV record: {rec_err}")
                    errors += 1
        log.info(f"CSV ingestion: {linked} records linked, {errors} errors.")

        run_meta = {
            "start_time": started_at.isoformat(),
            "end_time": datetime.datetime.utcnow().isoformat(),
            "duration_seconds": round((datetime.datetime.utcnow() - started_at).total_seconds(), 3),
            "linked": linked,
            "errors": errors,
            "status": "success" if errors == 0 else "partial",
        }
        meta = _json.dumps({
            "linked": linked,
            "errors": errors,
            "processed_ids": list(processed_ids),
            "run_meta": run_meta,
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
            db.add(Checkpoint(source="csv", last_value=meta))

        db.commit()
        log.info(f"CSV ingestion complete ({linked} coins linked)")
        return linked, errors

    except Exception as e:
        db.rollback()
        log.error(f"CSV ingestion failed: {e}")
        return 0, 1

    finally:
        db.close()


if __name__ == "__main__":
    ingest_csv()
