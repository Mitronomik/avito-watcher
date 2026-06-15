# PR20a — Alert delivery attempts ledger production smoke

Date: 2026-06-15

Status: **production deploy / migration smoke closed** ✅

Live delivery-attempt positive observation: **pending natural alert cycle** ⚠️

This handoff documents the safe production deploy and smoke for PR20a.

PR20a added a durable alert delivery attempt ledger:

```text
alert_delivery_attempts
```

The PR intentionally did **not** add:

```text
retry dashboard
manual retry
automatic retry scheduler
admin retry actions
alert suppression
agent/research side effects
```

The main invariant remains:

```text
alerts_sent is the success-only delivery dedupe table.
alert_delivery_attempts is the append-only observable attempt ledger.
failed/skipped/unknown attempts must not create AlertSent.
```

---

## Production revision

Before deploy, production was still on PR19d:

```text
b44346f Harden admin technical operations (#186)
```

Production was updated to PR20a:

```text
4a9d1b5 Add alert delivery attempts ledger (#188)
```

Git state after pull:

```text
branch: main
working tree: clean
origin/main: 4a9d1b5
```

PR20a changed 11 files in production after pull, including:

```text
alembic/versions/0015_alert_delivery_attempts.py
app/models/alert_delivery_attempt.py
app/repositories/alert_delivery_attempt_repository.py
app/services/alert_delivery_attempts.py
app/services/monitor_service.py
docs/alert_delivery.md
tests/test_alert_delivery_attempts.py
```

---

## Backup

A PostgreSQL custom-format backup was created before the migration:

```text
backups/pr20a_pre_alert_delivery_attempts_20260615T050301Z.dump
size: 2.1M
```

Worker was stopped before migration:

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
```

Result:

```text
Container deploy-worker-1 Stopped
```

---

## Build

Compose config was validated:

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null
```

Postgres and Redis were already running.

New images were built successfully:

```text
deploy-app: built
deploy-worker: built
```

---

## Alembic migration on real PostgreSQL

Before upgrade:

```text
alembic heads   -> 0015_alert_delivery_attempts (head)
alembic current -> 0014_human_review_tracking
```

Upgrade command executed successfully:

```text
alembic upgrade head
```

Observed migration:

```text
Running upgrade 0014_human_review_tracking -> 0015_alert_delivery_attempts, add alert delivery attempts
```

After upgrade:

```text
alembic current -> 0015_alert_delivery_attempts (head)
```

This closes the PR20a PostgreSQL migration risk that could not be verified in the Codex container because no local PostgreSQL service was available there.

---

## New table verification

Table exists:

```text
alert_delivery_attempts
```

Columns verified in production:

```text
id
listing_external_id
channel
dedupe_key
payload_hash
status
attempt_count
last_error
next_retry_at
sent_at
search_job_id
search_name
error_type
created_at
updated_at
```

Indexes verified in production:

```text
alert_delivery_attempts_pkey
ix_alert_delivery_attempts_channel
ix_alert_delivery_attempts_channel_status
ix_alert_delivery_attempts_created_at
ix_alert_delivery_attempts_dedupe_key
ix_alert_delivery_attempts_dedupe_key_channel
ix_alert_delivery_attempts_listing_external_id
ix_alert_delivery_attempts_payload_hash
ix_alert_delivery_attempts_status
ix_alert_delivery_attempts_status_next_retry_at
```

---

## Counts before app/worker restart

Counts before starting the new app/worker:

```text
agent_tasks              2
alert_delivery_attempts  0
alerts_sent              2916
human_review_actions     0
human_reviews            0
investment_decisions     0
knowledge_notes          0
listing_analyses         730
listing_detail_snapshots 0
listing_enrichments      0
listings                 1548
market_evidence_items    0
market_research_runs     0
search_jobs              2
```

---

## App and worker restart

App was restarted:

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

Health check passed:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Worker was restarted:

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Final container status:

```text
deploy-app-1        Up / healthy
deploy-postgres-1   Up / healthy
deploy-redis-1      Up / healthy
deploy-worker-1     Up
```

---

## Worker observation

Worker runtime diagnostics showed the existing production delivery channels:

```text
alert_channels=['jsonl', 'google_sheets']
```

Known runtime warning:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning is not a PR20a regression.

Two worker cycles were observed after restart:

```text
searches_processed=0
engine_used=None
fallback_used=False
blocks=0
engine_errors=0
browser_driver_crashes=0
proxy_failures=0
```

Because `searches_processed=0`, no new delivery was attempted during the smoke window.

---

## Counts after smoke

Counts after worker observation:

```text
agent_tasks              2
alert_delivery_attempts  0
alerts_sent              2916
human_review_actions     0
human_reviews            0
investment_decisions     0
knowledge_notes          0
listing_analyses         730
listing_detail_snapshots 0
listing_enrichments      0
listings                 1548
market_evidence_items    0
market_research_runs     0
search_jobs              2
```

