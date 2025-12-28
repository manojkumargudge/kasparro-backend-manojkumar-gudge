up:
	docker compose up --build --force-recreate -d

down:
	docker compose down

test:
	docker compose run --rm api pytest -q
