# Production deploy checklist

## First deploy stance

For the first production rollout, keep the watcher core-path simple and stable:

- Core watcher first (search feed parsing, dedup, filters, alert delivery).
- AI disabled in critical alert path:
  - `SCORING_ENABLED=false`
  - `LLM_PROVIDER=off`
  - `LLM_SHADOW_MODE=true`
  - `LLM_REVIEW_COPILOT_ENABLED=false`
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
- LLM vars (`SCORING_ENABLED`, `LLM_PROVIDER`, `LLM_*`, including `LLM_REVIEW_COPILOT_ENABLED=false` unless explicitly testing AgentTask shadow review)

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

1. Prepare the environment file and mounted data directories:
   - `cp deploy/env.production.example .env`
   - Fill real secret values outside git.

```bash
mkdir -p data/debug_html
```

Root `./data` is mounted to `/app/data` (via `../data:/app/data` in `deploy/docker-compose.prod.yml`) and is used for JSONL alerts, debug HTML dumps, and worker lock files. Create it before the first compose up.

2. Pass compose safety gate above.
3. Build the app image explicitly before migrations; do not rely on an implicit image build from `run` or `up`:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
```

4. Start only infrastructure; do not start the API or worker before migrations on a clean database:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d postgres redis
```


## Database migration

5. Run DB migrations to the latest revision before starting the API or enabling worker monitoring:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm app alembic upgrade head
```

6. Start API after migrations, without worker auto-monitoring:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

Local/dev alternative:

```bash
alembic upgrade head
```

## Backup schedule and retention

Before enabling production monitoring, configure and verify the backup schedule.
See `docs/deploy/backup_restore.md` for the daily cron example, systemd timer
alternative, latest-backup verification, and safe retention prune commands.

## Runtime ownership: app vs worker

- `app` (FastAPI/admin) is API/admin only.
- `app` does **not** schedule monitoring loops.
- `worker` is the automatic monitoring process.
- Ensure only worker is responsible for periodic monitoring.

## App health/admin check before worker

Confirm API/admin is reachable and healthy before enabling worker.

## Docker Xvfb/Camoufox smoke checks

When Docker is available, confirm the production app image includes Xvfb support before run-once smoke:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm app python3 -c "import shutil; print('Xvfb=', shutil.which('Xvfb')); print('xvfb-run=', shutil.which('xvfb-run')); print('xauth=', shutil.which('xauth')); assert shutil.which('Xvfb'); assert shutil.which('xvfb-run'); assert shutil.which('xauth')"
```

For a direct image smoke after building a release candidate locally:

```bash
docker build -t avito-watcher:camoufox-runtime-smoke .
docker run --rm avito-watcher:camoufox-runtime-smoke python3 -c "import shutil; print('Xvfb=', shutil.which('Xvfb')); print('xvfb-run=', shutil.which('xvfb-run')); print('xauth=', shutil.which('xauth')); assert shutil.which('Xvfb'); assert shutil.which('xvfb-run'); assert shutil.which('xauth')"
```

Then verify Camoufox can start with a virtual headless display:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm app python3 - <<'PY'
import asyncio
from camoufox.async_api import AsyncCamoufox

async def main():
    async with AsyncCamoufox(headless="virtual") as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        print("camoufox_virtual_ok")

asyncio.run(main())
PY
```

Direct image equivalent:

```bash
docker run --rm avito-watcher:camoufox-runtime-smoke python3 - <<'PY'
import asyncio
from camoufox.async_api import AsyncCamoufox

async def main():
    async with AsyncCamoufox(headless="virtual") as browser:
        page = await browser.new_page()
        await page.goto("about:blank")
        print("camoufox_virtual_ok")

asyncio.run(main())
PY
```

## Optional nodriver + Chromium canary smoke

These commands are for an explicit canary contour only. They must be run with
command-level environment overrides and must not change the production `.env`
defaults: keep `SCRAPE_PREFERRED_ENGINE=camoufox`,
`SCRAPE_ALLOWED_ENGINES=camoufox`, and proxy settings as originally intended.

Verify the Chromium executable exists in the production container:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm app \
  sh -lc 'which chromium || which chromium-browser || which google-chrome || true'
```

Run a nodriver dry-run without proxy by overriding the engine and executable path
for this command only:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e SCRAPE_PREFERRED_ENGINE=nodriver \
  -e SCRAPE_ALLOWED_ENGINES=nodriver \
  -e SCRAPE_NODRIVER_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
  -e PROXY_URLS= \
  app python3 -m app.cli dry-run-search --url "<test_url>"
