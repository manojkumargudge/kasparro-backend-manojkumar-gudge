.PHONY: up down test lint typecheck migrate etl smoke

up:
	docker compose up --build --force-recreate -d

down:
	docker compose down

# Runs the suite in the test image against the compose Postgres (kasparro_test DB).
test:
	docker compose run --rm --build test

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy

migrate:
	python -m scripts.init_db

etl:
	python scripts/run_etl.py

# Live smoke test against `make up`: crash + resume via one-off compose ETL runs.
SMOKE_ETL = docker compose run --rm -e ETL_SOURCES=coinpaprika -e ETL_BATCH_SIZE=10
smoke:
	python scripts/smoke_test.py --base-url http://localhost:8001 --api-key "$${APP_API_KEY:-testkey}" \
	  --crash-cmd "$(SMOKE_ETL) -e FAIL_AFTER_N=25 -e FAIL_MODE=exit api python scripts/run_etl.py" \
	  --resume-cmd "$(SMOKE_ETL) api python scripts/run_etl.py"
