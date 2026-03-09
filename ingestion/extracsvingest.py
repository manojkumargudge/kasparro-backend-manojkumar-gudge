import csv
import os
import datetime
import json as _json
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from core.models import Coin, Checkpoint, get_or_create_coin, link_source
from core.logger import get_logger

log = get_logger(__name__)

EXTRA_CSV_FILE = "data/extra_coins.csv"  # update if needed

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
        with open(EXTRA_CSV_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
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

                    db.commit()
                    linked += 1
                    processed_ids.add(source_coin_id)
                except Exception as rec_err:
                    db.rollback()
                    log.error(f"Failed to ingest Extra CSV record: {rec_err}")
                    errors += 1
        log.info(f"Extra CSV ingestion: {linked} records linked, {errors} errors.")

        meta = _json.dumps({
            "linked": linked,
            "processed_ids": list(processed_ids)
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

