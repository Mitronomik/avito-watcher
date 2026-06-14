# PR19a - Admin UI safety shell production smoke

Date: 2026-06-14
Environment: production (`avito-watcher-prod`)
Repository: `Mitronomik/avito-watcher`
Merged PR: #179 - `Add admin UI safety shell`
Merge commit: `5027f01bbc08a5c37c9d6907c04ac83e6ca6b08e`

## Status

```text
PR19a deploy: OK
PR19a admin access smoke: OK
PR19a technical write guards: OK
PR19a worker smoke: OK
PR19a final post-GET SQL no-side-effects check: CLOSED
```

This handoff records the production evidence captured during the PR19a deployment and smoke. The final read-only GET smoke was repeated with the worker stopped, and post-GET SQL counts matched the baseline captured immediately before that clean check.

## Scope

PR19a added the first safety shell for the existing admin UI:

```text
/admin operator dashboard
/admin/technical landing page
admin navigation/layout
Russian default UI labels
redaction/truncation helpers
ADMIN_UI_* safety settings
query-string api_key disabled by default
technical write endpoints blocked by default
```

PR19a deliberately did not add:

```text
human review workflow
listing detail workflow
evidence/agents/outcome analytics pages
technical operations redesign
alert retry
score/verdict mutation
monitor behavior changes
LLM calls
external calls
DB migrations
```

## Code and test status before merge

Amended PR test evidence reported before merge:

```text
python3 -m compileall app: OK
python3 -m ruff check app tests: OK
pytest -q tests/test_admin_ui.py: 67 passed
pytest -q: 830 passed, 1 skipped
git diff --check: OK
alembic heads: 0014_human_review_tracking (head)
```

## Deployment commands and results

Images were rebuilt successfully:

```text
Image deploy-app: Built
Image deploy-worker: Built
```

Alembic head/current remained unchanged:

```text
0014_human_review_tracking (head)
```

App and worker started:

```text
deploy-app-1: Started
deploy-worker-1: Started
```

Health check:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```http
HTTP/1.1 200 OK
{"status":"ok"}
```

## Admin UI disabled / protected access check

Initial access without admin key:

```bash
curl -i http://127.0.0.1:8010/admin
```

Result:

```http
HTTP/1.1 403 Forbidden
```

This confirms the admin UI was protected and not accessible anonymously.

## Admin UI smoke settings

For smoke, the intended safe settings were:

```env
ADMIN_UI_ENABLED=true
ADMIN_UI_ALLOW_QUERY_API_KEY=false
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
ADMIN_UI_LANGUAGE=ru
```

A read key was configured through `ADMIN_UI_READ_KEY` or fallback `API_KEY`.

The key value is intentionally not recorded in this document.

## Operator dashboard smoke

Header-key access to `/admin` succeeded:

```bash
curl -i -H "X-API-Key: $ADMIN_KEY" http://127.0.0.1:8010/admin
```

Result:

```http
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8
```

The page rendered the operator dashboard:

```text
Панель оператора
Операторская панель показывает состояние системы без изменения данных.
Сегодня
Новые объекты: 1520
Интересные объекты: 730
```

The page also rendered the new navigation:

```text
Панель
Объекты
Поиски
Уведомления
Анализы
Технический режим
```

## Query-string key rejection

Query-string `api_key` access was rejected as intended:

```bash
curl -i "http://127.0.0.1:8010/admin?api_key=$ADMIN_KEY"
```

Result:

```http
HTTP/1.1 403 Forbidden
{"detail":"Invalid admin key"}
```

This confirms `ADMIN_UI_ALLOW_QUERY_API_KEY=false` behavior.

## Technical operations guard

Technical write endpoint `run-once` was blocked:

```bash
curl -i -X POST -H "X-API-Key: $ADMIN_KEY" \
  http://127.0.0.1:8010/admin/searches/1/run-once
```

Result:

```http
HTTP/1.1 403 Forbidden
{"detail":"Technical operations are disabled"}
```

Technical write endpoint `reset-baseline` was blocked:

