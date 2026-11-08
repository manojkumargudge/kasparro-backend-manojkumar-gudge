# Kasparro Backend

A FastAPI service that ingests cryptocurrency market data, stores it in PostgreSQL, and exposes data, statistics, ETL health, and Prometheus-compatible metrics through a REST API and a browser dashboard.

![Python](https://img.shields.io/badge/python-3.11-blue)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![PostgreSQL](https://img.shields.io/badge/DB-PostgreSQL%2015-336791)
![Docker](https://img.shields.io/badge/container-Docker-2496ED)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF)

---

## Features

- ETL adapters for **CoinGecko**, **CoinPaprika** and **CSV files**
- Every ingested record validated with Pydantic; invalid rows are logged and counted, not fatal
- Alembic schema migrations, applied on API startup and before every ETL run
- Incremental checkpoints for CSV sources and per-run metadata
- Filterable, paginated data API
- Statistics, ETL health, run history and run comparison endpoints
- Prometheus-compatible `/metrics`
- API key protection and per-client rate limiting
- Browser-based operational dashboard
- Docker Compose for local development
- Pytest suite against PostgreSQL; GitHub Actions CI (ruff, mypy, pytest + coverage, docker build, `make test`)

---

## Architecture

```mermaid
flowchart LR
    CG[CoinGecko API] --> ETL
    CP[CoinPaprika API] --> ETL
    CSV[CSV files in data/] --> ETL

    subgraph Backend[Kasparro Backend]
        ETL[ETL pipelines<br/>checkpoints + run metadata] --> DB[(PostgreSQL 15)]
        DB --> API[FastAPI REST API]
    end

    API --> DASH[Dashboard<br/>/dashboard]
    API --> CONS[API consumers]
    API --> PROM[Prometheus<br/>/metrics]

    classDef src fill:#fff4e5,stroke:#f59e0b,color:#111
    classDef store fill:#e8f1ff,stroke:#3b82f6,color:#111
    classDef svc fill:#e9f9ee,stroke:#22c55e,color:#111
    class CG,CP,CSV src
    class DB store
    class ETL,API svc
```

---

## ETL flow

The runner processes sources in this order: CoinGecko, then CSV, then CoinPaprika.

```mermaid
flowchart TD
    S([Start ETL]) --> A[1. CoinGecko ingestion]
    A --> B[2. CSV ingestion<br/>files under data/]
    B --> C[3. CoinPaprika ingestion]
    C --> D[Save checkpoint + run metadata]
    D --> E{Any source<br/>reported errors?}
    E -->|No| OK([Exit 0])
    E -->|Yes| F{FAIL_ON_ERROR}
    F -->|1 default| BAD([Exit non-zero])
    F -->|0| OK

    classDef good fill:#e9f9ee,stroke:#22c55e,color:#111
    classDef bad fill:#fdecec,stroke:#ef4444,color:#111
    class OK good
    class BAD bad
```

Before ingesting, the runner applies Alembic migrations. When the API starts it waits for the database, applies migrations (startup fails if it cannot), then launches the ETL as a background task (disable with `RUN_ETL_ON_STARTUP=0`).

Each record is validated by a per-source Pydantic model (`schemas/coin.py`). Rejected records are logged with the failing fields and counted as `invalid` in the run metadata; the raw payload is still stored in `raw_coins`. Invalid records make a run `partial` but do not fail the ETL; fetch or database errors do (subject to `FAIL_ON_ERROR`).

---

## Request flow

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as FastAPI
    participant D as PostgreSQL

    C->>A: GET /data (X-API-KEY header)
    A->>A: Check API key
    A->>A: Check rate limit (30 requests / 60 s)
    alt Over limit
        A-->>C: HTTP 429
    else Allowed
        A->>D: Query with filters, sort, pagination
        D-->>A: Rows
        A-->>C: JSON response
    end
```

---

## API

Interactive docs are available at `/docs` while the API is running.

### Public endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Database and ETL health summary |
| GET | `/dashboard` | Operational dashboard |
| GET | `/docs` | Swagger UI |
| GET | `/openapi.json` | OpenAPI schema |

### Protected endpoints

Send `X-API-KEY: <APP_API_KEY>` with every request.

| Method | Path | Description |
|---|---|---|
| GET | `/data` | Paginated, filterable coin data |
| GET | `/stats` | Record count, price aggregates, ETL summary |
| GET | `/metrics` | Prometheus-compatible metrics |
| GET | `/runs` | Recent ETL runs by source |
| GET | `/compare-runs` | ETL anomalies and run-to-run changes |

### `/data` query parameters

| Parameter | Description | Default |
|---|---|---|
| `page` | 1-based page number | `1` |
| `limit` | Records per page, 1 to 100 | `10` |
| `symbol` | Case-insensitive symbol filter | none |
| `name` | Case-insensitive name filter | none |
| `min_price`, `max_price` | Price range filters | none |
| `sort` | `price_usd` or `market_cap` | `price_usd` |
| `order` | `asc` or `desc` | `desc` |

### Examples (Docker Compose port)

```bash
curl http://localhost:8001/health
curl -H "X-API-KEY: change-me" "http://localhost:8001/data?limit=20&sort=market_cap&order=desc"
curl -H "X-API-KEY: change-me" http://localhost:8001/stats
curl -H "X-API-KEY: change-me" http://localhost:8001/metrics
```

All non-health routes are rate limited to **30 requests per client per 60 seconds**. Extra requests get HTTP `429`.

---

## Tech stack

| Layer | Tool |
|---|---|
| Language | Python 3.11 |
| API | FastAPI, Uvicorn |
| Database | PostgreSQL 15, SQLAlchemy |
| HTTP client | Requests |
| Testing | Pytest |
| Containers | Docker, Docker Compose |
| CI and scheduling | GitHub Actions |
| Migrations | Alembic |
| Lint / types | Ruff, mypy |

---

## Repository layout

```text
core/                 Database, models, authentication, logging
data/                 Sample CSV input files
ingestion/            CoinGecko, CoinPaprika and CSV adapters + shared pipeline
migrations/           Alembic migrations
schemas/              Pydantic models for every ingested record
scripts/              DB init, ETL runner, smoke test, helper commands
static/               Operational dashboard
tests/                Automated tests
main.py               FastAPI entry point
Dockerfile            API container image
docker-compose.yml    Local development stack
```

---

## Prerequisites

**Docker workflow:** Docker Desktop with Docker Compose.

**Local Python workflow:** Python 3.11 and a PostgreSQL 15 database.

---

## Configuration

Copy `.env.example` to `.env` and fill it in. Never commit `.env` (it is also excluded from Docker images).

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Required. Compose defaults to `postgresql://postgres:postgres@db:5432/kasparro`. From the host use port `5433` |
| `APP_API_KEY` | Required for protected endpoints. Sent by clients in the `X-API-KEY` header. Never sent upstream |
| `COINGECKO_API_KEY` | Optional. Sent as `x-cg-demo-api-key`. Without it the keyless public tier is used |
| `COINPAPRIKA_API_KEY` | Optional. Sent as `Authorization`. The free host does not need it |
| `COINGECKO_BASE_URL`, `COINPAPRIKA_BASE_URL` | Optional host overrides (e.g. `https://api-pro.coinpaprika.com/v1`) |
| `COINGECKO_MAX_RETRIES`, `COINPAPRIKA_MAX_RETRIES` | Retry limits for each source (default 5) |
| `COINGECKO_MIN_INTERVAL`, `COINPAPRIKA_MIN_INTERVAL` | Minimum seconds between requests; also the backoff base (default 1.0) |
| `FAIL_ON_ERROR` | `1` (default): `run_etl.py` exits non-zero on fetch/database errors. `0` allows a successful exit |
| `RUN_ETL_ON_STARTUP` | `1` (default): the API runs the ETL in the background after migrating |

---

## Run with Docker Compose

```bash
docker compose up --build
```

The `api` container applies migrations itself on startup; no separate init step is needed.

```mermaid
flowchart LR
    U([You]) -->|localhost:8001| API[api container<br/>port 8000]
    API --> DB[(db container<br/>PostgreSQL, port 5432)]
    DB --- V[(postgres_data volume)]
    U -.->|localhost:5433| DB
```

| Service | Host port | Container port |
|---|---|---|
| API | `8001` | `8000` |
| PostgreSQL | `5433` | `5432` |

Helper scripts (PowerShell):

```powershell
.\scripts\up.ps1      # start in the background
.\scripts\down.ps1    # stop the stack
```

Database data is stored in the `postgres_data` Docker volume.

---

## Run locally without Docker

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Set `DATABASE_URL` and `APP_API_KEY`, then:

```powershell
python -m pip install -r requirements-dev.txt   # optional: tests, ruff, mypy
python -m scripts.init_db                        # optional: the API also migrates on startup
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Create a new migration after changing `core/models.py`:

```powershell
alembic revision --autogenerate -m "describe change"
```

---

## Run the ETL manually

```powershell
python scripts\run_etl.py
```

---

## Testing

Tests run against PostgreSQL (not SQLite). The target database name must end in `_test`; its schema is dropped and rebuilt with Alembic, and it is created if missing.

```powershell
make test                                   # test image + compose Postgres (kasparro_test)
.\scripts	est.ps1                          # same, without make

# Local Python against your own Postgres:
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/kasparro_test"
pytest --cov
ruff check .
mypy
```

Smoke test after starting the API:

```powershell
$env:APP_API_KEY = "change-me"
python scripts\smoke_test.py --base-url http://localhost:8001
```

---

## CI

`.github/workflows/ci.yml` runs on pushes and pull requests to `main`:

| Job | What it does |
|---|---|
| ruff + mypy | `ruff check .`, `mypy` (config in `pyproject.toml`) |
| pytest (Postgres) + coverage | Postgres 15 service container; `pytest --cov` (fails under 70%); coverage table in the job summary and `coverage.xml` artifact |
| docker build | Builds the runtime image |
| make test | Runs `make test` (compose) end to end |

Deployment and scheduled ETL are not configured yet.

---

## Security notes

- Use a long, unique `APP_API_KEY` outside local development.
- Keep API keys and database credentials in environment variables or a secret manager, never in source control.
- The sample Docker Compose credentials are for local development only.
- Restrict PostgreSQL network access in non-local environments.