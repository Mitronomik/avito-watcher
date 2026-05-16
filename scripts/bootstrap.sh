#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env created from template"
fi

docker compose -f deploy/docker-compose.yml up -d
python -m pip install -r requirements.txt
python -m playwright install chromium
alembic upgrade head
python -m app.cli seed-search --name default_spb --url 'https://www.avito.ru/all/kvartiry/prodam-ASgBAgICAUSSA8YQ' || true
python -m app.cli run-once || true

echo "Bootstrap complete"
echo "Run API: uvicorn app.main:app --reload"
