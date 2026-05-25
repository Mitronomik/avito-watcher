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

## Deploy steps (Docker Compose)

1. Prepare environment file from template:
   - `cp deploy/env.production.example .env`
   - Fill real secret values outside git.
2. Validate compose config:

```bash
docker compose -f deploy/docker-compose.yml config
```

3. Build and start services:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

## Database migration

Run DB migrations to the latest revision before enabling monitoring:

```bash
alembic upgrade head
```

## Runtime ownership: app vs worker

- `app` (FastAPI/admin) is API/admin only.
- `app` does **not** schedule monitoring loops.
- `worker` is the automatic monitoring process.
- Ensure only worker is responsible for periodic monitoring.

## Manual run-once smoke

Run an explicit one-pass smoke for a known search:

```bash
python3 -m app.cli run-once --search-id <ID>
```

Expected: run completes without crash and processes feed for the selected search.

## Alert channel smoke criteria

Verify alert delivery on enabled channels:

- JSONL: new alert line appears in `JSONL_OUTBOX_PATH`.
- Google Sheets: webhook receives row with listing payload.
- Email: message arrives with listing summary/content.

## Admin checks after deploy

Verify operational status in admin/API:

- Worker lock file exists and is writable (`MONITOR_WORKER_LOCK_PATH`).
- `last_error` is empty or transient/non-recurring.
- Active searches are present and enabled.
- "due now" searches are visible/processable by worker.
- Debug dump count does not grow unexpectedly (unless intentionally enabled).
- Runtime flags match rollout policy (`SCORING_ENABLED`, `LLM_PROVIDER`, enrichment flags, channels).

## AI shadow-mode smoke (optional)

Run lightweight checks without placing AI into critical delivery path:

```bash
LLM_PROVIDER=off python3 -m app.cli run-once --search-id <ID>
LLM_PROVIDER=ollama LLM_BASE_URL=http://localhost:11434 LLM_MODEL=<model> python3 -m app.cli run-once --search-id <ID>
LLM_PROVIDER=openai_compatible LLM_BASE_URL=<base_url> LLM_MODEL=<model> LLM_API_KEY=<key> python3 -m app.cli run-once --search-id <ID>
```

Keep `SCORING_ENABLED=false` for first production rollout.

## Rollback

If smoke checks fail or alert quality regresses:

1. Pause worker or scale it down.
2. Revert to last known-good image/tag.
3. Restore previous `.env` secret set (if changed).
4. Re-run `alembic upgrade head` only if required for rollback target compatibility.
5. Resume worker and re-run run-once smoke.
