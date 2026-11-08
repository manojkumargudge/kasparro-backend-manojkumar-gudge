import logging
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from core.db import Base

log = logging.getLogger(__name__)


# -----------------------------
# CANONICAL COIN (NORMALIZED)
# -----------------------------
class Coin(Base):
    __tablename__ = "coins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    canonical_id: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String, index=True)
    name: Mapped[str | None] = mapped_column(String)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)


# --------------------------------
# SOURCE -> CANONICAL COIN MAPPING
# --------------------------------
class CoinSourceMapping(Base):
    __tablename__ = "coin_source_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("coins.id"))
    source: Mapped[str | None] = mapped_column(String, index=True)  # coingecko / coinpaprika / csv
    source_coin_id: Mapped[str | None] = mapped_column(String, index=True)  # external ID


# -----------------------------
# RAW INGESTED DATA (UNCHANGED)
# -----------------------------
class RawCoin(Base):
    __tablename__ = "raw_coins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str | None] = mapped_column(String, index=True)
    row_hash: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    raw: Mapped[str | None] = mapped_column(Text)
    processed: Mapped[bool | None] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())


# -----------------------------
# INGESTION CHECKPOINTS
# -----------------------------
class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_value: Mapped[str | None] = mapped_column(String)


# -----------------------------
# PRICE HISTORY (SNAPSHOTS)
# -----------------------------
class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("coins.id"), index=True)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


# ==========================================================
# NORMALIZATION HELPERS
# ==========================================================


def get_or_create_coin(db: Session, symbol: str, name: str | None) -> Coin:
    """Return the canonical coin for a (symbol, name) pair, creating it if needed."""
    normalized_symbol = (symbol or "").strip().upper()
    normalized_name = (name or "").strip()

    coin = (
        db.query(Coin)
        .filter(
            func.lower(Coin.symbol) == normalized_symbol.lower(),
            func.lower(Coin.name) == normalized_name.lower(),
        )
        .first()
    )

    if coin:
        return coin

    # Symbol collisions across sources are common; keep separate canonical rows by name.
    symbol_collisions = db.query(Coin).filter(func.lower(Coin.symbol) == normalized_symbol.lower()).all()
    if symbol_collisions:
        existing_names = sorted({(c.name or "").strip() for c in symbol_collisions})
        log.warning(
            "Ticker collision for symbol '%s': existing names=%s, incoming name='%s'",
            normalized_symbol,
            existing_names,
            normalized_name,
        )

    coin = Coin(
        canonical_id=str(uuid.uuid4()),
        symbol=normalized_symbol,
        name=normalized_name,
    )
    db.add(coin)
    db.commit()
    db.refresh(coin)
    return coin


def link_source(db: Session, coin_id: int, source: str, source_coin_id: str) -> None:
    """Link an external source coin to a canonical coin."""
    exists = db.query(CoinSourceMapping).filter_by(source=source, source_coin_id=source_coin_id).first()

    if not exists:
        db.add(CoinSourceMapping(coin_id=coin_id, source=source, source_coin_id=source_coin_id))
        db.commit()


def record_price_snapshot(db: Session, coin_id: int, price_usd: float | None, market_cap: float | None) -> None:
    """Persist a point-in-time snapshot of a coin's latest pricing."""
    db.add(
        PriceSnapshot(
            coin_id=coin_id,
            price_usd=float(price_usd) if price_usd is not None else None,
            market_cap=float(market_cap) if market_cap is not None else None,
        )
    )
    db.commit()
