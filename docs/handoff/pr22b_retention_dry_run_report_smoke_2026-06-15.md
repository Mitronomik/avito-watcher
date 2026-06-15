# PR22b — Retention dry-run report production smoke handoff

Date: 2026-06-15

## Summary

PR22b added a read-only retention dry-run report to `/admin/system`.

The feature is deliberately non-destructive:

```text
Dry-run only.
No rows are deleted, archived, updated, or scheduled for deletion by this report.
```

This handoff records the production deployment and smoke results after merging PR #206.

## Merge / deploy state

Merged PR:

```text
PR #206 — Add read-only retention dry-run report
Merge commit: dc4de779450c47a9dd7a3e250551dd16cc5fba56
Short commit: dc4de77
```

Production pull result:

```text
49d5494..dc4de77  main -> origin/main
Fast-forward
```

Production HEAD after pull:

```text
dc4de77 (HEAD -> main, origin/main, origin/HEAD) Add read-only retention dry-run report (#206)
```

Changed files observed during production pull:

```text
app/admin.py
app/services/retention_dry_run.py
docs/admin_ui.md
docs/handoff/pr22a_backup_restore_retention_readiness_smoke_2026-06-15.md
docs/ops/backup_restore_retention_policy.md
tests/test_admin_system_health.py
```

Note: `docs/handoff/pr22a_backup_restore_retention_readiness_smoke_2026-06-15.md` appeared in the same production pull because the server moved from `49d5494` to `dc4de77` and also picked up the docs-only PR22a handoff merge already on `main`.

## Build / restart

Because PR22b changed `app/admin.py` and added `app/services/retention_dry_run.py`, production app image was rebuilt and the `app` container was restarted.

Commands run:

```bash
cd ~/apps/avito-watcher

git pull --ff-only origin main
git log -1 --oneline

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

for i in $(seq 1 20); do
  echo "try $i"
  curl -fsS http://127.0.0.1:8010/health && break
  sleep 2
done
```

Observed:

```text
Image deploy-app Built
Container deploy-app-1 Started
```

Initial health probes returned transient startup errors:

```text
try 1
curl: (56) Recv failure: Connection reset by peer
try 2
curl: (52) Empty reply from server
```

This matches prior app restart behavior. The app then served `/admin/system` successfully.

## Admin system smoke

Command pattern:

```bash
ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

curl -sS -o /tmp/pr22b_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Observed HTTP status:

```text
200
```

The new dry-run section rendered:

```text
Dry-run отчёт по retention
Dry-run only.
dry_run_candidate_count
```

The readiness section was also updated correctly:

```text
Retention execution: disabled / not implemented
Retention dry-run: available / read-only
```

## Dry-run report values observed

Production `/admin/system` rendered the following retention dry-run rows:

| table | threshold days | timestamp column | dry_run_candidate_count | total_count | status |
|---|---:|---|---:|---:|---|
| `alert_delivery_attempts` | 90 | `created_at` | 0 | 397 | supported |
| `monitor_cycle_runs` | 90 | `started_at` | 0 | 171 | supported |
| `agent_tasks` | 180 | `created_at` | 0 | 2 | supported |
| `listing_detail_snapshots` | 180 | `created_at` | 0 | 0 | supported |
| `listing_enrichments` | 180 | `created_at` | 0 | 0 | supported |

Agent-task notes rendered terminal statuses only:

```text
Terminal statuses only: canceled, failed, skipped, success.
Aggregate read-only count; no row IDs are returned.
```

Interpretation:

```text
0 means measured zero.
It does not mean unknown.
No rows matched the reporting-only retention thresholds at smoke time.
```

## Existing production health shown on /admin/system

Selected production counters shown during smoke:

```text
listings: 1745
listing_analyses: 730
alert_delivery_attempts: 397
alerts_sent: 3310
agent_tasks: 2
human_reviews: 0
human_review_actions: 0
investment_decisions: 0
market_research_runs: 0
market_evidence_items: 0
listing_detail_snapshots: 0
listing_enrichments: 0
knowledge_notes: 0
search_jobs: 2
```

Monitor cycle health shown during smoke:

```text
last 24h cycles total: 171
success: 171
partial: 0
failed: 0
skipped: 0
stale running count: 0
```

Alert delivery health shown during smoke:

```text
delivery attempts total: 397
last 24h: 397
last 7d: 397
failed 24h/7d: 3/3
unknown 24h/7d: 0/0
manual_retry attempts: 0
alerts_sent total: 3310
```

Delivery integrity hard issues remained zero:

```text
success_without_alert_sent: 0
success_missing_sent_at: 0
non_success_with_sent_at: 0
bad_payload_hash_count: 0
non_success_after_alert_sent: 0
```

Resolved historical delivery records remained informational:

```text
resolved_non_success_with_later_alert_sent: 3
```

## Destructive UI/action check

Command:

```bash
grep -Ei "<form|<button|Run retention|Execute retention|Apply retention|Delete old data|Purge old data|Archive now|Truncate|Удалить|Архивировать|Очистить|Запустить очистку" /tmp/pr22b_system.html \
  && echo "FAIL destructive UI/action found" \
  || echo "OK no destructive UI/action"
```

Observed:

```text
OK no destructive UI/action
```

## Leak / execution-like SQL check

The first broad grep included the word `ARCHIVE`, which matched the expected warning text containing `archived`:

```text
No rows are deleted, archived, updated, or scheduled for deletion by this report.
```

That was a false positive, not a leak or execution SQL.

The refined grep was then used:

```bash
grep -Ei "DELETE FROM|UPDATE .* SET|TRUNCATE TABLE|DROP TABLE|payload_json|attributes_json|DATABASE_URL|postgres://|Authorization: Bearer|X-API-Key:|script\.google\.com/macros/s/" /tmp/pr22b_system.html \
  && echo "CHECK possible leak or execution-like SQL" \
  || echo "OK no obvious leak/execution SQL"
```

Observed:

```text
OK no obvious leak/execution SQL
```

## Alembic check

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current
```

Observed:

```text
0016_monitor_cycle_runs (head)
```

No migration was added or applied for PR22b.

## Result

```text
PR22b — Retention dry-run report: PASSED
Merged: yes
Deployed: yes
Production-smoked: yes
Migration: no
Destructive UI/actions: no
Execution-like SQL leaked in UI: no
Secrets/raw webhook URLs leaked in UI: no obvious leak
Retention execution: disabled / not implemented
Retention dry-run: available / read-only
```

## Follow-up

Next roadmap step should remain conservative.

PR22b only answers:

```text
How many rows would match the reporting-only retention thresholds?
```

It does not approve deletion or archive.

Future retention execution must still require a separate PR with at least:

```text
backup precondition
explicit operator approval
gated execution
audit trail
rollback/restore plan
```
