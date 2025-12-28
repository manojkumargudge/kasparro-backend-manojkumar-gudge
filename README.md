# 🚀 Kasparro Backend & ETL System

A production-style backend system that ingests cryptocurrency market data, stores it in PostgreSQL, and exposes it via REST APIs using FastAPI.  
The entire system is fully containerized using Docker Compose.

---

## 🧱 Architecture Overview

```text
Client
  |
  v
FastAPI (Docker)
  |
  v
PostgreSQL (Docker)


