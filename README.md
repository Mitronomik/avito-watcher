# Avito watcher skeleton v3

Стартовый production-oriented skeleton для мониторинга поисковой выдачи недвижимости: Playwright + PostgreSQL + Telegram + Ollama.

## Что нового в v3
- CLI-команда для `seed-search`, `run-once`, `run-all` и `telegram-bot`
- bootstrap-скрипт для быстрого старта
- `docker compose` с отдельными сервисами для API, worker и Telegram command bot
- пример сидирования search job из CLI
- более понятный локальный запуск

## Быстрый старт
```bash
cp .env.example .env
./scripts/bootstrap.sh
```

## Docker Compose

Все команды ниже используют основной compose-файл:

```bash
docker compose -f deploy/docker-compose.yml <command>
```

### Запустить только инфраструктуру

Инфраструктура включает PostgreSQL, Redis и Ollama без API, worker и Telegram command bot:

```bash
make infra
```

Эквивалентная команда без Makefile:

```bash
docker compose -f deploy/docker-compose.yml up -d postgres redis ollama
```

### Запустить API

API-сервис запускает только FastAPI/admin-приложение и не выполняет автоматический мониторинг Avito. FastAPI нужен для healthcheck, управления поисками и ручных admin-действий:

```bash
make api
```

Эквивалентная команда без Makefile:

```bash
docker compose -f deploy/docker-compose.yml up app
```

### Запустить worker

Worker — единственный процесс, который автоматически выполняет мониторинг сохранённых SearchJob и отправку релевантных алертов:

```bash
make worker
```

Эквивалентная команда без Makefile:

```bash
docker compose -f deploy/docker-compose.yml up worker
```

> ⚠️ При первом запуске worker инициализирует baseline для поисков и не отправляет алерты по уже существующим объявлениям. Алерты появляются только для новых объявлений после baseline.


### Запустить API + worker

Команда `make up` запускает оба сервиса: FastAPI app и worker. При этом автоматический мониторинг выполняет только worker; FastAPI остаётся API/admin-приложением и не планирует фоновые циклы мониторинга.

```bash
make up
```

### Запустить Telegram command bot

Telegram command bot запускается отдельным сервисом, принимает команды Telegram и управляет SearchJob в базе данных. Он не запускает мониторинг, не парсит Avito напрямую и не открывает порты.

```bash
make bot
```

Эквивалентная команда без Makefile:

```bash
docker compose -f deploy/docker-compose.yml up telegram_bot
```

Логи и рестарт Telegram bot:

```bash
make bot-logs
make bot-restart
```

## Ручной локальный запуск
```bash
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


## Ручной запуск мониторинга

Эндпоинт `POST /monitor/run` — manual/admin-only запуск одного прохода для первого активного поиска. Он не является scheduler и не запускается FastAPI автоматически; если задан `API_KEY`, запрос должен передавать заголовок `X-API-Key`. Для автоматического мониторинга используйте worker.

CLI-команды `python -m app.cli run-once` и `python -m app.cli run-all` также предназначены для явного ручного запуска.

## Telegram-команды
```text
/start
/help
/add <url> [name]
/list
/pause <search_id>
/resume <search_id>
/status
/showfilters <search_id>
/setfilters <search_id> key=value key=value ...
/clearfilters <search_id>
```

Telegram-команды управляют SearchJob в базе данных и не запускают мониторинг или парсинг Avito.
Команды фильтров только читают и обновляют `SearchJob.filters_json`: они не запускают worker, AvitoParser, LLM-оценку и не отправляют алерты по объявлениям.

Примеры управления фильтрами:

```text
/setfilters 1 max_age_hours=24 require_published_at=true
/setfilters 1 max_price=30000000 min_area=40 exclude_keywords=доля,аренда
/showfilters 1
/clearfilters 1
```

Поддерживаемые ключи: `min_price`, `max_price`, `min_area`, `max_area`, `include_keywords`, `exclude_keywords`, `location_keywords`, `max_age_hours`, `published_after`, `published_on_date`, `require_published_at`.
Для свежих объявлений рекомендуется настройка `max_age_hours=24 require_published_at=true`: она ограничивает выдачу объявлениями с распознанной датой публикации за последние 24 часа.
