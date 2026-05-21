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

## Runtime-флаги скрейпинга

- `SCRAPE_HEADLESS=true` — рекомендуется для Docker/VPS и используется по умолчанию.
- `SCRAPE_HUMANIZE=false` — опциональная «человечная» прокрутка/паузы; по умолчанию выключена.
- `PROXY_URLS` — опциональный список прокси через запятую.
- `SCORING_ENABLED=false` — опционально отключает LLM scoring, чтобы изолировать browser/proxy smoke от доступности Ollama/модели.

Поддерживаемые схемы прокси: `http://` и `https://`.

Пример:

```bash
SCRAPE_HEADLESS=true
SCRAPE_HUMANIZE=false
PROXY_URLS=http://user:pass@host:port,https://user:pass@host2:port2
```

### Troubleshooting прокси

- Ошибка вида `unsupported proxy scheme` — проверьте, что используется `http` или `https` (не `socks5`).
- Ошибка вида `proxy must include valid port` — проверьте, что в URL указан числовой порт.
- Если `PROXY_URLS` пустой, приложение работает в no-proxy режиме.

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
