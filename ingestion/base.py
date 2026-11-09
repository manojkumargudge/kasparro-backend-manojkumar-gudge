"""Source adapter contract used by ``services.etl``."""

import datetime as dt
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, ClassVar

from ingestion.drift import DriftReport
from schemas.coin import NormalizedCoin, SourceRecord

log = logging.getLogger(__name__)


def log_auth_mode(source: str, header: str | None) -> None:
    """Record whether a source call is keyed (header name only, never the value)."""
    mode = "api_key" if header else "keyless"
    log.info(
        "%s auth: %s", source, f"API key via {header}" if header else "keyless", extra={"source": source, "auth": mode}
    )


class Source(ABC):
    """A data source.

    Incremental strategy (``incremental``):

    * ``"watermark"``: the record's source-side ``last_updated`` (``observed_at``) must be
      newer than the checkpointed value for that coin, otherwise it is skipped.
    * ``"content"``: a hash of the normalised values must differ from the checkpointed hash
      (used for files, where rows have no reliable timestamp).
    """

    name: str
    model: ClassVar[type[SourceRecord]]
    incremental: ClassVar[str] = "watermark"

    def prepare(self) -> DriftReport | None:
        """Called once before ``fetch``; may raise ``DriftRejected``."""
        return None

    @abstractmethod
    def fetch(self) -> Iterator[dict[str, Any]]:
        """Yield raw payloads exactly as received."""

    def to_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map a raw payload to the input of ``model`` (identity by default)."""
        return payload

    def change_token(self, coin: NormalizedCoin) -> str:
        if self.incremental == "watermark":
            return coin.observed_at.astimezone(dt.UTC).isoformat()
        values = [coin.symbol, coin.name, coin.price_usd, coin.market_cap]
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def is_changed(self, previous: str | None, token: str) -> bool:
        if previous is None:
            return True
        if self.incremental == "watermark":
            return dt.datetime.fromisoformat(token) > dt.datetime.fromisoformat(previous)
        return token != previous
