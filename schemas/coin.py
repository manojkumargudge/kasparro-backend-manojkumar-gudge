"""Pydantic models that every ingested record must pass before it is normalised.

Each source model maps its payload onto ``NormalizedCoin`` via ``to_coin()``. Invalid
records raise ``pydantic.ValidationError``; the ETL logs and counts them instead of crashing.
"""

import datetime as dt
import re
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

_MISSING = {"", "n/a", "na", "null", "none", "-", "--"}
_NUMERIC_JUNK = re.compile(r"[\s,$_]|usd", re.IGNORECASE)


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() in _MISSING:
        return None
    return value


def _coerce_number(value: Any) -> Any:
    """Accept ``"$43,250.10"``, ``" 1.2e12 "``, ``"1_000"``; leave anything else to Pydantic."""
    value = _blank_to_none(value)
    if isinstance(value, str):
        return _NUMERIC_JUNK.sub("", value)
    return value


def _coerce_datetime(value: Any) -> Any:
    """Accept ISO-8601 (naive = UTC) and unix epoch seconds (int or digit string)."""
    value = _blank_to_none(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int | float) and not isinstance(value, bool):
        return dt.datetime.fromtimestamp(value, tz=dt.UTC)
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return value
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    if isinstance(value, dt.datetime) and value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


NonEmptyStr = Annotated[str, Field(min_length=1)]
Money = Annotated[float, BeforeValidator(_coerce_number), Field(ge=0, allow_inf_nan=False)]
Timestamp = Annotated[AwareDatetime, BeforeValidator(_coerce_datetime)]


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _blanks_are_missing(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: _blank_to_none(v) for k, v in data.items()}
        return data


class NormalizedCoin(BaseModel):
    """Source-independent observation written to ``coins`` / ``price_snapshots``."""

    source_coin_id: NonEmptyStr
    symbol: NonEmptyStr
    name: NonEmptyStr
    price_usd: Money | None = None
    market_cap: Money | None = None
    observed_at: Timestamp

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class SourceRecord(_Record):
    """Base for per-source payload models."""

    def to_coin(self) -> NormalizedCoin:
        raise NotImplementedError


class CoinGeckoRecord(SourceRecord):
    """One element of ``GET /coins/markets``."""

    id: NonEmptyStr
    symbol: NonEmptyStr
    name: NonEmptyStr
    current_price: Money | None = None
    market_cap: Money | None = None
    last_updated: Timestamp

    def to_coin(self) -> NormalizedCoin:
        return NormalizedCoin(
            source_coin_id=self.id,
            symbol=self.symbol,
            name=self.name,
            price_usd=self.current_price,
            market_cap=self.market_cap,
            observed_at=self.last_updated,
        )


class CoinPaprikaUsdQuote(_Record):
    price: Money | None = None
    market_cap: Money | None = None


class CoinPaprikaQuotes(_Record):
    USD: CoinPaprikaUsdQuote | None = None


class CoinPaprikaRecord(SourceRecord):
    """One element of ``GET /tickers``."""

    id: NonEmptyStr
    symbol: NonEmptyStr
    name: NonEmptyStr
    quotes: CoinPaprikaQuotes | None = None
    last_updated: Timestamp

    def to_coin(self) -> NormalizedCoin:
        usd = self.quotes.USD if self.quotes and self.quotes.USD else CoinPaprikaUsdQuote()
        return NormalizedCoin(
            source_coin_id=self.id,
            symbol=self.symbol,
            name=self.name,
            price_usd=usd.price,
            market_cap=usd.market_cap,
            observed_at=self.last_updated,
        )


class CsvCoinRecord(SourceRecord):
    """A CSV row after column mapping (see ``ingestion.drift``).

    ``id`` is optional (falls back to the case-preserved symbol); ``observed_at`` is filled
    by the CSV source from the file modification time when the file has no timestamp column.
    """

    id: NonEmptyStr | None = None
    symbol: NonEmptyStr
    name: NonEmptyStr
    price_usd: Money | None = None
    market_cap: Money | None = None
    observed_at: Timestamp

    def to_coin(self) -> NormalizedCoin:
        return NormalizedCoin(
            source_coin_id=self.id or self.symbol,
            symbol=self.symbol,
            name=self.name,
            price_usd=self.price_usd,
            market_cap=self.market_cap,
            observed_at=self.observed_at,
        )
