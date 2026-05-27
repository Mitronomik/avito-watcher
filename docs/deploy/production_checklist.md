# Production deploy checklist

## First deploy stance

For the first production rollout, keep the watcher core-path simple and stable:

- Core watcher first (search feed parsing, dedup, filters, alert delivery).
- AI disabled in critical alert path:
  - `SCORING_ENABLED=false`
  - `LLM_PROVIDER=off`
  - `LLM_SHADOW_MODE=true`
- Item-page details enrichment off by default:
  - `SCRAPE_ENRICH_ITEM_PAGE_DETAILS=false`
- Keep JSONL enabled as an audit trail.

## Pre-merge local checks

Run before merge:

```bash
python3 -m compileall app
ruff check app tests
pytest -q
```

## Secrets and security checklist

- Use only placeholder values in tracked files.
- Inject real secrets only via deployment secret storage.
- Rotate all production secrets before go-live and document rotation date:
  - DB password
  - Redis auth (if enabled)
  - `API_KEY`
  - SMTP credentials
  - Google Sheets webhook secret
  - Telegram bot token/chat target (if used)
  - LLM API keys (if used)
- Never log or commit secrets/tokens/passwords.

## Compose safety gate (required before deploy)

Use `deploy/docker-compose.prod.yml` as the production deployment path.

`deploy/docker-compose.yml` remains a dev/pre-prod baseline and should not be treated as hardened production defaults.

Run:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config
```

Verify resolved values for app/worker match intended production values:

- `DATABASE_URL`
- `API_KEY`
- `PROXY_URLS`
- `ALERT_CHANNELS`
- Google Sheets (`GOOGLE_SHEETS_WEBHOOK_*`) and email (`SMTP_*`, `EMAIL_*`) vars
- LLM vars (`SCORING_ENABLED`, `LLM_PROVIDER`, `LLM_*`)

Do **not** proceed if `postgres:postgres`, placeholder secrets, or unexpected defaults remain.

Interpolation note:

- First copy `deploy/env.production.example` to root `.env` and fill real production values there.
- Docker Compose interpolation for `${POSTGRES_*}` in `deploy/docker-compose.prod.yml` must use `--env-file .env`.
- Service-level `env_file: ../.env` is a separate mechanism that passes env values into containers at runtime.
- Do not rely on `deploy/env.production.example` directly for final production render.

Optional local template dry-run:

```bash
cp .env .env.local.backup
cp deploy/env.production.example .env
docker compose --env-file .env -f deploy/docker-compose.prod.yml config
mv .env.local.backup .env
```

## Deploy steps (Docker Compose)

1. Prepare environment file from template:
   - `cp deploy/env.production.example .env`
   - Fill real secret values outside git.
2. Prepare mounted data directories used by containers:

```bash
mkdir -p data/debug_html
```

Root `./data` is mounted to `/app/data` (via `../data:/app/data` in `deploy/docker-compose.prod.yml`) and is used for JSONL alerts, debug HTML dumps, and worker lock files. Create it before the first compose up.

3. Pass compose safety gate above.
4. Build/start infra + API without worker auto-monitoring:

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build postgres redis app
```


## Database migration

Run DB migrations to the latest revision before enabling worker monitoring:

```bash
docker compose -f deploy/docker-compose.prod.yml run --rm app alembic upgrade head
```

Local/dev alternative:

```bash
alembic upgrade head
```

## Runtime ownership: app vs worker

- `app` (FastAPI/admin) is API/admin only.
- `app` does **not** schedule monitoring loops.
- `worker` is the automatic monitoring process.
- Ensure only worker is responsible for periodic monitoring.

## App health/admin check before worker

Confirm API/admin is reachable and healthy before enabling worker.

## Manual run-once smoke

Run an explicit one-pass smoke for a known search.

Primary (Docker Compose):

```bash
docker compose -f deploy/docker-compose.prod.yml run --rm app python3 -m app.cli run-once --search-id <ID>
```

Local/dev alternative:

```bash
python3 -m app.cli run-once --search-id <ID>
```

Expected: run completes without crash and processes feed for the selected search.

After smoke passes, start worker:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Worker operations:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile worker stop worker
docker compose -f deploy/docker-compose.prod.yml --profile worker restart worker
docker compose -f deploy/docker-compose.prod.yml --profile worker logs -f worker --tail=200
```

## Worker lifecycle

- Worker is a long-running process.
- It continuously picks active/due searches.
- Newly activated searches are picked up on the next cycle.
- Worker process lifecycle is controlled by Docker Compose/systemd, not by admin UI.
- Admin UI can observe worker status and run manual `run-once`, but it does not start/stop worker.

## Alert channel smoke criteria

Verify alert delivery on enabled channels:

- JSONL: new alert line appears in `JSONL_OUTBOX_PATH`.
- Google Sheets: webhook receives row with listing payload (if enabled).
- Email: message arrives with listing summary/content (if enabled).

## Admin checks after deploy

Verify operational status in admin/API:

- Worker lock file exists and is writable (`MONITOR_WORKER_LOCK_PATH`).
- `last_error` is empty or transient/non-recurring.
- Active searches are present and enabled.
- "due now" searches are visible/processable by worker.
- Debug dump count does not grow unexpectedly (unless intentionally enabled).
- Runtime flags match rollout policy (`SCORING_ENABLED`, `LLM_PROVIDER`, enrichment flags, channels).

## AI shadow-mode smoke (optional, controlled one-off)

First-production worker default remains:

- `SCORING_ENABLED=false`
- `LLM_PROVIDER=off`
- `LLM_SHADOW_MODE=true`

Optional shadow smoke commands below are one-off checks to exercise LLM path and are **not** first-prod worker defaults.

DeepSeek should be enabled only after core smoke passes, through existing `openai_compatible` provider:

- `LLM_PROVIDER=openai_compatible`
- `LLM_BASE_URL=https://api.deepseek.com`
- `LLM_MODEL=deepseek-v4-pro`
- Keep `LLM_SHADOW_MODE=true` first.
- LLM path must stay fail-soft and must not block alert delivery.

Docker Compose DeepSeek shadow smoke:

```bash
docker compose -f deploy/docker-compose.prod.yml run --rm   -e SCORING_ENABLED=true   -e LLM_SHADOW_MODE=true   -e LLM_PROVIDER=openai_compatible   -e LLM_BASE_URL=https://api.deepseek.com   -e LLM_MODEL=deepseek-v4-pro   -e LLM_API_KEY=<deepseek-api-key>   app python3 -m app.cli run-once --search-id <ID>
```

Optional local Ollama (not default production path):

```bash
docker compose -f deploy/docker-compose.prod.yml --profile llm-local up -d ollama
docker compose -f deploy/docker-compose.prod.yml run --rm -e SCORING_ENABLED=true -e LLM_SHADOW_MODE=true -e LLM_PROVIDER=ollama -e LLM_BASE_URL=http://ollama:11434 -e LLM_MODEL=<model> app python3 -m app.cli run-once --search-id <ID>
```

## Rollback

If smoke checks fail or alert quality regresses:

1. Pause worker:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile worker stop worker
```

2. Revert to last known-good image/tag.
3. Restore previous `.env` secret set (if changed).
4. Re-run migrations only as required for rollback target compatibility.
5. Resume worker and re-run run-once smoke:

```bash
docker compose -f deploy/docker-compose.prod.yml --profile worker up -d worker
```
