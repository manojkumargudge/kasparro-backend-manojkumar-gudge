"""SQLAlchemy models. Schema changes go through Alembic (see migrations/)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


# -----------------------------
# CANONICAL COIN (NORMALIZED)
# -----------------------------
class Coin(Base):
    """One row per real-world asset; identity is (symbol, lower(name))."""

    __tablename__ = "coins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    canonical_id: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    symbol: Mapped[str | None] = mapped_column(String, index=True)
    name: Mapped[str | None] = mapped_column(String)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    # observed_at of the observation that set price_usd/market_cap (latest wins)
    price_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index("uq_coins_symbol_lower_name", Coin.symbol, func.lower(Coin.name), unique=True)


# --------------------------------
# SOURCE -> CANONICAL COIN MAPPING
# --------------------------------
class CoinSourceMapping(Base):
    __tablename__ = "coin_source_mapping"
    __table_args__ = (UniqueConstraint("source", "source_coin_id", name="uq_coin_source_mapping_source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("coins.id"))
    source: Mapped[str | None] = mapped_column(String, index=True)
    source_coin_id: Mapped[str | None] = mapped_column(String, index=True)


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
    """Per-source incremental state, committed together with every batch."""

    __tablename__ = "checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_value: Mapped[str | None] = mapped_column(String)


# -----------------------------
# OBSERVATIONS (PRICE HISTORY)
# -----------------------------
class PriceSnapshot(Base):
    """One observation of a coin by a source at ``observed_at``. Upserted idempotently."""

    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint("source", "coin_id", "observed_at", name="uq_price_snapshots_source_coin_observed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    coin_id: Mapped[int | None] = mapped_column(ForeignKey("coins.id"), index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# -----------------------------
# ETL RUNS (ONE ROW PER RUN PER SOURCE)
# -----------------------------
class EtlRun(Base):
    __tablename__ = "etl_runs"
    __table_args__ = (Index("ix_etl_runs_source_started_at", "source", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_group: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running|success|partial|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    records_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    records_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    drift: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


# -----------------------------
# API RATE LIMIT COUNTERS (FIXED WINDOW, PER API KEY HASH)
# -----------------------------
class ApiRateLimit(Base):
    __tablename__ = "api_rate_limits"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
