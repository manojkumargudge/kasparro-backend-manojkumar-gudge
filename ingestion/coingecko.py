import requests
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.models import Coin, Checkpoint
from sqlalchemy import select, update
import datetime, json as _json
from core.logger import get_logger

log = get_logger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
PARAMS = {
    "vs_currency": "usd",
    "order": "market_cap_desc",
    "per_page": 10,
    "page": 1,
    "sparkline": "false"
}

def ingest_coingecko():
    log.info("Starting CoinGecko ingestion")

    try:
        response = requests.get(COINGECKO_URL, params=PARAMS, timeout=10)
        response.raise_for_status()
        log.info("CoinGecko API success")

        data = response.json()

    except Exception as e:
        log.error(f"CoinGecko API failed: {e}")
        return   # VERY IMPORTANT → do not crash backend

    # create a fresh engine/session here to avoid stale/global pool DNS issues
    engine = create_engine(os.getenv("DATABASE_URL"))
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        inserted = 0
        for item in data:
            symbol = (item.get("symbol") or "").strip().upper()
            # skip if already exists
            existing = db.query(Coin).filter_by(symbol=symbol).one_or_none()
            if existing:
                continue

            record = Coin(
                symbol=symbol,
                name=item.get("name"),
                price_usd=float(item.get("current_price") or 0),
                market_cap=float(item.get("market_cap") or 0),
            )
            db.add(record)
            inserted += 1

        db.commit()
        # update checkpoint
        meta = _json.dumps({"source_count": len(data), "inserted": inserted})
        cp = db.execute(select(Checkpoint).filter_by(source="coingecko")).scalar_one_or_none()
        if cp:
            db.execute(update(Checkpoint).where(Checkpoint.id == cp.id).values(last_run=datetime.datetime.utcnow(), last_value=meta))
        else:
            db.add(Checkpoint(source="coingecko", last_value=meta))
        db.commit()
        log.info(f"CoinGecko ingestion complete ({len(data)} records, inserted={inserted})")

    except Exception as e:
        db.rollback()
        log.error(f"DB insert failed: {e}")

    finally:
        db.close()

if __name__ == "__main__":
    ingest_coingecko()
