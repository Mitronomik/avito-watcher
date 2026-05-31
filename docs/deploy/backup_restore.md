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

## Restore the DB on the same server

The restore script only restores PostgreSQL. It does not restore `data/`.

Stop the worker first so monitoring does not write during the restore:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker
```

Run the restore with an explicit confirmation variable and dump path:

```bash
CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh backups/<YYYYMMDD_HHMMSS>/postgres.sql.gz
```

The script supports both `.sql.gz` and `.sql` dumps:

```bash
CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh backups/<YYYYMMDD_HHMMSS>/postgres.sql
```

After restore, run the normal production health checks and restart services as
needed:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
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
