# PR23a — Admin audit log ledger production smoke

Date: 2026-06-15
Environment: production (`avito-watcher-prod`)
Repository: `Mitronomik/avito-watcher`

## Summary

PR23a added a persistent admin audit log ledger for operator-facing technical admin actions, initially focused on manual alert delivery retry attempts.

Final status:

```text
PR23a — Admin audit log ledger ✅
Merged ✅
Deployed ✅
Migration repaired ✅
Production-smoked ✅
Manual retry audit-smoked ✅
Secret-safety check passed ✅
```

## Merge and deployment metadata

```text
PR: #208 — Add admin audit log ledger
Merge commit: 0aa7545bab6e9ae5448b095f177406ed6b068017
Previous deployed commit before pull: dc4de77
Final deployed commit: 0aa7545
Alembic migration: 0017_admin_audit_events
Final Alembic state: 0017_admin_audit_events (head)
```

Changed code introduced:

```text
alembic/env.py
alembic/versions/0017_admin_audit_events.py
app/admin.py
app/models/admin_audit_event.py
app/services/admin_audit.py
docs/admin_ui.md
tests/test_admin_audit.py
```

A docs-only handoff from PR22b was also pulled into production with this deploy because it had been merged after the previous production state:

```text
docs/handoff/pr22b_retention_dry_run_report_smoke_2026-06-15.md
```

## Intended PR23a behavior

PR23a must:

- create `admin_audit_events` via Alembic migration `0017_admin_audit_events`;
- record manual alert retry POST actions only;
- not audit read-only GET requests;
- store only safe audit metadata;
- store `request.url.path` only, never query strings;
- leave raw IP and raw user-agent out of the database;
- use `actor_kind = admin_technical_key` and `actor_label = technical_admin`;
- not store admin key values, prefixes, hashes, request bodies, headers, cookies, raw payloads, or Apps Script URLs;
- keep retry behavior unchanged;
- render a compact read-only recent audit section on `/admin/system`;
- keep retention execution disabled / not implemented.

## Deployment commands used

Initial deployment attempted:

```bash
cd ~/apps/avito-watcher

mkdir -p backups
BACKUP_FILE="backups/pr23a_pre_admin_audit_events_$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$BACKUP_FILE"

ls -lh "$BACKUP_FILE"

git pull --ff-only origin main
git log -1 --oneline

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic upgrade head

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

## Deployment incident: invalid backup command

The first backup command failed:

```text
pg_dump: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  role "root" does not exist
```

The resulting backup file was invalid:

```text
-rw-rw-r-- 1 deploy deploy 0 Jun 15 20:08 backups/pr23a_pre_admin_audit_events_20260615T200842Z.dump
```

Root cause:

```text
The command expanded `$POSTGRES_USER` / `$POSTGRES_DB` on the host instead of inside the postgres container.
`pg_dump` therefore attempted to connect as local/default root.
```

Repair action:

```bash
find backups -name 'pr23a_pre_admin_audit_events_*.dump' -size 0 -print

for f in $(find backups -name 'pr23a_pre_admin_audit_events_*.dump' -size 0); do
  mv "$f" "$f.invalid_zero_bytes"
done

BACKUP_FILE="backups/pr23a_repair_before_admin_audit_events_$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP_FILE"

test -s "$BACKUP_FILE" && ls -lh "$BACKUP_FILE"
```

Valid repair backup created:

```text
backups/pr23a_repair_before_admin_audit_events_20260615T201017Z.dump
size: 2.4M
```

The zero-byte backup placeholder was renamed to:

```text
backups/pr23a_pre_admin_audit_events_20260615T200842Z.dump.invalid_zero_bytes
```

## Deployment incident: duplicate table during Alembic upgrade

First Alembic upgrade attempt failed:

```text
INFO  [alembic.runtime.migration] Running upgrade 0016_monitor_cycle_runs -> 0017_admin_audit_events, admin audit events
psycopg.errors.DuplicateTable: relation "admin_audit_events" already exists
```

Observed state after failure:

```sql
select version_num from alembic_version;
select to_regclass('public.admin_audit_events') as admin_audit_events_table;
select count(*) as admin_audit_events_count from admin_audit_events;
```

Output:

```text
version_num: 0016_monitor_cycle_runs
admin_audit_events_table: admin_audit_events
admin_audit_events_count: 0
```

Interpretation:

```text
The table existed, but Alembic revision had not advanced to 0017.
The table was empty, so the safe repair was to drop the empty table and rerun Alembic.
```

Repair commands:

```bash
cd ~/apps/avito-watcher

