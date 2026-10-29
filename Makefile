.PHONY: up down logs test lint samples backtest eval etl shell observability

# Report-writing commands run as your user on Linux, so files in ./samples are yours (Docker Desktop ignores it).
UIDGID := APP_UID=$(shell id -u 2>/dev/null || echo 10001) APP_GID=$(shell id -g 2>/dev/null || echo 10001)

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker mailer seed

test:
	docker compose run --rm --no-deps api pytest

lint:
	docker compose run --rm --no-deps api ruff check app tests migrations

samples:
	$(UIDGID) docker compose run --rm tools python -m app.cli samples

backtest:
	$(UIDGID) docker compose run --rm tools python -m app.cli backtest

eval:
	$(UIDGID) docker compose run --rm tools python -m app.cli eval

etl:
	docker compose run --rm api python -m app.cli etl --force

shell:
	docker compose run --rm api bash

observability:
	docker compose --profile observability up -d prometheus
