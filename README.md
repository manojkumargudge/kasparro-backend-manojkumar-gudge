# Kasparro Backend

A FastAPI service that ingests cryptocurrency market data, stores it in PostgreSQL, and exposes data, statistics, ETL health, and Prometheus-compatible metrics through a REST API and a browser dashboard.

![Python](https://img.shields.io/badge/python-3.11-blue)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![PostgreSQL](https://img.shields.io/badge/DB-PostgreSQL%2015-336791)
![Docker](https://img.shields.io/badge/container-Docker-2496ED)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF)
![Deploy](https://img.shields.io/badge/deploy-Fly.io-8B5CF6)

---

## Features

- ETL adapters for **CoinGecko**, **CoinPaprika** and **CSV files**
- Incremental ETL checkpoints and run metadata
- Filterable, paginated data API
- Statistics, ETL health, run history and run comparison endpoints
- Prometheus-compatible `/metrics`
- API key protection and per-client rate limiting
- Browser-based operational dashboard
- Docker Compose for local development
- Pytest suite, GitHub Actions CI, and hourly scheduled ETL

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

When the API starts, it also launches a background ETL task. This task retries until the database is available.

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
| Hosting | Fly.io |

---

## Repository layout

```text
api/                  API-specific modules
core/                 Database, models, authentication, logging
data/                 Sample CSV input files
ingestion/            CoinGecko, CoinPaprika and CSV adapters
scripts/              DB init, ETL runner, smoke test, helper commands
static/               Operational dashboard
tests/                Automated tests
main.py               FastAPI entry point
Dockerfile            API container image
docker-compose.yml    Local development stack
fly.toml              Fly.io configuration
```

---

## Prerequisites

**Docker workflow:** Docker Desktop with Docker Compose.

**Local Python workflow:** Python 3.11 and a PostgreSQL 15 database (or any SQLAlchemy-compatible database URL).

---

## Configuration

Create a `.env` file in the repository root. Never commit real credentials.

```dotenv
DATABASE_URL=postgresql://postgres:postgres@db:5432/kasparro
APP_API_KEY=change-me
COINPAPRIKA_API_KEY=
COINGECKO_MAX_RETRIES=5
COINGECKO_MIN_INTERVAL=1.0
COINPAPRIKA_MAX_RETRIES=5
COINPAPRIKA_MIN_INTERVAL=1.0
FAIL_ON_ERROR=1
```

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Use host `db` inside Docker Compose. On the host machine use your own host and port, for example `postgresql://postgres:postgres@localhost:5433/kasparro` |
| `APP_API_KEY` | Required for protected endpoints. Sent in the `X-API-KEY` header |
| `COINPAPRIKA_API_KEY` | Optional, but CoinPaprika ingestion reports an error without it |
| `COINGECKO_MAX_RETRIES`, `COINPAPRIKA_MAX_RETRIES` | Retry limits for each source |
| `COINGECKO_MIN_INTERVAL`, `COINPAPRIKA_MIN_INTERVAL` | Minimum seconds between requests |
| `FAIL_ON_ERROR` | `1` (default) exits non-zero when ingestion errors occur. `0` allows a successful exit |

---

## Run with Docker Compose

```bash
docker compose up --build
```

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
python -m scripts.init_db
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## Run the ETL manually

```powershell
python scripts\run_etl.py
```

---

## Testing

```powershell
docker compose run --rm api pytest -q     # in Docker
.\scripts\test.ps1                        # helper script
```

Smoke test after starting the API:

```powershell
$env:APP_API_KEY = "change-me"
python scripts\smoke_test.py --base-url http://localhost:8001
```

With `TESTING=1`, the test suite uses a file-backed SQLite database, so no running PostgreSQL is needed.

---

## CI/CD and scheduled ETL

```mermaid
flowchart LR
    PR[Push or PR to main] --> CI[ci.yml<br/>Python 3.11 + pytest -q]
    CRON[Hourly schedule<br/>or manual trigger] --> ETLJ[scheduled-etl.yml<br/>run ETL]
    PUSH[Push to main] --> DEP[Fly.io deploy workflow]
    DEP --> FLY[Fly.io app]

    classDef wf fill:#e8f1ff,stroke:#3b82f6,color:#111
    class CI,ETLJ,DEP wf
```

| Workflow | Trigger | Needs |
|---|---|---|
| `ci.yml` | Push and PR to `main` | none |
| `scheduled-etl.yml` | Hourly, and `workflow_dispatch` | Secrets `APP_API_KEY`, `COINPAPRIKA_API_KEY` |
| Fly.io deploy | Push to `main` | Secret `FLY_API_TOKEN`; `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` if Docker Hub login is required |

---

## Deploy to Fly.io

Set `DATABASE_URL`, `APP_API_KEY` and `COINPAPRIKA_API_KEY` as Fly secrets or environment variables. Check the `app` name and `primary_region` in `fly.toml` first.

```bash
flyctl auth login
flyctl deploy --config fly.toml
```

---

## Security notes

- Use a long, unique `APP_API_KEY` outside local development.
- Keep API keys and database credentials in environment variables or a secret manager, never in source control.
- The sample Docker Compose credentials are for local development only.
- Restrict PostgreSQL network access in non-local environments.