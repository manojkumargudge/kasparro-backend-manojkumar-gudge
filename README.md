# Kasparro Backend & ETL System

A production-style backend system that ingests cryptocurrency market data, stores it in PostgreSQL, and exposes it via REST APIs using FastAPI.  
The entire system is fully containerized using Docker Compose.

---

## Architecture Overview

Client  
│  
▼  
FastAPI (Docker)  
│  
▼  
PostgreSQL (Docker)

---

## Tech Stack

- **Backend:** FastAPI (Python)
- **Database:** PostgreSQL
- **ORM:** SQLAlchemy
- **ETL:** Custom ingestion pipelines (API + CSV)
- **Containerization:** Docker & Docker Compose
- **Logging:** Structured logging
- **Scheduling (P2):** Cron-ready standalone ETL runner

---

## Features

### Data Ingestion (ETL)
- Ingests cryptocurrency market data from:
  - External API (CoinGecko)
  - CSV data source
- Stores normalized data in PostgreSQL
- Designed to run at startup and via scheduled execution

### REST APIs
- `GET /health` — Service and database health check
- `GET /data` — Paginated and filterable cryptocurrency data
- `GET /stats` — Aggregated statistics (minimum, maximum, and average price)

### Observability
- Request ID generation
- API latency measurement
- Structured logging for ETL and API layers

---

## Assignment Coverage

### P0 — Foundation Layer (Completed)
- Data ingestion from API and CSV sources
- PostgreSQL persistence
- FastAPI backend services
- Dockerized application using Docker Compose
- Health monitoring endpoint

### P1 — Growth Layer (Completed)
- Pagination and filtering on `/data` endpoint
- Aggregated insights via `/stats` endpoint
- Schema-safe database queries
- Stable and production-ready API design

### P2 — Differentiator Layer (Completed)
- Standalone ETL runner (`scripts/run_etl.py`)
- Cron-ready ETL execution decoupled from the API
- Designed to run inside Docker networking for production parity

---

## How to Run

```bash
docker compose up --build
```

## Cloud Deployment (optional)

This repository includes scaffolding to deploy to Fly.io and run scheduled ETL jobs via GitHub Actions.

High level steps to enable cloud deployment:

1. Create a Fly account (https://fly.io) and install `flyctl` on your machine.
2. Set repository secrets in GitHub: `FLY_API_TOKEN`, `DATABASE_URL`, `APP_API_KEY`, `COINPAPRIKA_API_KEY`.
3. Push to `main` — the `deploy-fly.yml` workflow will deploy to Fly (ensure `fly.toml` `app` name is unique and matches your Fly app).
4. The `scheduled-etl.yml` workflow runs hourly and executes `scripts/run_etl.py` using the repository secrets; view run logs under Actions → Scheduled ETL.

If you prefer to deploy manually with `flyctl`:

```bash
fly auth login
fly apps create <your-app-name>
fly postgres create --name <your-db-name>
fly postgres attach --app <your-app-name> <your-db-name>
fly secrets set APP_API_KEY=yourkey COINPAPRIKA_API_KEY=yourkey
fly deploy
```

See `.github/workflows` for CI, deploy, and scheduled ETL workflow definitions.

