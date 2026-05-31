#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  :
else
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

BACKUP_ROOT="$REPO_ROOT/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
DRY_RUN="${DRY_RUN:-true}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || fail "BACKUP_RETENTION_DAYS must be a non-negative integer."
case "$DRY_RUN" in
  true|false) ;;
  *) fail "DRY_RUN must be exactly 'true' or 'false'. Default is 'true'." ;;
esac

[[ -d "$BACKUP_ROOT" ]] || fail "backup directory is missing: $BACKUP_ROOT"
[[ -r "$BACKUP_ROOT" ]] || fail "backup directory is not readable: $BACKUP_ROOT"
if [[ "$DRY_RUN" == "false" ]]; then
  [[ -w "$BACKUP_ROOT" ]] || fail "backup directory is not writable: $BACKUP_ROOT"
fi

if command -v realpath >/dev/null 2>&1; then
  BACKUP_ROOT_REAL="$(realpath "$BACKUP_ROOT")"
  REPO_ROOT_REAL="$(realpath "$REPO_ROOT")"
else
  BACKUP_ROOT_REAL="$(cd "$BACKUP_ROOT" && pwd -P)"
  REPO_ROOT_REAL="$(cd "$REPO_ROOT" && pwd -P)"
fi

[[ "$BACKUP_ROOT_REAL" == "$REPO_ROOT_REAL/backups" ]] || \
  fail "refusing to operate outside repository backups directory: $BACKUP_ROOT_REAL"

CUTOFF="$(date -u -d "$RETENTION_DAYS days ago" +%Y%m%d_%H%M%S)"

mapfile -t BACKUP_NAMES < <(
  find "$BACKUP_ROOT_REAL" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | awk '/^[0-9]{8}_[0-9]{6}$/ { print }' \
    | sort
)

if ((${#BACKUP_NAMES[@]} == 0)); then
  echo "No timestamp-like backup directories found under: $BACKUP_ROOT_REAL"
  exit 0
fi

LATEST_BACKUP="${BACKUP_NAMES[-1]}"
DELETE_NAMES=()

for backup_name in "${BACKUP_NAMES[@]}"; do
  if [[ "$backup_name" == "$LATEST_BACKUP" ]]; then
    continue
  fi

  if [[ "$backup_name" < "$CUTOFF" ]]; then
    DELETE_NAMES+=("$backup_name")
  fi
done

printf 'Backup root: %s\n' "$BACKUP_ROOT_REAL"
printf 'Retention days: %s\n' "$RETENTION_DAYS"
printf 'Cutoff timestamp (UTC): %s\n' "$CUTOFF"
printf 'Latest timestamp-like backup kept regardless of age: %s\n' "$LATEST_BACKUP"
printf 'Dry run: %s\n' "$DRY_RUN"

if ((${#DELETE_NAMES[@]} == 0)); then
  echo "No backup directories eligible for pruning."
  exit 0
fi

echo "Backup directories eligible for pruning:"
for backup_name in "${DELETE_NAMES[@]}"; do
  printf -- '- backups/%s\n' "$backup_name"
done

if [[ "$DRY_RUN" != "false" ]]; then
  echo "DRY_RUN is not false; no backup directories were deleted."
  exit 0
fi

for backup_name in "${DELETE_NAMES[@]}"; do
  [[ "$backup_name" =~ ^[0-9]{8}_[0-9]{6}$ ]] || fail "internal safety check rejected backup name: $backup_name"
  backup_path="$BACKUP_ROOT_REAL/$backup_name"
  [[ -d "$backup_path" ]] || fail "internal safety check found non-directory candidate: $backup_path"
  case "$backup_path" in
    "$BACKUP_ROOT_REAL"/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) fail "internal safety check rejected path outside backups: $backup_path" ;;
  esac

  rm -rf -- "$backup_path"
  printf 'Deleted: backups/%s\n' "$backup_name"
done
