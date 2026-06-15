# PR21c — Monitor cycle run ledger production smoke

Date: 2026-06-15

Status: **PASSED**

This handoff records the production deploy and smoke test for PR21c:

```text
PR21c — Monitor cycle run ledger
PR #199 — Add monitor cycle run ledger
Merge commit: e54058668508a7601f2aec73cb65ac078ce82595
```

## Purpose

PR21c added a durable, read-only operational ledger for top-level monitor worker cycles.

The goal is operational observability only:

```text
record one row per top-level monitor cycle;
show recent monitor cycle history on /admin/system;
preserve NULL as unknown/not captured;
keep monitor/parser/scoring/delivery/retry behavior unchanged.
```

## Scope confirmed

Confirmed in production smoke:

```text
Code deployed: e540586
Docker images rebuilt: app + worker
Alembic migration applied: 0015_alert_delivery_attempts -> 0016_monitor_cycle_runs
/admin/system returns 200
monitor_cycle_runs table exists
worker creates one top-level cycle ledger row
running row finishes correctly
nullable metrics remain NULL when unknown
worker_status_file stores basename only
```

## Production deploy summary

Repository was fast-forwarded on production:

```bash
cd ~/apps/avito-watcher
git pull --ff-only origin main
git log -1 --oneline
```

Observed:

```text
e540586 (HEAD -> main, origin/main, origin/HEAD) Add monitor cycle run ledger (#199)
```

Docker images were rebuilt after the pull:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker
```

Observed:

```text
Image deploy-app    Built
Image deploy-worker Built
```

## Backup

A pre-migration backup was created before applying `0016`:

```text
backups/pr21c_pre_monitor_cycle_runs_20260615T154117Z.dump
size: 2.3M
```

## Alembic migration

Before rebuilding the image, Alembic still saw the old head from the previously built container image:

```text
heads/current: 0015_alert_delivery_attempts
```

After rebuilding `app` and `worker`, Alembic saw the new migration:

```text
alembic heads: 0016_monitor_cycle_runs (head)
alembic current before upgrade: 0015_alert_delivery_attempts
```

Migration was applied:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic upgrade head
```

Observed:

```text
Running upgrade 0015_alert_delivery_attempts -> 0016_monitor_cycle_runs, add monitor cycle runs ledger
```

After upgrade:

```text
alembic current: 0016_monitor_cycle_runs (head)
```

## App and worker restart

Services were restarted:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Observed:

```text
Container deploy-postgres-1 Healthy
Container deploy-redis-1    Healthy
Container deploy-worker-1   Started
Container deploy-app-1      Started
```

Initial `/health` checks returned transient startup errors:

```text
curl: (56) Recv failure: Connection reset by peer
curl: (52) Empty reply from server
```

This matched previous post-restart startup windows and was not treated as a blocker because the admin endpoint later returned 200.

## DB schema smoke

Checked table existence and nullable columns:

```sql
select count(*) as monitor_cycle_runs from monitor_cycle_runs;

select column_name, is_nullable
from information_schema.columns
where table_name = 'monitor_cycle_runs'
order by ordinal_position;
```

Observed initially:

```text
monitor_cycle_runs: 1
```

Important nullable metric fields:

```text
listings_seen: YES
listings_created: YES
listings_updated: YES
alert_delivery_attempts_created: YES
alerts_sent_created: YES
alert_delivery_failed: YES
alert_delivery_unknown: YES
```

This confirms the intended semantics:

```text
NULL = unknown / not captured
0 = measured and zero
```

## Admin UI smoke

`/admin/system` returned 200:

