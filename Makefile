COMPOSE := docker compose --env-file .env
UV ?= uv

.PHONY: up down verify logs sync lock lint test

.env:
	cp .env.example .env
	@echo "Created .env from .env.example; set LOCALSTACK_AUTH_TOKEN in it."

up: .env
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

verify:
	./scripts/verify-localstack.sh

logs:
	$(COMPOSE) logs -f

sync:
	$(UV) sync --all-packages

lock:
	$(UV) lock

lint:
	$(UV) run ruff check libs services
	$(UV) run ruff format --check libs services

test:
	$(UV) run --all-packages pytest libs/observability
	cd services/commerce && $(UV) run --all-packages pytest
