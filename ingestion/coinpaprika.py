import requests
import os
import datetime
import json as _json
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from core.models import Coin, Checkpoint, get_or_create_coin, link_source, record_price_snapshot
from core.models import RawCoin
from core.logger import get_logger

log = get_logger(__name__)

URL = "https://api.coinpaprika.com/v1/tickers"

def ingest_coins():
    log.info("Starting CoinPaprika ingestion")

    api_key = os.getenv("COINPAPRIKA_API_KEY")
    if not api_key:
        log.error("COINPAPRIKA_API_KEY not set; aborting CoinPaprika ingestion")
        return

    # Rate limiting and exponential backoff
    max_retries = int(os.getenv("COINPAPRIKA_MAX_RETRIES", "5"))
    min_interval = float(os.getenv("COINPAPRIKA_MIN_INTERVAL", "1.0"))  # seconds
    last_call_time = getattr(ingest_coins, "_last_call_time", None)
    import time
    if last_call_time:
        elapsed = time.time() - last_call_time
        if elapsed < min_interval:
            log.info(f"Rate limiting: sleeping {min_interval - elapsed:.2f}s before API call")
            time.sleep(min_interval - elapsed)
    ingest_coins._last_call_time = time.time()

    for attempt in range(1, max_retries + 1):
        try:
            headers = {"X-API-KEY": api_key}
            response = requests.get(URL, headers=headers, timeout=10)
            response.raise_for_status()
            log.info(f"CoinPaprika API success (attempt {attempt})")
            data = response.json()
            break
        except Exception as e:
            log.error(f"CoinPaprika API failed (attempt {attempt}): {e}")
            if attempt == max_retries:
                log.error("Max retries reached, aborting ETL.")
                return
            backoff = min_interval * (2 ** (attempt - 1))
            log.info(f"Retrying after {backoff:.2f}s...")
            time.sleep(backoff)

    engine = create_engine(os.getenv("DATABASE_URL"))
    Session = sessionmaker(bind=engine)
    db = Session()
    started_at = datetime.datetime.utcnow()

    try:
        linked = 0
        errors = 0

        for item in data[:50]:  # limit to 50 coins
            try:
                # persist raw payload
                raw = _json.dumps(item)
                import hashlib
                row_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                exists_raw = db.query(RawCoin).filter_by(row_hash=row_hash).first()
                if not exists_raw:
                    rc = RawCoin(source="coinpaprika", row_hash=row_hash, raw=raw, processed=False)
                    db.add(rc)
                    db.commit()

                symbol = (item.get("symbol") or "").strip().upper()
                name = item.get("name")
                source_coin_id = item.get("id")

                if not symbol or not source_coin_id:
                    continue

                # 🔹 NORMALIZATION
                coin = get_or_create_coin(
                    db=db,
                    symbol=symbol,
                    name=name
                )

                # update market data
                quotes = item.get("quotes", {}).get("USD", {})
                coin.price_usd = float(quotes.get("price") or 0)
                coin.market_cap = float(quotes.get("market_cap") or 0)

                link_source(
                    db=db,
                    coin_id=coin.id,
                    source="coinpaprika",
                    source_coin_id=source_coin_id
                )
                record_price_snapshot(db, coin.id, coin.price_usd, coin.market_cap)
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

        log.info(f"CoinPaprika ingestion: {linked} records linked, {errors} errors.")

        run_meta = {
            "start_time": started_at.isoformat(),
            "end_time": datetime.datetime.utcnow().isoformat(),
            "duration_seconds": round((datetime.datetime.utcnow() - started_at).total_seconds(), 3),
            "linked": linked,
            "errors": errors,
            "status": "success" if errors == 0 else "partial",
        }
        meta = _json.dumps({
            "source_count": len(data),
            "linked": linked,
            "errors": errors,
            "run_meta": run_meta,
        })

        cp = db.execute(
            select(Checkpoint).filter_by(source="coinpaprika")
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
            db.add(Checkpoint(source="coinpaprika", last_value=meta))

        db.commit()
        log.info(f"CoinPaprika ingestion complete ({linked} records linked)")
        return linked, errors

    except Exception as e:
        db.rollback()
        log.error(f"DB operation failed: {e}")
        return 0, 1

    finally:
        db.close()


if __name__ == "__main__":
    ingest_coins()
