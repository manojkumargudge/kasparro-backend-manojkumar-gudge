"""Schema drift detection for tabular sources.

Headers are normalised (camelCase split, punctuation removed) and fuzzy-matched with
rapidfuzz ``WRatio`` against aliases of each canonical field. Assignment is greedy by
score, one header per field. Confidence is the mean match score (0-1) over the required
fields (unmatched required field = 0).

* confidence < ``DRIFT_WARN_THRESHOLD`` (default 0.90): warning, ingestion continues
* confidence < ``DRIFT_REJECT_THRESHOLD`` (default 0.60): the file is rejected
"""

import logging
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from rapidfuzz import fuzz

from core.config import env_float

log = logging.getLogger(__name__)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "coin id", "slug"),
    "symbol": ("symbol", "ticker", "sym"),
    "name": ("name", "coin name", "asset name"),
    "price_usd": ("price usd", "price", "usd price"),
    "market_cap": ("market cap", "marketcap", "mkt cap"),
    "observed_at": ("observed at", "last updated", "updated at", "timestamp"),
}
REQUIRED_FIELDS: tuple[str, ...] = ("symbol", "name", "price_usd")
MIN_COLUMN_SCORE = 75.0


class DriftRejected(Exception):
    def __init__(self, report: "DriftReport"):
        super().__init__(
            f"schema drift: confidence {report.confidence:.2f} below reject threshold {report.reject_threshold:.2f}"
        )
        self.report = report


class DriftReport(BaseModel):
    headers: list[str]
    mapping: dict[str, str]  # canonical field -> source header
    scores: dict[str, float]  # canonical field -> match score (0-100)
    renamed: dict[str, str]  # source header -> canonical field, where they differ
    missing_fields: list[str]
    extra_headers: list[str]
    confidence: float
    status: str  # ok | warn | reject
    warn_threshold: float
    reject_threshold: float

    @property
    def drifted(self) -> bool:
        return bool(self.renamed or self.missing_fields or self.extra_headers)

    def remap(self, row: dict[str, Any]) -> dict[str, Any]:
        return {field: row.get(header) for field, header in self.mapping.items()}


def normalize_header(header: str) -> str:
    header = re.sub(r"([a-z])([A-Z])", r"\1 \2", header.strip())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", header.lower()).split())


def _score(header: str, field: str) -> float:
    normalized = normalize_header(header)
    return max(float(fuzz.WRatio(normalized, alias)) for alias in FIELD_ALIASES[field])


def match_columns(headers: Sequence[str]) -> DriftReport:
    warn = env_float("DRIFT_WARN_THRESHOLD", 0.90)
    reject = env_float("DRIFT_REJECT_THRESHOLD", 0.60)
    candidates = sorted(
        ((_score(h, f), f, h) for f in FIELD_ALIASES for h in headers),
        key=lambda c: (-c[0], list(FIELD_ALIASES).index(c[1]), headers.index(c[2])),
    )
    mapping: dict[str, str] = {}
    scores: dict[str, float] = {}
    for score, field, header in candidates:
        if score < MIN_COLUMN_SCORE or field in mapping or header in mapping.values():
            continue
        mapping[field] = header
        scores[field] = round(score, 1)

    confidence = round(sum(scores.get(f, 0.0) for f in REQUIRED_FIELDS) / (100 * len(REQUIRED_FIELDS)), 3)
    status = "reject" if confidence < reject else "warn" if confidence < warn else "ok"
    return DriftReport(
        headers=list(headers),
        mapping=mapping,
        scores=scores,
        renamed={h: f for f, h in mapping.items() if h != f},
        missing_fields=[f for f in FIELD_ALIASES if f not in mapping],
        extra_headers=[h for h in headers if h not in mapping.values()],
        confidence=confidence,
        status=status,
        warn_threshold=warn,
        reject_threshold=reject,
    )


def check_drift(source: str, headers: Sequence[str]) -> DriftReport:
    report = match_columns(headers)
    fields = {"source": source, "confidence": report.confidence, "drift_status": report.status}
    if report.status == "reject":
        log.error("Schema drift: rejecting %s (confidence %.2f)", source, report.confidence, extra=fields)
        raise DriftRejected(report)
    if report.status == "warn":
        log.warning(
            "Schema drift: %s confidence %.2f below %.2f; renamed=%s missing=%s extra=%s",
            source,
            report.confidence,
            report.warn_threshold,
            report.renamed,
            report.missing_fields,
            report.extra_headers,
            extra=fields,
        )
    elif report.drifted:
        log.info("Schema drift tolerated for %s: renamed=%s extra=%s", source, report.renamed, report.extra_headers)
    return report
