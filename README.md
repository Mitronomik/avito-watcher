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

## Production deploy

- Чеклист первого продакшн-деплоя: `docs/deploy/production_checklist.md`
- Безопасный шаблон переменных окружения: `deploy/env.production.example`

## Runtime-флаги скрейпинга

- `SCRAPE_HEADLESS=true` — рекомендуется для Docker/VPS и используется по умолчанию.
- `SCRAPE_HUMANIZE=false` — опциональная «человечная» прокрутка/паузы; по умолчанию выключена.
- `PROXY_URLS` — опциональный список прокси через запятую.
- `SCRAPE_PREFERRED_ENGINE=auto|nodriver|camoufox` — выбор первого движка скрейпинга; по умолчанию `auto`.
- `SCORING_ENABLED=false` — опционально отключает LLM scoring, чтобы изолировать browser/proxy smoke от доступности Ollama/модели.

Поддерживаемые схемы прокси: `http://` и `https://`.

- Режим `camoufox` может быть полезен, если `nodriver` стабильно упирается в timeout на конкретном прокси.
- Fallback между движками остаётся включённым всегда, независимо от `SCRAPE_PREFERRED_ENGINE`.

Пример:

```bash
SCRAPE_HEADLESS=true
SCRAPE_HUMANIZE=false
PROXY_URLS=http://user:pass@host:port,https://user:pass@host2:port2
SCRAPE_PREFERRED_ENGINE=auto
```

### Troubleshooting прокси

- Ошибка вида `unsupported proxy scheme` — проверьте, что используется `http` или `https` (не `socks5`).
- Ошибка вида `proxy must include valid port` — проверьте, что в URL указан числовой порт.
- Если `PROXY_URLS` пустой, приложение работает в no-proxy режиме.

### Known nodriver cleanup warning on macOS

На macOS с Python 3.12 после **контролируемого** timeout в изолированном вызове `fetch_with_nodriver` может появляться финализаторное предупреждение вида:

- `Exception ignored in: <function BaseSubprocessTransport.__del__ ...>`
- `RuntimeError: Event loop is closed`

Это известное не-блокирующее ограничение cleanup в изолированном smoke-сценарии и **не означает**, что парсер/мониторинг упал.

Операционный критерий исправности — успешное выполнение `run-once` по пути `MonitorService` (проход завершается, fallback работает, алерты доставляются, состояние БД сохраняется корректно), а не отсутствие этого финализаторного warning в isolated nodriver smoke.

Если нужен практический smoke-check на macOS, используйте:

- `python3 -m app.cli run-once` как основной operational check;
- camoufox fallback как рабочий fallback-путь при nodriver timeout.

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

## Search profile analysis metadata

Search profiles loaded with `python -m app.cli upsert-search-profile --file <path>` can include analysis metadata in the `[filters]` TOML table. `analysis_profile` controls the specialized deterministic analysis provider used by `analyze-search-matches`; it does **not** affect Avito parsing, monitor cadence, alert filtering, notifiers, or alert delivery.

Documented `analysis_profile` values:

- `default` — generic deterministic local provider.
- `commercial_rent` — implemented deterministic commercial-rent provider.
- `flat_sale` — implemented deterministic apartment-purchase provider v0. It uses only already parsed listing/snapshot fields and simple v0 assumptions; it does not call LLMs or external APIs.
- `flat_rent` — implemented deterministic apartment-rent provider v0. It uses only already parsed listing/snapshot fields and simple v0 assumptions; it does not call LLMs or external APIs.

Commercial rent example:

```toml
[filters]
analysis_profile = "commercial_rent"
asset_type = "commercial"
deal_type = "rent"
require_published_at = true
max_age_hours = 72
missing_published_at_policy = "reject"
source_sort = "date"
```

Flat sale deterministic v0 example:

```toml
[filters]
analysis_profile = "flat_sale"
asset_type = "flat"
deal_type = "sale"
```

Flat rent deterministic v0 example:

```toml
[filters]
analysis_profile = "flat_rent"
asset_type = "flat"
deal_type = "rent"
```

## Search-aware analysis runbook

Run the operational guardrail first to inspect profile readiness without running analysis:

```bash
python3 -m app.cli check-analysis-profiles
```

Run deterministic search-aware analysis for one search with a small initial limit:

```bash
python3 -m app.cli analyze-search-matches --search-id <id> --limit 5
python3 -m app.cli analyze-search-matches --search-id <flat_sale_search_id> --limit 5
python3 -m app.cli analyze-search-matches --search-id <flat_rent_search_id> --limit 5
```

Inspect recent results in the database:

```sql
select id, search_job_id, context_key, listing_external_id, profile, analysis_version, status, score, verdict
from listing_analyses
where search_job_id = <id>
order by id desc
limit 20;
```

Operational notes:

- `listing_search_matches` are created by worker cycles after PR #136 deployment.
- Old listings will not automatically have matches unless a backfill is added later.
- First production rollout should use small `--limit` values.
- Analysis failures must not affect monitor, parser, or notifiers.

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


## Каналы алертов

Telegram теперь опциональный: можно запускать доставку без Telegram, например через JSONL + email.

Пример:

```bash
ALERT_CHANNELS=jsonl,email
```

Поддерживаемые каналы и порядок задаются в `ALERT_CHANNELS` (например `jsonl,email,telegram`).

- `jsonl` пишет алерты в локальный durable outbox: `JSONL_OUTBOX_PATH` (по умолчанию `./data/alerts.jsonl`).
- `email` отправляет plain text письма через SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`).
- `telegram` включается только при наличии конфигурации и при наличии канала в `ALERT_CHANNELS`.
- `google_sheets` отправляет JSON webhook в Apps Script (`GOOGLE_SHEETS_WEBHOOK_URL`, `GOOGLE_SHEETS_WEBHOOK_SECRET`, `GOOGLE_SHEETS_WEBHOOK_TIMEOUT_SEC`).



## Google Sheets webhook alerts

Telegram опционален: можно использовать только Google Sheets webhook-канал через `ALERT_CHANNELS=google_sheets`.

Пример `doPost(e)` для Google Apps Script:

```javascript
function doPost(e) {
  const data = JSON.parse(e.postData.contents || '{}');
  const expectedSecret = PropertiesService.getScriptProperties().getProperty('WEBHOOK_SECRET');
  if (!expectedSecret || data.secret !== expectedSecret) {
    return ContentService.createTextOutput(JSON.stringify({ ok: false, error: 'forbidden' }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('alerts');
  sheet.appendRow([
    data.sent_at || '',
    data.search_name || '',
    data.external_id || '',
    data.title || '',
    data.price || '',
    data.area_m2 || '',
    data.rooms || '',
    data.address || '',
    data.published_label || '',
    data.published_at || '',
    data.url || '',
    data.summary || '',
    data.score || '',
    JSON.stringify(data.tags || []),
    data.message || ''
  ]);

  return ContentService.createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
```

Деплой Apps Script webhook:
- `Deploy` → `New deployment` → `Web app`
- `Execute as`: `Me`
- `Who has access`: `Anyone`

`GOOGLE_SHEETS_WEBHOOK_SECRET` защищает endpoint и должен проверяться в `doPost`.

Ожидаемые колонки листа: `sent_at`, `search_name`, `external_id`, `title`, `price`, `area_m2`, `rooms`, `address`, `published_label`, `published_at`, `url`, `summary`, `score`, `tags`, `message`.

Не коммитьте webhook URL и secret в репозиторий.
