# Kasparro Backend & ETL

A FastAPI service and ETL pipeline. It ingests cryptocurrency market data from **CoinGecko**,
**CoinPaprika** and **two CSV files**, normalises everything into one schema in PostgreSQL,
and serves it through an authenticated, rate-limited REST API with health, stats,
run-history, anomaly and Prometheus endpoints.

Requirements are in [`docs/BRIEF.md`](docs/BRIEF.md). The [coverage table](#brief-coverage)
below maps each requirement to its code and the test that proves it. Cloud deployment
(GCP) is prepared but not yet live; see [`docs/DEPLOY_GCP.md`](docs/DEPLOY_GCP.md).

CI on `main` runs ruff (lint + format), mypy, actionlint, shellcheck, **111 tests against
PostgreSQL** with a coverage gate of 85% (currently 95%), a Docker build, and `make test`.

---

## Architecture

```text
                 ┌──────────────── ingestion/ (sources) ─────────────────┐
 CoinGecko API ──┤ coingecko.py   ─┐                                     │
 CoinPaprika API ┤ coinpaprika.py ─┼─ http.py: per-source rate limit,     │
 data/*.csv ─────┤ csv_source.py  ─┘  backoff + jitter, Retry-After       │
                 │   └─ drift.py: fuzzy header match → confidence         │
                 └────────────────────────────┬───────────────────────────┘
                                              │ raw payloads
                 ┌──────────── services/etl.py (per source run) ──────────┐
                 │ raw_coins ← store raw                                   │
                 │ schemas/coin.py ← Pydantic validate (invalid: log+count)│
                 │ incremental check vs checkpoint (watermark / hash)      │
                 │ services/store.py ← upsert coins, mappings, observations│
                 │ every ETL_BATCH_SIZE records: COMMIT data + checkpoint  │
                 │ etl_runs row: running → success | partial | failed      │
                 └────────────────────────────┬───────────────────────────┘
                                              ▼
                               PostgreSQL (schema via Alembic)
   coins · coin_source_mapping · price_snapshots · raw_coins · checkpoints · etl_runs · api_rate_limits
                                              ▲
                 ┌───────────── api/ (FastAPI) ┴───────────────────────────┐
                 │ deps.py: X-API-KEY auth + per-key rate limit (Postgres) │
                 │ /data  /stats  /runs  /compare-runs     (key required)  │
                 │ /health  /metrics  /dashboard  /docs    (public)        │
                 └──────────────────────────────────────────────────────────┘
```

Layout: `api/` (app + routers), `services/` (ETL, storage, run analytics, rate limiting,
metrics), `ingestion/` (source adapters, HTTP, drift), `schemas/` (Pydantic record models),
`core/` (DB, models, config, logging, security, migrations), `migrations/` (Alembic),
`scripts/` (ETL runner, smoke test, DB helpers), `tests/`, `deploy/gcp/`, `docs/`.

**Startup:** one image runs everything.
- The default command starts the API. It applies migrations first, then runs the ETL once in
  the background (set `RUN_ETL_ON_STARTUP=0` to disable, or `ETL_INTERVAL_SECONDS` to repeat).
- `python scripts/run_etl.py` is the one-shot ETL that the cloud job uses. It also migrates
  before ingesting.

---

## Quick start

Prerequisites: Docker with Compose v2 and `make`. Python 3.11 only for local development.

```bash
cp .env.example .env      # set APP_API_KEY; the upstream keys are optional
export APP_API_KEY=...    # same value as in .env (curl and `make smoke` read it from the shell)
make up                   # build, start Postgres + API; migrations and the first ETL run automatically
curl -s localhost:8001/health
curl -s -H "X-API-KEY: $APP_API_KEY" "localhost:8001/data?limit=5&sort=market_cap"
make smoke                # live end-to-end check incl. crash + resume and rate limit
make test                 # full suite in the test image against the compose Postgres
make down
```

| Service | Host port |
|---|---|
| API (Swagger at `/docs`, dashboard at `/dashboard`) | `8001` |
| PostgreSQL | `5433` |

Local development without Docker:

```bash
python -m pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5433/kasparro
uvicorn main:app --reload               # migrates on startup
python scripts/run_etl.py               # one ETL run
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/kasparro_test pytest --cov
make lint typecheck
```

Tests need PostgreSQL; SQLite isn't supported. The test database name must end in `_test`.
It is created if missing, and its schema is dropped and rebuilt.

---

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/data` | key | Coins. `page`, `limit` (1–100), `symbol`, `name`, `min_price`, `max_price`, `sort` (`price_usd`/`market_cap`/`symbol`), `order`. Returns `request_id`, `api_latency_ms`, `meta.total_records` |
| GET | `/stats` | key | Coin/observation counts, price aggregates, per-source run totals, last success/failure, last durations, latest run metadata |
| GET | `/runs?limit=N&source=` | key | Most recent `etl_runs` rows (status, timings, record counts, error, checkpoint, drift) |
| GET | `/compare-runs?source=&window=20&min_samples=5&sigma=3` | key | Per-source history, deltas and anomaly flags |
| GET | `/health` | public | DB status + latest run per source. `ok`; `degraded` if a source's latest run failed; HTTP 503 `down` if the DB is unreachable |
| GET | `/metrics` | public | Prometheus text format (no secrets) |
| GET | `/dashboard`, `/docs` | public | Static dashboard, Swagger UI |

Protected routes take an `X-API-KEY` header and count against that key's rate limit.
Responses carry `X-RateLimit-Limit/Remaining/Reset` headers. Over the limit you get
`429` with `Retry-After`.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | — (required) | PostgreSQL URL. Neon: direct endpoint with `?sslmode=require` |
| `APP_API_KEY`, `APP_API_KEYS` | — | Accepted client keys (`APP_API_KEYS` is comma-separated). Never sent upstream; only SHA-256 fingerprints are stored |
| `COINGECKO_API_KEY` | empty | Sent as `x-cg-demo-api-key` when set; keyless public tier otherwise |
| `COINPAPRIKA_API_KEY` | empty | Sent as `Authorization` when set; the free host doesn't need it |
| `COINGECKO_BASE_URL`, `COINPAPRIKA_BASE_URL` | public hosts | Host override (e.g. CoinPaprika Pro) |
| `COINGECKO_PAGES`, `COINGECKO_PER_PAGE`, `COINPAPRIKA_LIMIT` | 1, 50, 50 | How many coins to fetch |
| `<SOURCE>_MIN_INTERVAL` | 1.0 | Minimum seconds between requests to that source |
| `<SOURCE>_MAX_RETRIES`, `_BACKOFF_BASE`, `_BACKOFF_MAX` | 5, 1.0, 60 | Retry budget; backoff is `uniform(0, min(max, base·2^n))`; `Retry-After` honoured up to `_BACKOFF_MAX` |
| `CSV_PATH`, `QUIRKY_CSV_PATH` | `data/sample_coins.csv`, `data/quirky_coins.csv` | CSV inputs |
| `ETL_SOURCES` | all | Comma-separated subset: `coingecko,csv,quirky_csv,coinpaprika` |
| `ETL_BATCH_SIZE` | 25 | Records per commit (checkpoint granularity) |
| `RUN_ETL_ON_STARTUP`, `ETL_INTERVAL_SECONDS` | 1, 0 | API background ETL: once at startup / every N seconds |
| `FAIL_ON_ERROR` | 1 | `run_etl.py` exits 1 if any source run failed |
| `DRIFT_WARN_THRESHOLD`, `DRIFT_REJECT_THRESHOLD` | 0.90, 0.60 | Schema-drift confidence thresholds |
| `API_RATE_LIMIT`, `API_RATE_WINDOW_SECONDS` | 60, 60 | Requests per key per window |
| `FAIL_AFTER_N`, `FAIL_MODE`, `FAIL_SOURCES` | off, `raise`, all | Failure injection: crash after N records; `exit` kills the process |
| `DB_STARTUP_TIMEOUT` | 30 | Seconds the API waits for the DB before failing startup |

---

## Design decisions

- **One row per run per source** in `etl_runs`. It records status, start/end, duration,
  records in/inserted/updated/unchanged/invalid, error, checkpoint summary, drift report and
  metadata. `/stats`, `/runs`, `/compare-runs`, `/health` and `/metrics` all read from it.
- **Incremental by source type.**
  - APIs use a per-coin watermark on the source's `last_updated`. A record is written only
    if it's newer than the checkpoint.
  - CSVs are re-read every run, but only rows whose normalised content hash changed are
    written.
  - The checkpoints live in `checkpoints`.
- **Idempotent writes.**
  - Observations are upserted on `(source, coin_id, observed_at)`. The same values are a
    no-op; new values at the same `observed_at` count as `updated`.
  - Coins are unique on `(symbol, lower(name))`, and mappings on `(source, source_coin_id)`.
  - Re-running with the checkpoint deleted writes nothing new.
- **Crash safety.**
  - Every `ETL_BATCH_SIZE` records, one transaction commits the raw rows, normalised rows,
    checkpoint and run counters together. A crash loses at most the current batch.
  - The next run marks the dead `running` row as `failed (abandoned)` and resumes from the
    checkpoint.
  - A Postgres advisory lock per source stops two processes from ingesting the same source
    at once.
- **Validation never crashes the run.**
  - Each source has a Pydantic model (`schemas/coin.py`) that coerces messy values: `"$1,234"`,
    `1.2e12`, `N/A`, epoch or naive timestamps.
  - Rejected records are logged with the failing fields (no values), counted as `invalid`,
    and leave the run `partial`. Their raw payload is still kept in `raw_coins`.
- **Schema drift.**
  - CSV headers are normalised and matched to the canonical fields with rapidfuzz `WRatio`
    against aliases. Assignment is greedy, one header per field.
  - Confidence is the mean score of the required fields (`symbol`, `name`, `price_usd`).
  - Below 0.90 the run logs a warning; below 0.60 the file is rejected (run `failed`). The
    report is stored on the run either way.
  - `data/quirky_coins.csv` is real data with renamed, reordered and extra columns, odd
    types and blanks. It scores 0.88: ingested, with a warning.
- **Latest observation wins.** When sources disagree, `coins.price_usd` follows the newest
  `observed_at`. Every observation is kept in `price_snapshots`.
- **API rate limit in Postgres.** Fixed window per key fingerprint, so the limit holds across
  instances.
- **Anomalies.** For `records_in` and `duration_seconds`, the latest completed run gets a
  z-score against the previous `window` completed runs. It's flagged when `|z| > sigma` and
  at least `min_samples` exist. A flat history flags any change. A failed latest run is
  flagged separately.
- **Observability.**
  - JSON logs on stdout with Cloud Logging `severity`; uvicorn logs included.
  - Every source run emits `event=etl_run_finished` with its counts, which the GCP
    log-based metrics are built on.
  - `/metrics` reads ETL state from the DB at scrape time, plus in-process HTTP metrics.

---

## Brief coverage

Status reflects what tests or CI prove. "Prepared" means code and docs exist but were not
run against a real cloud project.

| Item | Status | Where | Proof |
|---|---|---|---|
| P0.1 API + CSV → `raw_*` | done | `ingestion/`, `services/store.py` (`raw_coins`) | `test_etl.py::test_csv_run_records_etl_run`, `::test_invalid_rows_logged_and_counted` |
| P0.2 Unified schema | done | `core/models.py`, `services/store.py` | `test_etl.py::test_latest_observation_wins_across_sources` |
| P0.3 Pydantic validation | done | `schemas/coin.py`, `services/etl.py::_validate` | `test_schemas.py`, `test_etl.py::test_invalid_rows_logged_and_counted` |
| P0.4 Incremental | done | `ingestion/base.py`, `services/etl.py` | `test_etl.py::test_api_watermark_incremental`, `::test_csv_rereads_and_only_writes_changed_rows` |
| P0.5 Secure key auth | done | `core/security.py`, `api/deps.py`, `ingestion/coingecko.py`, `ingestion/coinpaprika.py` | `test_api.py::test_protected_routes_require_key`, `test_http.py::test_coingecko_keyless` (app key never sent upstream), `::test_secret_headers_never_logged` |
| P0.6 `/data` | done | `api/routers/data.py` | `test_api.py::test_get_data`, `::test_data_filters_sorting_pagination` |
| P0.7 `/health` | done | `api/routers/health.py` | `test_api.py::test_health_*` |
| P0.8 make / Docker / compose / README | done | `Makefile`, `Dockerfile`, `docker-compose.yml` | CI jobs `docker build` and `make test` |
| P0.9 Image auto-starts ETL + API | done | `api/app.py::lifespan` | `test_api.py::test_startup_migrates_then_runs_etl` |
| P0.10 Tests (transforms, endpoint, failure) | done | `tests/` | CI `pytest` job |
| P1.1 Third source unified | done (4 sources) | `ingestion/registry.py` | `test_etl.py::test_latest_observation_wins_across_sources`, `::test_quirky_csv_is_real_data_with_drift` |
| P1.2 Checkpoints, resume, idempotent | done | `services/etl.py`, `services/store.py` | `test_etl.py::test_injected_crash_then_resume_without_duplicates`, `::test_upsert_is_idempotent_without_checkpoint` |
| P1.3 `/stats` | done | `services/runs.py::stats` | `test_api.py::test_stats_reads_etl_runs` |
| P1.4 Tests: incremental, failures, schema mismatch, endpoints, rate limiting | done | `tests/` | `test_etl.py`, `test_drift.py`, `test_api.py::test_rate_limit_*`, `test_http.py` |
| P1.5 Layout incl. `services/` | done | see Architecture | — |
| P2.1 Schema drift (fuzzy, confidence, warnings) | done | `ingestion/drift.py` | `test_drift.py`, `test_etl.py::test_drift_below_floor_rejects_file` |
| P2.2 Failure injection + clean resume | done | `services/etl.py` (`FAIL_AFTER_N`) | `test_etl.py::test_hard_crash_process_exit_then_resume` (real process kill) |
| P2.3 Per-source rate limit + backoff + logs | done | `ingestion/http.py` | `test_http.py::test_retry_with_exponential_backoff_and_jitter`, `::test_429_retry_after_seconds_is_honoured`, `::test_per_source_min_interval` |
| P2.4 `/metrics` + JSON logs | done | `services/metrics.py`, `core/logger.py` | `test_api.py::test_metrics_prometheus_format_without_secrets`, `test_logging.py` |
| P2.5 CI, image publishing, healthchecks | done | `.github/workflows/ci.yml`, `deploy.yml` (GHCR), `Dockerfile` `HEALTHCHECK` | CI green; Deploy workflow `publish image (GHCR)` succeeded on `main` |
| P2.6 `/runs`, `/compare-runs`, anomalies | done | `services/runs.py` | `test_api.py::test_runs_limit_and_source_filter`, `::test_compare_runs_*` |
| F.1 Provided keys used, none hardcoded | done (fakes only) | `ingestion/*.py`, `.env.example` | `test_http.py::test_coingecko_demo_key_base_url_and_pages`, `::test_coinpaprika_key_and_base_url`; not yet tried with real upstream keys |
| F.2 Image runs without code changes | done | `Dockerfile` | CI `docker build` + `make test`; `make up` + live smoke run manually |
| F.3 Cloud deploy, public API | prepared | `deploy.yml`, `docs/DEPLOY_GCP.md` | actionlint in CI; not deployed |
| F.4 Cloud-scheduled ETL | prepared | Cloud Run job + Cloud Scheduler in `deploy.yml` | not deployed |
| F.5 Logs + metrics in console | prepared | `deploy/gcp/log-metrics/`, Cloud Logging `severity` | `test_logging.py`; not deployed |
| F.6 Full test suite | done | `tests/` (111 tests) | CI: 95% coverage, gate 85% |
| F.7 Live smoke (ingest, API, resume after restart, rate limit) | done locally | `scripts/smoke_test.py`, `make smoke` | `test_smoke.py` (live uvicorn + real ETL subprocesses); not yet run against the cloud |

---

## Known limitations

- **No live cloud deployment yet.** The GCP workflow, bootstrap script and docs are written
  and linted but untested against a real project. `/metrics` is not scraped on Cloud Run;
  the console metrics are log-based.
- **Upstream keys are tested only with fakes.** The CoinGecko demo key and CoinPaprika key
  headers are verified in unit tests, not against the real APIs. The default keyless tiers
  were exercised live.
- **The API rate limit is a fixed window.** A client can burst up to 2× the limit across a
  window boundary.
- **Fetch volume is small.** By default only the top 50 coins come from each API.
- **One raw table.** Raw data goes to a single `raw_coins` table with a `source` column,
  not one `raw_*` table per source.
- **Coin identity is `(symbol, name)`.** The same asset listed under different names by
  different sources stays separate; same ticker with a different name is kept apart on
  purpose.
- **CSV timestamps can fall back to the file mtime.** CSV rows without a timestamp column
  use the file's modification time as `observed_at`.
- **CSV checkpoints grow with keys.** The CSV content checkpoint stores one hash per source
  key in `checkpoints`, which suits small files.
- **Anomaly detection is narrow.** It only looks at `records_in` and `duration_seconds`.
- **The dashboard is barely tested.** `/dashboard` is a static page; only "serves 200" is
  tested.
