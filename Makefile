.PHONY: infra build migrate up down logs status seed run-once run-all api worker

COMPOSE=docker compose -f deploy/docker-compose.yml

infra:
	$(COMPOSE) up -d postgres redis ollama

build:
	$(COMPOSE) build app worker

migrate: build infra
	$(COMPOSE) run --rm -e PYTHONPATH=/app app alembic upgrade head

up: migrate
	$(COMPOSE) up -d app worker

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

status:
	$(COMPOSE) ps

seed:
	$(COMPOSE) run --rm app python -m app.cli seed-search --name default_spb --url 'https://www.avito.ru/all/kvartiry/prodam-ASgBAgICAUSSA8YQ'

run-once:
	$(COMPOSE) run --rm app python -m app.cli run-once

run-all:
	$(COMPOSE) run --rm app python -m app.cli run-all

api:
	$(COMPOSE) up app

worker:
	$(COMPOSE) up worker
