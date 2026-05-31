# Production backup and restore

This guide covers safe operational backup and restore procedures for the
production Docker Compose deployment. It backs up:

- PostgreSQL data from the `postgres` service in `deploy/docker-compose.prod.yml`.
- App data files under root `data/`, including JSONL alert trails, debug files,
  status files, and worker lock/runtime artifacts when present.

Restore is destructive for the database. Read the restore section fully before
running any command.

## What not to commit

Never commit operational artifacts or secrets:

- `.env`, `.env.*`, real tokens, passwords, or API keys.
- `backups/` contents.
- `data/` contents, JSONL alert trails, debug HTML, sessions, cookies, logs, or
  local databases.

The backup scripts do not print database passwords or other secret values.

## Create a backup

Run from the repository root on the production server:

```bash
./scripts/prod_backup.sh
```

The script detects the repository root, uses `.env`, and runs `pg_dump` through
the production Compose stack:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres ...
```

Each run creates a timestamped directory:

```text
backups/YYYYMMDD_HHMMSS/
```

Expected files:

- `postgres.sql.gz` — compressed PostgreSQL SQL dump.
- `data.tar.gz` — archive of `data/` when the directory exists.
- `manifest.txt` — timestamp, git commit SHA when available, Compose file path,
  and files created.

If Docker Compose, `.env`, the production Compose file, or the `postgres` service
is unavailable, the script exits with a clear error.

`pg_dump` produces a consistent database dump while services are running. If you
also need the most consistent `data.tar.gz` file archive, you may briefly stop the
worker and app before backup, then start them again after the backup completes.

## Verify backup files exist

List the backup directories:

```bash
ls -lah backups/
```

List files inside recent backups:

```bash
find backups/ -maxdepth 2 -type f -print
```

Verify the compressed SQL dump is readable:

```bash
gzip -t backups/*/*.sql.gz
```

Inspect the manifest:

```bash
cat backups/<YYYYMMDD_HHMMSS>/manifest.txt
```

## List backup contents

List the app data archive without extracting it:

```bash
tar -tzf backups/<YYYYMMDD_HHMMSS>/data.tar.gz
```

Preview the SQL dump without extracting it to disk:

```bash
gzip -dc backups/<YYYYMMDD_HHMMSS>/postgres.sql.gz | sed -n '1,40p'
```

Do not paste SQL output into tickets or chat if it may contain production data.


## Verify the latest backup

Find the latest timestamp-like backup directory and inspect its manifest:

```bash
latest_backup="$(find backups -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -E '^[0-9]{8}_[0-9]{6}$' | sort | tail -n 1)"
test -n "$latest_backup"
printf 'Latest backup: backups/%s\n' "$latest_backup"
cat "backups/$latest_backup/manifest.txt"
gzip -t "backups/$latest_backup/postgres.sql.gz"
```

This check verifies the latest backup directory name, confirms the manifest is
present, and validates that the compressed PostgreSQL dump can be read. If the
backup includes `data.tar.gz`, list it without extracting production data:

```bash
tar -tzf "backups/$latest_backup/data.tar.gz"
```

## Backup retention and pruning

`./scripts/prod_prune_backups.sh` removes old timestamp-like backup directories
under repository root `backups/` only. It ignores files and non-matching
directories, only considers names shaped as `YYYYMMDD_HHMMSS`, and always keeps
the latest timestamp-like backup even when it is older than the retention window.

The retention window defaults to 14 days:

```bash
DRY_RUN=true ./scripts/prod_prune_backups.sh
```

To test a different retention window without deleting anything:

```bash
DRY_RUN=true BACKUP_RETENTION_DAYS=30 ./scripts/prod_prune_backups.sh
```

Real deletion requires an explicit `DRY_RUN=false` override:

```bash
DRY_RUN=false BACKUP_RETENTION_DAYS=30 ./scripts/prod_prune_backups.sh
```

If `backups/` is missing, the prune script exits with an error instead of
creating or deleting anything. Review the dry-run output before every real prune,
and never commit `backups/` contents to git.

## Production backup scheduling

A simple daily cron schedule can create one backup and then prune old backups.
Use the absolute repository path for the production checkout and keep the prune
step in dry-run mode until the output has been reviewed at least once:

```cron
# Daily UTC backup at 02:15, then retain 14 days while always keeping the latest backup.
15 2 * * * cd /opt/avito-watcher && ./scripts/prod_backup.sh >> /var/log/avito-watcher-backup.log 2>&1 && DRY_RUN=false BACKUP_RETENTION_DAYS=14 ./scripts/prod_prune_backups.sh >> /var/log/avito-watcher-backup.log 2>&1
```

For the first scheduled run, use `DRY_RUN=true` in the prune command and switch
to `DRY_RUN=false` only after the log lists the expected directories.

A systemd timer is an alternative when the host standardizes on systemd units.
Example service:

```ini
# /etc/systemd/system/avito-watcher-backup.service
[Unit]
Description=Avito Watcher production backup and retention prune

[Service]
Type=oneshot
WorkingDirectory=/opt/avito-watcher
ExecStart=/bin/bash -lc './scripts/prod_backup.sh && DRY_RUN=false BACKUP_RETENTION_DAYS=14 ./scripts/prod_prune_backups.sh'
```

Example timer:

```ini
# /etc/systemd/system/avito-watcher-backup.timer
[Unit]
Description=Run Avito Watcher production backup daily

[Timer]
OnCalendar=*-*-* 02:15:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now avito-watcher-backup.timer
systemctl list-timers avito-watcher-backup.timer
```

## Restore the DB on the same server

The restore script only restores PostgreSQL. It does not restore `data/`.

Stop the worker and app first so no service writes during the destructive database restore:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
docker compose --env-file .env -f deploy/docker-compose.prod.yml stop app
```

Run the restore with an explicit confirmation variable and dump path:

```bash
CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh backups/<YYYYMMDD_HHMMSS>/postgres.sql.gz
```

The script supports both `.sql.gz` and `.sql` dumps:

```bash
CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh backups/<YYYYMMDD_HHMMSS>/postgres.sql
```

After restore, start the app first, then start the worker and run the normal
production health checks:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

## Restore the DB on a fresh server

1. Clone the repository and check out the intended release commit.
2. Create the production `.env` from secure secret storage. Do not copy secrets
   through git.
3. Copy the selected backup directory to the new server, for example under
   `backups/<YYYYMMDD_HHMMSS>/`.
4. Start infrastructure only:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d postgres redis
```

5. Restore the database:

```bash
CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh backups/<YYYYMMDD_HHMMSS>/postgres.sql.gz
```

6. Restore `data/` manually if needed, then start the app and worker following
   the production checklist.

Do not run migrations before restoring unless the target backup and application
version require a planned migration path. Prefer restoring to the same application
revision recorded in `manifest.txt`, then migrate deliberately if needed.

## Restore `data/` manually

The database restore script intentionally does not restore files under `data/`.
To restore app data from a backup archive, review the archive first:

```bash
tar -tzf backups/<YYYYMMDD_HHMMSS>/data.tar.gz
```

Stop services that may write to `data/`:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
docker compose --env-file .env -f deploy/docker-compose.prod.yml stop app telegram_bot
```

Move the current data directory aside and extract the backup:

```bash
mv data "data.before_restore.$(date -u +%Y%m%d_%H%M%S)"
tar -xzf backups/<YYYYMMDD_HHMMSS>/data.tar.gz -C .
```

Confirm ownership and permissions match the deployment user, then restart the
services you need.

## Safe restore rehearsal

Use a non-production host or a disposable clone of the repository:

1. Copy `.env` with non-production secrets or isolated local credentials.
2. Copy one backup directory into `backups/`.
3. Start only `postgres` and `redis` in the rehearsal environment.
4. Run:

```bash
CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh backups/<YYYYMMDD_HHMMSS>/postgres.sql.gz
```

5. Run read-only checks, such as service `ps`, API health after app startup, and
   selected admin/listing queries.
6. If validating `data/`, extract it only in the rehearsal checkout and inspect
   JSONL/debug/status files there.

Never rehearse a restore against the live production database unless the goal is
an actual production restore.

## Destructive restore warning

`prod_restore_db.sh` drops and recreates the PostgreSQL `public` schema before
loading the supplied dump. Any current production rows in that schema can be
lost. The script refuses to run unless `CONFIRM_RESTORE=yes` is set and a dump
file argument is provided.
