# PR22a — Backup / restore / retention readiness production smoke

Date: 2026-06-15

Status: PASSED

## Scope

PR22a adds backup, restore, and retention policy documentation plus a read-only readiness section in `/admin/system`.

This PR is intentionally non-destructive:

- no backup execution;
- no restore execution;
- no retention execution;
- no deletion/archive/truncate logic;
- no scheduler;
- no DB migration;
- no Admin UI forms/buttons/actions.

## PRs / commits

- PR204 — `Add backup restore and retention readiness visibility`
- Merged commit: `49d5494bbf9df73e3a7e07370e7d81075be449bb`
- Deployed short commit: `49d5494`

## Production deploy

Production was updated with a fast-forward pull from `main`:

```text
792a45b..49d5494 main -> origin/main
49d5494 Add backup restore and retention readiness visibility (#204)
```

Only the app image was rebuilt and restarted, because the PR changes `app/admin.py` and documentation only.

```bash
cd ~/apps/avito-watcher

git pull --ff-only origin main
git log -1 --oneline

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

Health check initially returned transient startup failures and then succeeded:

```text
try 1: connection reset by peer
try 2: empty reply from server
try 3: {"status":"ok"}
```

## Admin smoke

`/admin/system` was checked with the read-only Admin API key.

Result:

```text
HTTP 200
```

The new read-only section rendered successfully:

```text
Готовность backup / restore / retention
Backup policy: docs/ops/backup_restore_retention_policy.md
Restore procedure: documented
Retention mode: policy-only
Retention execution: disabled / not implemented
Retention dry-run: not implemented
Latest backup: unknown
Backup metadata source: not configured
```

Existing system sections were still present, including:

- Parser diagnostics;
- Search jobs;
- Alert Delivery health;
- Monitor cycle history;
- Data volume summary;
- Alembic.

Alembic remained unchanged:

```text
current DB revision: 0016_monitor_cycle_runs
```

## Safety checks

No destructive Admin UI/action was found:

```text
OK no destructive UI/action
```

The smoke checked for forms/buttons and destructive action text such as:

- `Run backup`;
- `Restore now`;
- `Delete old data`;
- `Apply retention`;
- `Archive now`;
- `Truncate`.

No obvious sensitive leak was found:

```text
OK no obvious sensitive leak
```

The smoke checked for sensitive indicators such as:

- DB URLs;
- raw absolute host paths;
- authorization headers;
- raw Admin API key labels;
- raw Apps Script deployment URLs.

## Production health snapshot

Observed values during smoke:

```text
listings: 1738
listing_analyses: 730
alert_delivery_attempts: 383
alerts_sent: 3296
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

Monitor cycle health:

```text
last 24h cycles total: 85
success: 85
partial: 0
failed: 0
skipped: 0
stale running count: 0
```

Alert delivery health:

```text
delivery attempts total: 383
last 24h: 383
last 7d: 383
failed 24h/7d: 3/3
unknown 24h/7d: 0/0
manual_retry attempts: 0
alerts_sent total: 3296
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
next_retry_at_non_null: 0
```

## Final result

PR22a production smoke passed.

Confirmed:

- app deploy successful;
- `/health` OK;
- `/admin/system` OK;
- backup/restore/retention readiness visible;
- policy document path visible;
- retention execution disabled;
- retention dry-run not implemented;
- backup metadata unknown/not configured;
- no destructive UI/action;
- no obvious sensitive leak;
- no DB migration required;
- Alembic still at `0016_monitor_cycle_runs`.

## Follow-up

Next planned work should remain risk-layered:

1. PR22b — retention dry-run report only;
2. later, gated retention execution only after dry-run is proven and backup preconditions are explicit.
