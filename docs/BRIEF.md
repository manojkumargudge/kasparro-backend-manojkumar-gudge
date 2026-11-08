# Kasparro Backend & ETL — Brief (summary)

This is the requirements checklist the repository is graded against. Item IDs (P0.x, P1.x,
P2.x, F.x) are used in the README coverage table and in PR descriptions.

## P0 — Foundation

| ID | Requirement |
|---|---|
| P0.1 | Ingest **1 API source + 1 CSV source** into `raw_*` tables |
| P0.2 | Normalize into a **unified schema** |
| P0.3 | **Pydantic validation** of ingested records |
| P0.4 | **Incremental** ingestion (no reprocessing of unchanged data) |
| P0.5 | **Secure key auth** (keys from environment, never hardcoded) |
| P0.6 | `GET /data`: pagination, filters, `request_id`, `api_latency_ms` |
| P0.7 | `GET /health`: DB connectivity + last ETL status |
| P0.8 | `make up` / `make down` / `make test`, Dockerfile, docker compose, README |
| P0.9 | Image auto-starts ETL + API |
| P0.10 | Tests: transforms, at least one endpoint, at least one failure case |

## P1 — Growth

| ID | Requirement |
|---|---|
| P1.1 | **Third source**, unified into the same schema |
| P1.2 | **Checkpoint table**, resume-on-failure, idempotent writes |
| P1.3 | `GET /stats`: records processed, duration, last success / last failure, run metadata |
| P1.4 | Tests for incremental ingestion, failures, schema mismatch, endpoints, rate limiting |
| P1.5 | Layout: `ingestion/`, `api/`, `services/`, `schemas/`, `core/`, `tests/` |

## P2 — Differentiators

| ID | Requirement |
|---|---|
| P2.1 | **Schema drift** detection: fuzzy column matching, confidence score, warnings |
| P2.2 | **Failure injection** + clean resume, no duplicates, run metadata recorded |
| P2.3 | **Per-source rate limiting** + retry with backoff + logging |
| P2.4 | `GET /metrics` in Prometheus format + structured JSON logs |
| P2.5 | CI, container image publishing, Docker healthchecks |
| P2.6 | `GET /runs?limit=N`, `GET /compare-runs`, anomaly detection |

## Final evaluation

| ID | Requirement |
|---|---|
| F.1 | All upstream calls use the provided keys; no hardcoded keys |
| F.2 | Docker image runs without code changes |
| F.3 | Cloud deployment (AWS / GCP / Azure) with a **public API** |
| F.4 | **Cloud-scheduled ETL** |
| F.5 | Logs + metrics visible in the cloud console |
| F.6 | Full automated test suite |
| F.7 | Live smoke test: ingest, API, resume after restart, rate limit |