docker compose --env-file .env -f deploy/docker-compose.prod.yml stop app

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec postgres \
  sh -lc 'psql -v ON_ERROR_STOP=1 -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
drop table if exists admin_audit_events cascade;
"'

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic upgrade head

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

Repair output:

```text
DROP TABLE
INFO  [alembic.runtime.migration] Running upgrade 0016_monitor_cycle_runs -> 0017_admin_audit_events, admin audit events
```

Final migration verification:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select version_num from alembic_version;
select count(*) as admin_audit_events_count from admin_audit_events;
"'
```

Output:

```text
0017_admin_audit_events (head)
version_num: 0017_admin_audit_events
admin_audit_events_count: 0
```

Health check:

```bash
for i in $(seq 1 20); do
  echo "try $i"
  curl -fsS http://127.0.0.1:8010/health && break
  sleep 2
done
```

Output:

```json
{"status":"ok"}
```

## `/admin/system` read-only smoke

Read key setup:

```bash
ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"
```

System page request:

```bash
curl -sS -o /tmp/pr23a_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Output:

```text
200
```

Audit section rendered:

```text
Recent admin audit events
Read-only compact audit ledger. Metadata, request bodies, headers, cookies, API keys, raw IPs, and raw user-agents are not shown.
No admin audit events yet.
```

GET audit non-creation query:

```sql
select
  count(*) as total,
  count(*) filter (where action = 'alert_delivery_retry') as retry_events,
  count(*) filter (where request_method = 'GET') as get_events
from admin_audit_events;
```

Output:

```text
total: 0
retry_events: 0
get_events: 0
```

This confirms read-only GET page views did not create audit events.

## Secret/action leak smoke for `/admin/system`

Initial broad grep produced a false positive because the safe explanatory text contains the word `cookies`:

```text
Metadata, request bodies, headers, cookies, API keys, raw IPs, and raw user-agents are not shown.
```

Refined grep:

```bash
grep -Ei "<form|<button|Authorization:|X-API-Key:|ADMIN_UI_READ_KEY=|DATABASE_URL=|postgres://|payload_json|confirm_action|script\.google\.com/macros/s/" /tmp/pr23a_system.html \
  && echo "CHECK possible leak/action" \
  || echo "OK no obvious secret/action leak"
```

Output:

```text
OK no obvious secret/action leak
```

## Manual retry audit smoke

A safe blocked retry case was selected:

```text
alert_delivery_attempts.id = 140
channel = google_sheets
listing_external_id = 8154722632
dedupe_key = google_sheets:new:8154722632
status = failed
matching_alert_sent_exists = true
```

Pre-check query:

```sql
select
  a.id,
  a.status,
  a.channel,
  a.listing_external_id,
  a.dedupe_key,
  exists (
    select 1
    from alerts_sent s
    where s.dedupe_key = a.dedupe_key
      and s.listing_external_id = a.listing_external_id
      and s.channel = a.channel
  ) as matching_alert_sent_exists
from alert_delivery_attempts a
where a.id = 140;

select
  (select count(*) from admin_audit_events) as audit_before,
  (select count(*) from alert_delivery_attempts) as attempts_before,
  (select count(*) from alerts_sent) as alerts_sent_before;
```

Output:

```text
id: 140
status: failed
channel: google_sheets
listing_external_id: 8154722632
dedupe_key: google_sheets:new:8154722632
matching_alert_sent_exists: true

audit_before: 0
attempts_before: 409
alerts_sent_before: 3322
```

POST command:

```bash
ADMIN_TECH_KEY="$(
  grep -E '^ADMIN_UI_TECHNICAL_WRITE_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

curl -sS -o /tmp/pr23a_retry_140.json -w "%{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "admin_technical_write_key=$ADMIN_TECH_KEY" \
  --data-urlencode "confirm_action=retry_delivery_attempt_140" \
  "http://127.0.0.1:8010/admin/alerts/delivery-attempts/140/retry"

cat /tmp/pr23a_retry_140.json
```

Output:

```text
403
```

The blocked reason was stored in audit metadata:

```json
{"reason": "technical_ops_disabled", "source_attempt_id": 140}
```

