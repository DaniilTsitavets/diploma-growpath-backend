COMPOSE := docker compose --env-file .env
UV ?= uv
SERVICES := commerce entitlement plant-care expert-consultation
COVERAGE_GATE := --cov-fail-under=70

.PHONY: up down verify logs sync lock lint test test-unit test-db-up test-db-down

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

test-db-up:
	docker compose -f compose.test.yml up -d --wait

test-db-down:
	docker compose -f compose.test.yml down

test-unit:
	$(UV) run --all-packages pytest libs/observability
	for service in $(SERVICES); do \
		(cd services/$$service && $(UV) run --all-packages pytest -m "not db" $(COVERAGE_GATE)) || exit 1; \
	done

test: test-db-up
	$(UV) run --all-packages pytest libs/observability
	for service in $(SERVICES); do \
		(cd services/$$service && $(UV) run --all-packages pytest $(COVERAGE_GATE)) || exit 1; \
	done