```

Run a nodriver dry-run with a one-off proxy override without editing `.env`:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e SCRAPE_PREFERRED_ENGINE=nodriver \
  -e SCRAPE_ALLOWED_ENGINES=nodriver \
  -e SCRAPE_NODRIVER_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
  -e PROXY_URLS="http://<user>:<pass>@<host>:<port>" \
  app python3 -m app.cli dry-run-search --url "<test_url>"
```

Run a one-pass nodriver canary for a known search by overriding the engine and
executable path for this command only:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e SCRAPE_PREFERRED_ENGINE=nodriver \
  -e SCRAPE_ALLOWED_ENGINES=nodriver \
  -e SCRAPE_NODRIVER_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
  -e PROXY_URLS= \
  app python3 -m app.cli run-once --search-id <ID>
```


## Manual run-once smoke

7. Run an explicit one-pass smoke for a known search.

Primary (Docker Compose):

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm app python3 -m app.cli run-once --search-id <ID>
```

Proxy-backed production smoke when `PROXY_URLS` is configured in `.env`:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm app python3 -m app.cli run-once --search-id <ID>
```

Direct image proxy smoke when using the local release-candidate tag:

```bash
docker run --rm --env-file .env avito-watcher:camoufox-runtime-smoke python3 -m app.cli run-once --search-id <ID>
```

Local/dev alternative:

```bash
python3 -m app.cli run-once --search-id <ID>
```

Expected: run completes without crash and processes feed for the selected search.

8. After smoke passes, start worker:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Worker operations:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker restart worker
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker logs -f worker --tail=200
```

## Worker lifecycle

- Worker is a long-running process.
- It continuously picks active/due searches.
- Newly activated searches are picked up on the next cycle.
- Worker process lifecycle is controlled by Docker Compose/systemd, not by admin UI.
- Admin UI can observe worker status and run manual `run-once`, but it does not start/stop worker.
- Worker liveness is currently checked through worker logs, admin lock/runtime status, and monitor cycle summaries. The production worker does not expose an HTTP endpoint, so Docker HTTP health is disabled for the worker service.

## Alert channel smoke criteria

Verify alert delivery on enabled channels:

- JSONL: new alert line appears in `JSONL_OUTBOX_PATH`.
- Google Sheets: webhook receives row with listing payload (if enabled). For the production Google Sheets + LLM summary contour, use the [Google Sheets alerts with LLM summary runbook](google_sheets_llm_runbook.md).
- Email: message arrives with listing summary/content (if enabled). Keep email dormant unless a separate rollout explicitly enables it.

Useful SQL smoke checks after deploy:

```sql
select channel,
       count(*) as sent_count,
       min(id) as first_id,
       max(id) as last_id,
       min(created_at) as first_created_at,
       max(created_at) as last_created_at
from alerts_sent
group by channel
order by channel;
```

```sql
select channel,
       count(*) as alerts_without_current_match
from alerts_sent a
where not exists (
  select 1
  from listing_search_matches m
  where m.listing_external_id = a.listing_external_id
)
group by channel
order by channel;
```

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
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm   -e SCORING_ENABLED=true   -e LLM_SHADOW_MODE=true   -e LLM_PROVIDER=openai_compatible   -e LLM_BASE_URL=https://api.deepseek.com   -e LLM_MODEL=deepseek-v4-pro   -e LLM_API_KEY=<deepseek-api-key>   app python3 -m app.cli run-once --search-id <ID>
```

Optional local Ollama (not default production path):

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile llm-local up -d ollama
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm -e SCORING_ENABLED=true -e LLM_SHADOW_MODE=true -e LLM_PROVIDER=ollama -e LLM_BASE_URL=http://ollama:11434 -e LLM_MODEL=<model> app python3 -m app.cli run-once --search-id <ID>
```

## Backups and restore

Before risky operations and on a regular production cadence, create operational backups. See [Production backup and restore](backup_restore.md) for exact backup, verification, restore, and rehearsal commands. Database restore is destructive and requires `CONFIRM_RESTORE=yes`.

## Rollback

If smoke checks fail or alert quality regresses:

1. Pause worker:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
```

2. Revert to last known-good image/tag.
3. Restore previous `.env` secret set (if changed).
4. Re-run migrations only as required for rollback target compatibility.
5. Resume worker and re-run run-once smoke:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

## Worker status observability

The worker writes a lightweight JSON status file after each monitor cycle. By
default the file is:

```bash
./data/worker_status.json
```

The admin UI shows the latest status in the existing Worker status block at
`/admin/searches`, including stale/fresh state, last cycle success/failure, and
parser health counters such as browser driver crash retries and session reuse.
The default stale threshold is 180 seconds and can be overridden with:

```bash
MONITOR_WORKER_STALE_AFTER_SECONDS=180
```

Inspect the raw file manually on the host with:

```bash
cat data/worker_status.json
```

This status file is observability only. It does not start, stop, restart, or
change the worker cadence.
