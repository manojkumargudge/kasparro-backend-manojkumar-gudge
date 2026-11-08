.PHONY: up down test lint typecheck migrate etl

up:
	docker compose up --build --force-recreate -d

down:
	docker compose down

# Runs the suite in the test image against the compose Postgres (kasparro_test DB).
test:
	docker compose run --rm --build test

lint:
	ruff check .

typecheck:
	mypy

migrate:
	python -m scripts.init_db

etl:
	python scripts/run_etl.py