Diff before/after:

```text
no row-count changes
```

Interpretation:

```text
No delivery spam.
No accidental listing/analysis/agent/research/human-review side effects.
No alert_delivery_attempt rows yet because no delivery was attempted.
```

---

## Ledger invariant checks

Because `alert_delivery_attempts` was empty, the invariant checks were vacuously true.

Status/channel aggregation:

```text
0 rows
```

Failed/skipped/unknown matching `AlertSent`:

```text
0 rows
```

Success attempts without `AlertSent`:

```text
success_without_alert_sent = 0
```

Timestamp rules:

```text
success_missing_sent_at  = 0
non_success_with_sent_at = 0
non_null_next_retry_at   = 0
```

Payload hash shape:

```text
bad_payload_hash_count = 0
```

Sanitized error scan:

```text
0 matching rows
```

Important note:

```text
These SQL checks confirm schema/query compatibility on production PostgreSQL.
They do not yet prove live success/failed/skipped/unknown attempt recording in production because no delivery attempt occurred during smoke.
```

---

## Logs

No application tracebacks or critical errors were observed.

No worker tracebacks or delivery exceptions were observed.

Observed log lines included runtime diagnostics and normal monitor cycle summaries.

Known non-blocking diagnostics:

```text
PROXY_URLS not set — running without proxies
llm_api_key_set / equivalent boolean runtime flag present in diagnostics
```

These are not secret leaks: the worker logs boolean `*_set` flags, not raw secret values.

---

## Smoke result

Closed:

```text
backup created ✅
worker stopped before migration ✅
main updated to 4a9d1b5 ✅
app/worker images built ✅
alembic upgrade head succeeded on PostgreSQL ✅
alembic current = 0015_alert_delivery_attempts ✅
alert_delivery_attempts table exists ✅
expected columns exist ✅
expected indexes exist ✅
app health = 200 ✅
worker starts ✅
DB counts unchanged ✅
no delivery spam ✅
no agent/research/human-review side effects ✅
logs without tracebacks ✅
```

Pending:

```text
observe first natural delivery attempt after PR20a ⚠️
```

Final status:

```text
PR20a production deploy: done ✅
PR20a PostgreSQL migration smoke: closed ✅
PR20a app/worker health smoke: closed ✅
PR20a no-side-effects smoke: closed ✅
PR20a live delivery-attempt observation: pending natural alert cycle ⚠️
```

---

## Follow-up observation commands

Run later after a natural worker cycle that actually processes a search and attempts delivery.

Status aggregation:

```bash
cd /home/deploy/apps/avito-watcher

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select status, channel, count(*) as cnt
from alert_delivery_attempts
group by status, channel
order by status, channel;
"'
```

Latest attempts:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id, listing_external_id, channel, status, attempt_count, sent_at, next_retry_at, created_at,
       left(payload_hash, 12) as payload_hash_prefix,
       left(coalesce(last_error, '\''\''), 120) as last_error_preview
from alert_delivery_attempts
order by id desc
limit 20;
"'
```

Success invariant:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select count(*) as success_without_alert_sent
from alert_delivery_attempts a
left join alerts_sent s on s.dedupe_key = a.dedupe_key
where a.status = '\''success'\'' and s.id is null;
"'
```

Expected:

```text
success_without_alert_sent = 0
```

Failed/skipped/unknown invariant:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select a.status, a.channel, count(s.id) as matching_alert_sent
from alert_delivery_attempts a
left join alerts_sent s on s.dedupe_key = a.dedupe_key
where a.status in ('\''failed'\'','\''skipped'\'','\''unknown'\'')
group by a.status, a.channel
order by a.status, a.channel;
"'
```

Expected:

```text
matching_alert_sent = 0 for failed/skipped/unknown
```

Timestamp rules:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select
  count(*) filter (where status = '\''success'\'' and sent_at is null) as success_missing_sent_at,
  count(*) filter (where status <> '\''success'\'' and sent_at is not null) as non_success_with_sent_at,
  count(*) filter (where next_retry_at is not null) as non_null_next_retry_at
from alert_delivery_attempts;
"'
```

Expected:

```text
success_missing_sent_at = 0
non_success_with_sent_at = 0
non_null_next_retry_at = 0
```

---

## Next PR guidance

Do not jump directly to manual retry before a read-only delivery dashboard.

Recommended sequence:

```text
PR20b — Admin alert delivery dashboard, read-only
PR20c — Manual retry for failed deliveries
```

PR20b should consume `alert_delivery_attempts` as a read model and must not retry or mutate delivery state.

PR20c should add manual retry only after the dashboard makes failed/skipped/unknown delivery states visible and reviewable.
