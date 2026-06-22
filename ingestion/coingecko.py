import requests
import os
import datetime
import json as _json
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from core.models import Coin, RawCoin, Checkpoint, get_or_create_coin, link_source
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

    # Rate limiting and exponential backoff
    max_retries = int(os.getenv("COINGECKO_MAX_RETRIES", "5"))
    min_interval = float(os.getenv("COINGECKO_MIN_INTERVAL", "1.0"))  # seconds
    last_call_time = getattr(ingest_coingecko, "_last_call_time", None)
    import time
    if last_call_time:
        elapsed = time.time() - last_call_time
        if elapsed < min_interval:
            log.info(f"Rate limiting: sleeping {min_interval - elapsed:.2f}s before API call")
            time.sleep(min_interval - elapsed)
    ingest_coingecko._last_call_time = time.time()

    for attempt in range(1, max_retries + 1):
        try:
            # CoinGecko may not need an API key, but include APP_API_KEY if present
            headers = {}
            app_api_key = os.getenv("APP_API_KEY")
            if app_api_key:
                headers["X-API-KEY"] = app_api_key
            response = requests.get(COINGECKO_URL, params=PARAMS, timeout=10, headers=headers)
            response.raise_for_status()
            log.info(f"CoinGecko API success (attempt {attempt})")
            data = response.json()
            break
        except Exception as e:
            log.error(f"CoinGecko API failed (attempt {attempt}): {e}")
            if attempt == max_retries:
                log.error("Max retries reached, aborting ETL.")
                return
            backoff = min_interval * (2 ** (attempt - 1))
            log.info(f"Retrying after {backoff:.2f}s...")
            time.sleep(backoff)

    engine = create_engine(os.getenv("DATABASE_URL"))
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        linked = 0
        errors = 0

        for item in data:
            try:
                # persist raw payload
                raw = _json.dumps(item)
                import hashlib
                row_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                exists_raw = db.query(RawCoin).filter_by(row_hash=row_hash).first()
                if not exists_raw:
                    rc = RawCoin(source="coingecko", row_hash=row_hash, raw=raw, processed=False)
                    db.add(rc)
                    db.commit()

                symbol = (item.get("symbol") or "").strip().upper()
                name = item.get("name")
                source_coin_id = item.get("id")

                if not symbol or not source_coin_id:
                    continue

                # 🔹 NORMALIZATION STARTS HERE
                coin = get_or_create_coin(
                    db=db,
                    symbol=symbol,
                    name=name
                )

                # update market data if present
                coin.price_usd = float(item.get("current_price") or 0)
                coin.market_cap = float(item.get("market_cap") or 0)

                link_source(
                    db=db,
                    coin_id=coin.id,
                    source="coingecko",
                    source_coin_id=source_coin_id
                )
                # mark raw processed
                db.execute(
                    update(RawCoin).where(RawCoin.row_hash == row_hash).values(processed=True)
                )

                db.commit()
                linked += 1
            except Exception as rec_err:
                db.rollback()
                log.error(f"Failed to ingest record: {rec_err}")
                errors += 1

        log.info(f"CoinGecko ingestion: {linked} records linked, {errors} errors.")

        meta = _json.dumps({
            "source_count": len(data),
            "linked": linked
        })

        cp = db.execute(
            select(Checkpoint).filter_by(source="coingecko")
        ).scalar_one_or_none()

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
            db.add(Checkpoint(source="coingecko", last_value=meta))

        db.commit()
        log.info(f"CoinGecko ingestion complete ({linked} records linked)")

    except Exception as e:
        db.rollback()
        log.error(f"DB operation failed: {e}")

    finally:
        db.close()


if __name__ == "__main__":
    ingest_coingecko()
