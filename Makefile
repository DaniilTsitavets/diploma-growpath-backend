COMPOSE := docker compose --env-file .env

.PHONY: up down verify logs

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