```bash
curl -sS -o /tmp/pr21c_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Observed:

```text
200
```

The new section rendered:

```text
История циклов мониторинга / Monitor cycle history
```

Initial summary after the first row:

```text
last 24h cycles total: 1
success: 1
partial: 0
failed: 0
skipped: 0
stale running count: 0
```

After the next real worker cycle completed:

```text
last 24h cycles total: 2
success: 2
partial: 0
failed: 0
skipped: 0
latest failed cycle: —
stale running count: 0
```

## Read-only and redaction checks

Checked that `/admin/system` remained read-only and did not expose obvious secret/path patterns:

```bash
grep -F "<form" /tmp/pr21c_system.html && echo "CHECK forms present" || echo "OK no forms in system page"
grep -F "Bearer " /tmp/pr21c_system.html && echo "LEAK bearer" || echo "OK no bearer token"
grep -F "api_key=" /tmp/pr21c_system.html && echo "CHECK api_key rendered" || echo "OK no api_key query leak"
grep -F "/app/data/worker_status.json" /tmp/pr21c_system.html && echo "LEAK absolute worker path" || echo "OK no absolute worker path"
```

Observed:

```text
OK no forms in system page
OK no bearer token
OK no api_key query leak
OK no absolute worker path
```

## Worker cycle ledger rows

First row after restart:

```text
id: 1
status: success
started_at: 2026-06-15 15:43:36.322389
finished_at: 2026-06-15 15:43:36.383092
duration_ms: 60
searches_total: 0
searches_processed: 0
searches_failed: 0
worker_status_file: worker_status.json
```

Worker logs for the first row showed an empty cycle:

```text
monitor_service.cycle_summary searches_processed=0
monitor cycle completed
```

A second row was observed while running:

```text
id: 2
status: running
started_at: 2026-06-15 15:44:38.134865
finished_at: NULL
```

After the real cycle completed, the same row was updated:

```text
id: 2
status: success
started_at: 2026-06-15 15:44:38.134865
finished_at: 2026-06-15 15:46:20.463264
duration_ms: 102328
searches_total: 1
searches_processed: 1
searches_failed: 0
listings_created: 3
alert_delivery_attempts_created: NULL
alerts_sent_created: NULL
worker_status_file: worker_status.json
```

This confirms:

```text
one row per top-level cycle;
running row is updated on finish;
result-derived metrics are recorded;
unavailable alert delivery deltas remain NULL;
worker_status_file is basename-only.
```

## Worker log smoke

The second cycle showed real work:

```text
scorer provider=openai_compatible model=deepseek-v4-flash status=success
avito_parser.end_cycle engine_used=camoufox session_open_count=1 session_reuse_count=4
monitor_service.cycle_summary searches_processed=1
monitor cycle completed
```

The worker also logged successful Google Sheets delivery redirects and final 200 responses.

Important note: runtime worker logs still include raw external delivery URLs. This is **not a PR21c regression** and does not affect `/admin/system` rendering, but it should be handled in a future hardening PR.

Do not copy raw Apps Script deployment IDs or `googleusercontent` query tokens into docs.

## Alert delivery integrity remained healthy

`/admin/system` still rendered the normalized PR21b delivery integrity groups:

```text
Delivery integrity issues (all time):
  success_without_alert_sent: 0
  success_missing_sent_at: 0
  non_success_with_sent_at: 0
  bad_payload_hash_count: 0
  non_success_after_alert_sent: 0

Resolved delivery history (all time):
  resolved_non_success_with_later_alert_sent: 3

Retry scheduling indicators (all time):
  next_retry_at_non_null: 0
```

This confirms that PR21c did not regress PR21b semantics.

## Data volume observed during smoke

After the successful real cycle:

```text
listings: 1725
listing_analyses: 730
alert_delivery_attempts: 357
alerts_sent: 3270
agent_tasks: 2
search_jobs: 2
monitor_cycle_runs: 2
```

## Final verdict

```text
PR21c production smoke: PASSED ✅
monitor_cycle_runs migration applied ✅
worker writes one top-level cycle ledger row ✅
running row finishes correctly ✅
/admin/system monitor cycle history works ✅
ledger metrics preserve NULL as unknown ✅
worker_status_file is basename-only ✅
read-only and basic redaction checks passed ✅
```

## Follow-up recommendation

A future hardening PR should redact external delivery URLs in runtime logs:

```text
Future PR — Redact external delivery URLs in worker/runtime logs
```

Rationale:

```text
Admin UI redaction is working, but runtime logs can still contain raw Apps Script deployment URLs and googleusercontent query tokens.
```
