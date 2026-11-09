"""Scrub error text before it is stored in ``etl_runs`` or returned by the API.

Removes URLs (upstream endpoints, DSNs such as ``postgresql://user:pw@host/db``), HTTP
auth headers, libpq ``key=value`` connection settings, database host/IP details,
SQLAlchemy's ``[SQL: ...]`` / ``[parameters: ...]`` sections, and the literal values of
configured secrets.
"""

import os
import re
from urllib.parse import urlsplit

REDACTED = "[redacted]"

_SECRET_ENV = ("DATABASE_URL", "APP_API_KEY", "APP_DEMO_API_KEY", "COINGECKO_API_KEY", "COINPAPRIKA_API_KEY")

_SQL_SECTIONS = re.compile(r"\s*\[(?:SQL|parameters):.*", re.DOTALL)
_URL = re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s'\"<>()]+", re.IGNORECASE)
_HEADER = re.compile(
    r"(?i)\b(authorization|proxy-authorization|x-api-key|x-cg-demo-api-key|x-cg-pro-api-key|cookie|set-cookie)"
    r"(['\"]?\s*[:=]\s*)('[^']*'|\"[^\"]*\"|[^\s,;}]+(?:\s+[^\s,;}]+)?)"
)
_QUERY_KEY = re.compile(r"(?i)\b(x_cg_demo_api_key|x_cg_pro_api_key|api_key|apikey|key|token)=[^\s&'\"]+")
_LIBPQ = re.compile(r"(?i)\b(host|hostaddr|port|dbname|user|password|sslmode|sslcert|sslkey)=('[^']*'|\S+)")
_PG_SERVER = re.compile(r'(?i)(connection to server (?:at|on socket) )("[^"]*"|\S+)')
# IPv4 anywhere; IPv6 only in the "(addr)" form libpq uses, so times like 10:00:00 survive.
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\((?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}\)", re.IGNORECASE)


def _secret_literals() -> list[str]:
    values: list[str] = []
    for name in _SECRET_ENV:
        value = os.getenv(name, "").strip()
        if len(value) >= 6:
            values.append(value)
    for entry in os.getenv("APP_API_KEYS", "").split(","):
        parts = entry.strip().split(":")
        candidate = parts[1] if len(parts) >= 3 else parts[0]
        if len(candidate) >= 6:
            values.append(candidate)
    url = os.getenv("DATABASE_URL", "")
    if url:
        dsn = urlsplit(url)
        values += [v for v in (dsn.password, dsn.username, dsn.hostname) if v and len(v) >= 4]
    return sorted(set(values), key=len, reverse=True)


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    for secret in _secret_literals():
        text = text.replace(secret, REDACTED)
    text = _SQL_SECTIONS.sub("", text)
    text = _URL.sub(REDACTED, text)
    text = _HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _QUERY_KEY.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _LIBPQ.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _PG_SERVER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return _IP.sub(REDACTED, text)