```bash
curl -i -X POST -H "X-API-Key: $ADMIN_KEY" \
  http://127.0.0.1:8010/admin/searches/1/reset-baseline
```

Result:

```http
HTTP/1.1 403 Forbidden
{"detail":"Technical operations are disabled"}
```

Earlier smoke also showed the same expected `403` behavior for:

```text
/admin/searches/1/activate
/admin/searches/1/deactivate
```

## GET page smoke

The admin key was loaded from `.env` without sourcing the full file, because `.env` is valid for Docker Compose but not necessarily valid as a Bash script.

Successful GET page smoke:

```text
admin=200
searches=200
alerts=200
listings=200
analyses=200
technical=200
```

Pages checked:

```text
/admin
/admin/searches
/admin/alerts
/admin/listings
/admin/listing-analyses
/admin/technical
```

## Initial baseline SQL counts captured before successful GET smoke

Baseline counts captured during smoke:

```text
listings = 1520
listing_analyses = 730
alerts_sent = 2860
search_jobs = 2
agent_tasks = 2
human_reviews = 0
human_review_actions = 0
investment_decisions = 0
```

SQL used:

```sql
select
  (select count(*) from listings) as listings,
  (select count(*) from listing_analyses) as listing_analyses,
  (select count(*) from alerts_sent) as alerts_sent,
  (select count(*) from search_jobs) as search_jobs,
  (select count(*) from agent_tasks) as agent_tasks,
  (select count(*) from human_reviews) as human_reviews,
  (select count(*) from human_review_actions) as human_review_actions,
  (select count(*) from investment_decisions) as investment_decisions;
```

## Final no-side-effects evidence

The first no-side-effects check was inconclusive because the worker was still running during that smoke. The temporary count changes below were caused by normal worker activity, not by admin GET pages:

```text
listings 1520 -> 1521
alerts_sent 2860 -> 2862
```

The final clean read-only GET smoke was repeated with the worker stopped for the baseline and GET-page checks:

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
```

Baseline counts before the final GET smoke:

```text
listings = 1521
listing_analyses = 730
alerts_sent = 2862
search_jobs = 2
agent_tasks = 2
human_reviews = 0
human_review_actions = 0
investment_decisions = 0
```

GET smoke with valid `X-API-Key`:

```text
admin=200
searches=200
alerts=200
listings=200
analyses=200
technical=200
```

Post-GET SQL counts:

```text
listings = 1521
listing_analyses = 730
alerts_sent = 2862
search_jobs = 2
agent_tasks = 2
human_reviews = 0
human_review_actions = 0
investment_decisions = 0
```

Final verdict:

```text
GET admin pages are read-only: OK
No DB side effects from PR19a GET admin pages.
PR19a production smoke: CLOSED.
```

Worker was restarted after the final GET-page smoke. A post-GET SQL count was then captured and remained identical to the clean baseline.

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

## Worker logs

Worker logs were clean from an application-error perspective:

```text
monitor cycle completed
no Traceback
no OperationalError
no unexpected agent execution
```

Observed warning:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning is pre-existing environment/runtime behavior and not introduced by PR19a.

Observed monitor activity:

```text
searches_processed=0 during several cycles
later searches_processed=1
monitor cycle completed
```

No admin-triggered `run-once` was executed because technical operations were disabled and returned `403`.

## Verdict

Current evidence supports:

```text
PR19a deploy: CLOSED
Admin UI protected access: CLOSED
Query-string key disabled: CLOSED
Technical write guards: CLOSED
GET page availability: CLOSED
Worker log smoke: CLOSED
Final post-GET SQL no-side-effects check: CLOSED
PR19a production smoke: CLOSED
```

## Next step

With the post-GET SQL no-side-effects evidence attached and this handoff finalized, proceed to:

```text
PR19b - Listing detail and human review workflow
```

PR19b should add the user-facing listing detail workflow and safe human decision recording through the existing `HumanReviewService`, without opening broader technical operations or mutating deterministic score/verdict.
