COMPOSE := docker compose --env-file .env
UV ?= uv
SERVICES := commerce entitlement plant-care expert-consultation
COVERAGE_GATE := --cov-fail-under=70

.PHONY: up down import-kb verify demo client-env deploy-frontend catalog-images catalog-labels logs sync lock lint contracts test test-unit test-db-up test-db-down

.env:
	cp .env.example .env
	@echo "Created .env from .env.example; set LOCALSTACK_AUTH_TOKEN in it."

# The seeds are best effort: they need uv and, for the import, the internet; a failure does not fail `make up`.
up: .env
	$(COMPOSE) up -d --build --wait
	@$(MAKE) --no-print-directory catalog-images || echo "WARNING: the catalog images were NOT uploaded (needs uv and the originals in media/source/, which are not in git): run make catalog-images"
	@$(MAKE) --no-print-directory import-kb || echo "WARNING: the knowledge base was NOT imported (needs internet): run make import-kb"

down:
	$(COMPOSE) down

import-kb:
	$(COMPOSE) --profile import run --build --rm kb-import $(ARGS)

verify:
	./scripts/verify-localstack.sh

demo:
	./scripts/demo.sh $(ACT)

client-env:
	./scripts/client-env.sh

deploy-frontend:
	./scripts/deploy-frontend.sh $(DIST)

catalog-images:
	AWS_ENDPOINT_URL=http://localhost:4566 AWS_DEFAULT_REGION=us-east-1 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
		$(UV) run --script scripts/upload-catalog-images.py

# Refreshes services/commerce/app/catalog_labels.json from PlantSolve (internet, no database); commit the result.
catalog-labels:
	cd services/plant-care && $(UV) run --package plant-care python -m app.knowledge_import \
		--write-catalog-labels ../commerce/app/catalog_labels.json

logs:
	$(COMPOSE) logs -f

sync:
	$(UV) sync --all-packages

lock:
	$(UV) lock

lint:
	$(UV) run ruff check libs services
	$(UV) run ruff format --check libs services

contracts:
	for service in $(SERVICES); do \
		(cd services/$$service && UPDATE_CONTRACTS=1 $(UV) run --all-packages pytest tests/unit/test_contracts.py --no-cov) || exit 1; \
	done

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