This means the retry was blocked before creating a new delivery attempt. This is acceptable and safer than forcing an actual delivery in production.

Audit query:

```sql
select
  id,
  actor_kind,
  actor_label,
  action,
  target_type,
  target_id,
  status,
  request_method,
  request_path,
  ip_hash,
  user_agent_hash,
  metadata_json,
  error_type,
  error_message
from admin_audit_events
order by id desc
limit 3;

select
  (select count(*) from admin_audit_events) as audit_after,
  (select count(*) from alert_delivery_attempts) as attempts_after,
  (select count(*) from alerts_sent) as alerts_sent_after;
```

Output:

```text
id: 1
actor_kind: admin_technical_key
actor_label: technical_admin
action: alert_delivery_retry
target_type: alert_delivery_attempt
target_id: 140
status: blocked
request_method: POST
request_path: /admin/alerts/delivery-attempts/140/retry
ip_hash: null
user_agent_hash: null
metadata_json: {"reason": "technical_ops_disabled", "source_attempt_id": 140}
error_type: null
error_message: null

audit_after: 1
attempts_after: 409
alerts_sent_after: 3322
```

This confirms:

```text
manual retry POST was audited ✅
retry was blocked ✅
no new alert_delivery_attempts were created ✅
no new alerts_sent rows were created ✅
```

## Audit secret-safety smoke

Query:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
select
  count(*) filter (
    where
      request_path like '%api_key%'
      or metadata_json::text ~* 'admin_technical_write_key|confirm_action|x-api-key|authorization|cookie|script\.google\.com|payload_json'
      or coalesce(error_message, '') ~* 'admin_technical_write_key|x-api-key|authorization|cookie|script\.google\.com|payload_json'
  ) as possible_secret_leaks
from admin_audit_events;
SQL
```

Output:

```text
possible_secret_leaks: 0
```

## `/admin/system` after POST audit smoke

Request:

```bash
curl -sS -o /tmp/pr23a_system_after_retry.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

grep -E "Recent admin audit events|alert_delivery_retry|admin_technical_key|blocked|technical_ops_disabled" /tmp/pr23a_system_after_retry.html
```

Output:

```text
200
```

Rendered audit row:

```text
created_at: 2026-06-15 20:19:30.280425
actor_kind: admin_technical_key
action: alert_delivery_retry
target_type: alert_delivery_attempt
target_id: 140
status: blocked
error_type: —
error_message: —
```

Data volume after POST:

```text
admin_audit_events: 1
alert_delivery_attempts: 409
alerts_sent: 3322
```

## Production health observations after deploy

`/admin/system` also showed:

```text
Alert delivery attempts total: 409
failed 24h / 7d: 3 / 3
unknown 24h / 7d: 0 / 0
manual_retry attempts: 0
alerts_sent total: 3322
```

Delivery integrity issues:

```text
success_without_alert_sent: 0
success_missing_sent_at: 0
non_success_with_sent_at: 0
bad_payload_hash_count: 0
non_success_after_alert_sent: 0
resolved_non_success_with_later_alert_sent: 3
next_retry_at_non_null: 0
```

Monitor cycle history:

```text
last 24h cycles total: 244
success: 244
partial: 0
failed: 0
skipped: 0
stale running count: 0
```

Retention remained read-only / disabled:

```text
Retention mode: policy-only
Retention execution: disabled / not implemented
Retention dry-run: available / read-only
```

No retention execution or destructive admin actions were introduced.

## Final result

```text
PR23a deploy: OK ✅
Backup repair: OK ✅
Migration repair: OK ✅
Alembic 0017: OK ✅
/health: OK ✅
/admin/system: OK ✅
GET audit non-creation: OK ✅
Manual retry POST audit creation: OK ✅
Delivery counters unchanged after blocked retry: OK ✅
Secret-safety check: OK ✅
Retention execution still disabled: OK ✅
```

## Follow-up recommendation

Do not proceed directly to retention execution or user/RBAC work.

Recommended next roadmap step:

```text
PR23b — Admin access control hardening
```

Suggested scope:

- centralize admin read key and technical write key checks;
- keep GET read access separate from POST technical action access;
- ensure GET never requires the technical key;
- ensure POST manual retry requires both read key and technical key;
- never store key hash, prefix, or value;
- never log key material;
- do not add users, login sessions, RBAC, retention execution, parser changes, scoring changes, or agent behavior changes.
