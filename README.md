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
