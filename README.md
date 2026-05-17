# Avito watcher skeleton v3

Стартовый production-oriented skeleton для мониторинга поисковой выдачи недвижимости: Playwright + PostgreSQL + Telegram + Ollama.

## Что нового в v3
- CLI-команда для `seed-search`, `run-once`, `run-all`
- bootstrap-скрипт для быстрого старта
- `docker compose` с app-сервисом
- пример сидирования search job из CLI
- более понятный локальный запуск

## Быстрый старт
```bash
cp .env.example .env
./scripts/bootstrap.sh
```

## Ручной запуск
```bash
docker compose -f deploy/docker-compose.yml up -d
pip install -r requirements.txt
playwright install chromium
alembic upgrade head
python -m app.cli seed-search --name spb_flats --url 'https://www.avito.ru/all/kvartiry/prodam-ASgBAgICAUSSA8YQ'
python -m app.cli run-once
uvicorn app.main:app --reload
```

## Команды CLI
```bash
python -m app.cli seed-search --name test --url 'https://www.avito.ru/all/kvartiry/prodam-ASgBAgICAUSSA8YQ'
python -m app.cli run-once
python -m app.cli run-all
python -m app.cli telegram-bot
```

## Telegram-команды
```text
/start
/help
/add <name> <url>
/list
/pause <search_id>
/resume <search_id>
/status
```

Telegram-команды управляют SearchJob в базе данных и не запускают мониторинг или парсинг Avito.
