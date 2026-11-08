"""Pydantic models that every ingested record must pass before it is normalised.

Each source model maps its own payload shape onto ``NormalizedCoin`` via ``to_coin()``.
Invalid records raise ``pydantic.ValidationError``; the ingestion layer logs and counts
them instead of crashing.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _blanks_are_missing(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: _blank_to_none(v) for k, v in data.items()}
        return data


class NormalizedCoin(BaseModel):
    """Source-independent shape written to the canonical ``coins`` table."""

    source_coin_id: NonEmptyStr
    symbol: NonEmptyStr
    name: NonEmptyStr
    price_usd: NonNegativeFloat | None = None
    market_cap: NonNegativeFloat | None = None

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
    current_price: NonNegativeFloat | None = None
    market_cap: NonNegativeFloat | None = None

    def to_coin(self) -> NormalizedCoin:
        return NormalizedCoin(
            source_coin_id=self.id,
            symbol=self.symbol,
            name=self.name,
            price_usd=self.current_price,
            market_cap=self.market_cap,
        )


class CoinPaprikaUsdQuote(_Record):
    price: NonNegativeFloat | None = None
    market_cap: NonNegativeFloat | None = None


class CoinPaprikaQuotes(_Record):
    USD: CoinPaprikaUsdQuote | None = None


class CoinPaprikaRecord(SourceRecord):
    """One element of ``GET /tickers``."""

    id: NonEmptyStr
    symbol: NonEmptyStr
    name: NonEmptyStr
    quotes: CoinPaprikaQuotes | None = None

    def to_coin(self) -> NormalizedCoin:
        usd = self.quotes.USD if self.quotes and self.quotes.USD else CoinPaprikaUsdQuote()
        return NormalizedCoin(
            source_coin_id=self.id,
            symbol=self.symbol,
            name=self.name,
            price_usd=usd.price,
            market_cap=usd.market_cap,
        )


class CsvCoinRecord(SourceRecord):
    """One row of a CSV file with columns ``[id,] symbol, name, price_usd, market_cap``.

    ``id`` is optional; when absent the (case-preserved) symbol is the source id.
    """

    id: NonEmptyStr | None = None
    symbol: NonEmptyStr
    name: NonEmptyStr
    price_usd: NonNegativeFloat | None = None
    market_cap: NonNegativeFloat | None = None

    def to_coin(self) -> NormalizedCoin:
        return NormalizedCoin(
            source_coin_id=self.id or self.symbol,
            symbol=self.symbol,
            name=self.name,
            price_usd=self.price_usd,
            market_cap=self.market_cap,
        )
