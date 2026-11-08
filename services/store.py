"""Idempotent writes (Postgres ``INSERT ... ON CONFLICT``). Callers own the transaction."""

import hashlib
import json
import logging
import uuid
from typing import Any, Literal

from sqlalchemy import func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from core.models import Coin, CoinSourceMapping, PriceSnapshot, RawCoin
from schemas.coin import NormalizedCoin

log = logging.getLogger(__name__)

WriteOutcome = Literal["inserted", "updated", "unchanged"]


def raw_hash(source: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(f"{source}:{json.dumps(payload, sort_keys=True, default=str)}".encode()).hexdigest()


def store_raw(db: Session, source: str, payload: dict[str, Any]) -> str:
    row_hash = raw_hash(source, payload)
    db.execute(
        insert(RawCoin)
        .values(source=source, row_hash=row_hash, raw=json.dumps(payload, default=str), processed=False)
        .on_conflict_do_nothing(index_elements=[RawCoin.row_hash])
    )
    return row_hash


def mark_raw_processed(db: Session, row_hash: str) -> None:
    db.execute(update(RawCoin).where(RawCoin.row_hash == row_hash).values(processed=True))


def get_or_create_coin(db: Session, symbol: str, name: str) -> int:
    """Canonical coin id for (symbol, case-insensitive name). Same ticker + different name
    stays a separate coin (ticker collisions are common across sources)."""
    symbol = symbol.strip().upper()
    name = name.strip()
    lookup = select(Coin.id).where(Coin.symbol == symbol, func.lower(Coin.name) == name.lower())
    coin_id = db.execute(lookup).scalar()
    if coin_id is not None:
        return int(coin_id)
    if db.execute(select(Coin.id).where(Coin.symbol == symbol).limit(1)).first():
        log.warning("Ticker collision for %s: new canonical coin for name %r", symbol, name)
    db.execute(
        insert(Coin)
        .values(canonical_id=str(uuid.uuid4()), symbol=symbol, name=name)
        .on_conflict_do_nothing(index_elements=[Coin.symbol, func.lower(Coin.name)])
    )
    return int(db.execute(lookup).scalar_one())


def link_source(db: Session, coin_id: int, source: str, source_coin_id: str) -> None:
    db.execute(
        insert(CoinSourceMapping)
        .values(coin_id=coin_id, source=source, source_coin_id=source_coin_id)
        .on_conflict_do_nothing(constraint="uq_coin_source_mapping_source_id")
    )


def upsert_observation(db: Session, source: str, coin_id: int, coin: NormalizedCoin) -> WriteOutcome:
    """Upsert on (source, coin_id, observed_at). Identical values are a no-op."""
    stmt = insert(PriceSnapshot).values(
        source=source,
        coin_id=coin_id,
        observed_at=coin.observed_at,
        price_usd=coin.price_usd,
        market_cap=coin.market_cap,
    )
    upsert: Any = stmt.on_conflict_do_update(  # SQLAlchemy types RETURNING literal_column as Never
        constraint="uq_price_snapshots_source_coin_observed",
        set_={
            "price_usd": stmt.excluded.price_usd,
            "market_cap": stmt.excluded.market_cap,
            "captured_at": func.now(),
        },
        where=or_(
            PriceSnapshot.price_usd.is_distinct_from(stmt.excluded.price_usd),
            PriceSnapshot.market_cap.is_distinct_from(stmt.excluded.market_cap),
        ),
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    row = db.execute(upsert).first()
    if row is None:
        return "unchanged"
    return "inserted" if row.inserted else "updated"


def refresh_latest_price(db: Session, coin_id: int, coin: NormalizedCoin) -> None:
    """Set the coin's current price if this observation is the newest one seen."""
    db.execute(
        update(Coin)
        .where(
            Coin.id == coin_id,
            or_(Coin.price_observed_at.is_(None), Coin.price_observed_at <= coin.observed_at),
        )
        .values(price_usd=coin.price_usd, market_cap=coin.market_cap, price_observed_at=coin.observed_at)
    )


def store_coin(db: Session, source: str, coin: NormalizedCoin) -> WriteOutcome:
    coin_id = get_or_create_coin(db, coin.symbol, coin.name)
    link_source(db, coin_id, source, coin.source_coin_id)
    outcome = upsert_observation(db, source, coin_id, coin)
    refresh_latest_price(db, coin_id, coin)
    return outcome
