#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  :
else
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

COMPOSE_FILE="$REPO_ROOT/deploy/docker-compose.prod.yml"
ENV_FILE="$REPO_ROOT/.env"
BACKUP_ROOT="$REPO_ROOT/backups"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"
DB_DUMP="$BACKUP_DIR/postgres.sql.gz"
DATA_ARCHIVE="$BACKUP_DIR/data.tar.gz"
MANIFEST="$BACKUP_DIR/manifest.txt"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not available (expected 'docker compose')."
command -v gzip >/dev/null 2>&1 || fail "gzip is not installed or not on PATH."
command -v tar >/dev/null 2>&1 || fail "tar is not installed or not on PATH."
[[ -f "$COMPOSE_FILE" ]] || fail "production compose file not found: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || fail "production env file not found: $ENV_FILE"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if ! "${COMPOSE[@]}" ps postgres >/dev/null 2>&1; then
  fail "postgres service is unavailable in the production Docker Compose stack. Start it first."
fi

if ! "${COMPOSE[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null; then
  fail "postgres service is not ready. Check: docker compose --env-file .env -f deploy/docker-compose.prod.yml ps postgres"
fi

mkdir -p "$BACKUP_DIR"

echo "Creating PostgreSQL backup: $DB_DUMP"
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip -c > "$DB_DUMP"

CREATED_FILES=("postgres.sql.gz")

if [[ -d "$REPO_ROOT/data" ]]; then
  echo "Archiving app data directory: $DATA_ARCHIVE"
  tar -czf "$DATA_ARCHIVE" -C "$REPO_ROOT" data
  CREATED_FILES+=("data.tar.gz")
else
  echo "No data/ directory found; skipping app data archive."
fi

GIT_SHA="unavailable"
if GIT_SHA_VALUE="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"; then
  GIT_SHA="$GIT_SHA_VALUE"
fi

{
  echo "timestamp_utc=$TIMESTAMP"
  echo "git_commit_sha=$GIT_SHA"
  echo "docker_compose_file=deploy/docker-compose.prod.yml"
  echo "backup_directory=backups/$TIMESTAMP"
  echo "backup_files_created:"
  for file in "${CREATED_FILES[@]}"; do
    echo "- $file"
  done
} > "$MANIFEST"

printf 'Backup completed: %s\n' "$BACKUP_DIR"
printf 'Manifest: %s\n' "$MANIFEST"
