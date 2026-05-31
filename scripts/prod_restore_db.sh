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
DUMP_FILE="${1:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

usage() {
  cat >&2 <<'USAGE'
Usage:
  CONFIRM_RESTORE=yes ./scripts/prod_restore_db.sh <dump.sql|dump.sql.gz>

This restores only the PostgreSQL database. Restore data/ manually if needed.
USAGE
}

[[ -n "$DUMP_FILE" ]] || { usage; fail "missing DB dump file argument."; }
[[ "${CONFIRM_RESTORE:-}" == "yes" ]] || {
  usage
  fail "refusing to restore unless CONFIRM_RESTORE=yes is set."
}
[[ -f "$DUMP_FILE" ]] || fail "dump file not found: $DUMP_FILE"
[[ -r "$DUMP_FILE" ]] || fail "dump file is not readable: $DUMP_FILE"
case "$DUMP_FILE" in
  *.sql|*.sql.gz) ;;
  *) fail "unsupported dump format. Expected .sql or .sql.gz" ;;
esac

if [[ "$DUMP_FILE" == *.sql.gz ]]; then
  command -v gzip >/dev/null 2>&1 || fail "gzip is not installed or not on PATH."
fi

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not available (expected 'docker compose')."
[[ -f "$COMPOSE_FILE" ]] || fail "production compose file not found: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || fail "production env file not found: $ENV_FILE"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if ! "${COMPOSE[@]}" ps postgres >/dev/null 2>&1; then
  fail "postgres service is unavailable in the production Docker Compose stack. Start it first."
fi

if ! "${COMPOSE[@]}" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null; then
  fail "postgres service is not ready. Check: docker compose --env-file .env -f deploy/docker-compose.prod.yml ps postgres"
fi

cat >&2 <<'WARNING'
WARNING: DESTRUCTIVE DATABASE RESTORE
This will drop and recreate the public schema in the production PostgreSQL database,
then load the supplied SQL dump. Current database contents may be permanently lost.
The data/ directory is NOT restored by this script.
WARNING

RESTORE_CMD='psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" >/dev/null && psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

if [[ "$DUMP_FILE" == *.sql.gz ]]; then
  gzip -dc "$DUMP_FILE" | "${COMPOSE[@]}" exec -T postgres sh -c "$RESTORE_CMD"
else
  "${COMPOSE[@]}" exec -T postgres sh -c "$RESTORE_CMD" < "$DUMP_FILE"
fi

printf 'Database restore completed from: %s\n' "$DUMP_FILE"
