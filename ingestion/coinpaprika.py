import requests
from core.db import SessionLocal
from core.models import get_or_create_coin, link_source

URL = "https://api.coinpaprika.com/v1/tickers"


def ingest_coins():
    db = SessionLocal()
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        linked = 0

        for item in data[:50]:  # limit to 50 coins
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

            linked += 1

        db.commit()
        print(f"CoinPaprika ingestion complete ({linked} coins linked)")

    except Exception as e:
        db.rollback()
        print("ERROR:", e)
    finally:
        db.close()


if __name__ == "__main__":
    ingest_coins()
