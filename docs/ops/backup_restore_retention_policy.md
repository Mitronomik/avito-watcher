# Backup, restore, and retention policy

PR22a is policy/readiness only. It does not execute backups, does not execute restore, and does not execute retention. It adds no scheduler, no retention dry-run, no deletion, no archive/truncate logic, and no Admin UI technical action.

## Backup goals

Backups should make it possible to recover the monitoring history, operator decisions, audit trail, and DB-stored configuration after host, volume, operator, or deployment failure.

Recommended baseline:

- take a full PostgreSQL backup at least daily for production;
- take an extra backup before migrations, restore work, risky deployment work, or manual DB maintenance;
- periodically test restore in staging;
- store backups outside the source repository and outside the primary database volume;
- protect backup files and secrets through the deployment/secret-management process.

## Data that must be backed up

### Critical business data

These tables preserve what was collected, analyzed, reviewed, decided, and stored as historical knowledge:

- `listings`
- `listing_analyses`
- `human_reviews`
- `human_review_actions`
- `investment_decisions`
- `market_research_runs`
- `market_evidence_items`
- `knowledge_notes`

### Operational / audit data

These tables preserve alert delivery history, monitor history, agent job history, and supporting enrichment/detail evidence:

- `alerts_sent`
- `alert_delivery_attempts`
- `monitor_cycle_runs`
- `agent_tasks`
- `listing_detail_snapshots`
- `listing_enrichments`

### Config / migration state

These tables preserve DB migration state and DB-stored monitoring configuration:

- `alembic_version`
- `search_jobs`

## Non-DB restore prerequisites

A database backup is necessary but may not be sufficient for a full service restore. A complete operator restore plan may also require securely managed copies of:

- `.env` / deployment secrets;
- Docker Compose and deployment configuration;
- Google credentials or service account files if used;
- external provider credentials;
- runtime data directory artifacts if the deployment treats them as audit artifacts;
- `alerts.jsonl` if still used;
- worker status files if needed for diagnostics.

This PR does not back up secrets. This PR does not expose secrets in Admin UI. Secrets and deployment configuration must be handled through a secure deployment/secret-management process outside this PR.

## JSONL and runtime data files

The repository still has a JSONL outbox notifier with the default relative file name `alerts.jsonl` under the configured data path. Because alert delivery attempts and sent-alert records are stored in the database, this JSONL file should be treated as an **operational duplicate of DB alert history / optional legacy artifact** unless an operator has separately declared it a durable audit artifact for a specific deployment.

Diagnostic/runtime files such as worker status files are operational diagnostics by default. They are useful for troubleshooting but are not treated here as critical durable business data.

PR22a does not implement file backup automation.

## Safe backup command templates

Use placeholders and deployment-specific paths. Do not paste real secrets into tickets, logs, or docs. Do not commit backup files to the repository.

```bash
# Example only. Adjust paths/env for actual production.
# Do not commit backup files to the repository.
# Do not print or paste real secrets.

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/avito_watcher_backup.dump'
```

Optional copy example with placeholders only:

```bash
# Example only. Store backups outside the source tree in real production.
BACKUP_DIR=/secure/off-repo/backup/location
mkdir -p "$BACKUP_DIR"
docker compose --env-file .env -f deploy/docker-compose.prod.yml cp \
  postgres:/tmp/avito_watcher_backup.dump "$BACKUP_DIR/avito_watcher_backup.dump"
```

## Restore principles

Never run restore commands against production without an approved restore plan, fresh pre-restore backup, and operator confirmation.

Restore must never run through Admin UI. Restore is an operator shell/deployment procedure only.

Safe restore flow:

1. Do not restore over a running app/worker.
2. Stop the worker first.
3. Stop the app if needed.
4. Take a fresh pre-restore backup before touching the DB.
5. Verify that the selected backup file exists.
6. Verify that the selected backup file size is non-zero.
7. Restore into staging first when possible.
8. Restore with a deployment-approved procedure.
9. Verify Alembic current revision after restore.
10. Run read-only smoke checks after restore.
11. Start the app.
12. Start the worker last.
13. Check `/health`.
14. Check `/admin/system`.
15. Check logs for errors and secret leakage.

Example stop command:

```bash
# Example only. Adjust paths/env for actual production.
docker compose --env-file .env -f deploy/docker-compose.prod.yml stop worker app
```

Example restore outline with placeholders:

```bash
# Example only. Do not run against production without an approved restore plan.
# Verify the backup file and restore target before continuing.

test -s /path/to/backup/avito_watcher_backup.dump

docker compose --env-file .env -f deploy/docker-compose.prod.yml cp \
  /path/to/backup/avito_watcher_backup.dump postgres:/tmp/restore.dump

# The exact restore command depends on the approved deployment procedure.
# Prefer staging validation before production restore.
```

## Post-restore smoke checklist

```bash
# Example only. Safe read-only checks.
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current

curl -i http://127.0.0.1:8010/health
```

Then open `/admin/system` with the read-only Admin UI key and verify:

- data volume summary renders;
- Alembic revision renders;
- monitor cycle history renders;
- alert delivery health renders;
- backup / restore / retention readiness renders;
- no secrets, webhook URLs, tokens, or sensitive absolute paths are shown.

## Retention policy principles

Retention candidate does not mean safe to delete.

### Keep indefinitely unless explicit migration/archival plan exists

- `listings`
- `listing_analyses`
- `human_reviews`
- `human_review_actions`
- `investment_decisions`
- `market_research_runs`
- `market_evidence_items`
- `knowledge_notes`
- `alerts_sent`
- `search_jobs`
- `alembic_version`

### Candidate for future retention dry-run

- `alert_delivery_attempts`
- `monitor_cycle_runs`
- old `agent_tasks`
- `listing_detail_snapshots`
- `listing_enrichments`

`alert_delivery_attempts` and `monitor_cycle_runs` are operational audit data. They may become retention candidates only after enough history is preserved, backup policy is verified, a dry-run report exists, an operator approval path exists, and an audit trail exists.

Future retention must follow this progression:

```text
policy
-> dry-run report
-> backup precondition
-> explicit operator approval
-> gated execution
-> audit trail
-> rollback/restore plan
```

PR22a implements only the policy step and safe read-only visibility. It does not add retention execution, retention dry-run execution, deletion code, archive code, truncate code, or automatic retention jobs.

## PR22b read-only retention dry-run report

PR22b adds read-only retention dry-run visibility only. It does not implement retention execution, deletion, archive, truncation, scheduler/cron, POST action, executor API, candidate row list, row IDs, or generated DELETE/ARCHIVE SQL.

Current progression state:

- policy: implemented;
- dry-run report: implemented;
- execution: not implemented.

The `/admin/system` dry-run report uses initial conservative reporting-only thresholds for operational tables. These thresholds are not execution policy and must be reviewed again before any future retention execution PR. A `dry_run_candidate_count` means only that rows matched the current reporting threshold at read time; it does not mean those rows are approved for deletion or archive.

Unknown and unsupported values are strict:

- `0` means the metric was measured and no rows matched;
- `unknown` means the metric was not measured or could not be safely evaluated;
- `not_supported` means the table/model lacks clear timestamp or status semantics for safe dry-run reporting.

The required future progression remains:

policy -> dry-run report -> backup precondition -> explicit operator approval -> gated execution -> audit trail -> rollback/restore plan

No future execution may treat the PR22b report as operator approval. No row IDs, URLs, payload JSON, secrets, raw environment values, sensitive paths, or delete/archive SQL are produced by this report.
