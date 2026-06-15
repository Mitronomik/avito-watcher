# PR23b — Admin access control hardening production smoke

Date: 2026-06-15  
Environment: `avito-watcher-prod`  
Commit deployed: `f7cd12018ce32a9b2e9753e4e93965cc3895a1d8`  
Merge commit title: `Harden admin key-based access control (#210)`

## Status

```text
PR23b - Admin access control hardening
merged ✅
deployed ✅
production-smoked ✅
```

## Scope reminder

PR23b hardened key-based admin access boundaries.

It introduced:

- centralized admin read-key checks;
- centralized technical-write-key checks for technical POST actions;
- fail-closed behavior when required keys are not configured;
- constant-time key comparison through `secrets.compare_digest(...)`;
- no read-key fallback to write/technical/API keys for the admin read boundary;
- no technical-write-key transport through query-string parameters;
- preservation of existing technical-ops flag behavior;
- preservation of existing action-specific confirmation behavior;
- focused access-control tests and admin UI documentation updates.

This PR did not introduce:

- users;
- RBAC;
- login/session authentication;
- CSRF protection;
- new key transports;
- successful technical retry execution while technical ops are disabled;
- any database migration.

## Deployed revision

Production was fast-forwarded from:

```text
0aa7545 Add admin audit log ledger (#208)
```

to:

```text
f7cd120 Harden admin key-based access control (#210)
```

Recent production log:

```text
f7cd120 (HEAD -> main, origin/main, origin/HEAD) Harden admin key-based access control (#210)
5b3174d Add PR23a production smoke handoff (#209)
0aa7545 Add admin audit log ledger (#208)
f11320d Add PR22b production smoke handoff (#207)
dc4de77 Add read-only retention dry-run report (#206)
```

## Build and startup

Production deploy commands completed successfully:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Container deploy-postgres-1 Healthy
Container deploy-redis-1    Healthy
Container deploy-app-1      Started
Container deploy-worker-1   Started
```

The first immediate `/health` request returned a transient connection reset while the app was still coming up. A repeat request succeeded.

## Health smoke

Command:

```bash
curl -i http://localhost:8010/health
```

Result:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

## Alembic smoke

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec app \
  sh -lc 'alembic current'
```

Result:

```text
0017_admin_audit_events (head)
```

Expected because PR23b has no migration and builds on PR23a's audit ledger migration.

## Safe key handling during smoke

Admin keys were loaded into shell variables without printing their values and without relying on `source .env`.

Command pattern:

```bash
set +x

ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

ADMIN_TECH_KEY="$(
  grep -E '^ADMIN_UI_TECHNICAL_WRITE_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

printf 'read_key_len=%s\ntech_key_len=%s\n' "${#ADMIN_READ_KEY}" "${#ADMIN_TECH_KEY}"
```

Result:

```text
read_key_len=64
tech_key_len=43
```

No secret values were printed.

## Admin read access smoke

### Valid read key

Command:

```bash
curl -sS -o /tmp/pr23b_admin_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Result:

```text
200
```

The rendered page included expected admin system sections, including:

- Alert Delivery health;
- Monitor cycle history;
- Recent failed delivery attempts;
- Recent admin audit events;
- Alembic revision `0017_admin_audit_events`;
- Backup / restore / retention readiness;
- retention dry-run report.

### Missing read key

Command:

```bash
curl -sS -o /tmp/pr23b_admin_system_no_key.json -w "%{http_code}\n" \
  "http://127.0.0.1:8010/admin/system"

cat /tmp/pr23b_admin_system_no_key.json
```

Result:

```text
403
{"detail":"Invalid admin key"}
```

This confirms fail-closed read access.

## Read-only GET audit behavior

Before valid GET:

```sql
select count(*) as audit_before_get from admin_audit_events;
```

Result:

```text
audit_before_get = 1
```

After valid GET:

```sql
select count(*) as audit_after_get from admin_audit_events;
```

Result:

```text
audit_after_get = 1
```

After invalid no-key GET:

```sql
select count(*) as audit_after_invalid_get from admin_audit_events;
```

Result:

```text
audit_after_invalid_get = 1
```

Conclusion:

```text
Read-only GET requests did not create admin audit events.
```

This preserves the PR23a audit-noise boundary.

## Technical POST smoke

The smoke used existing failed delivery attempt `140` from the admin system page.

Pre-POST counts:

```sql
select
  (select count(*) from admin_audit_events) as audit_before_post,
  (select count(*) from alert_delivery_attempts) as attempts_before_post,
  (select count(*) from alerts_sent) as alerts_sent_before_post;
```

Result:

```text
audit_before_post       = 1
attempts_before_post    = 415
alerts_sent_before_post = 3328
```

Command:

```bash
curl -sS -o /tmp/pr23b_retry_140.json -w "%{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "admin_technical_write_key=$ADMIN_TECH_KEY" \
  --data-urlencode "confirm_action=retry_delivery_attempt_140" \
  "http://127.0.0.1:8010/admin/alerts/delivery-attempts/140/retry"
```

Result:

```text
403
```

This is expected because production has technical operations disabled.

Post-POST audit event:

```text
id: 2
actor_kind: admin_technical_key
actor_label: technical_admin
action: alert_delivery_retry
target_type: alert_delivery_attempt
target_id: 140
status: blocked
request_method: POST
request_path: /admin/alerts/delivery-attempts/140/retry
metadata_json: {"reason": "technical_ops_disabled", "source_attempt_id": 140}
```

Post-POST counts:

```text
audit_after_post       = 2
attempts_after_post    = 415
alerts_sent_after_post = 3328
```

Conclusion:

```text
Blocked technical retry created an audit event.
No alert_delivery_attempts rows were created.
No alerts_sent rows were created.
```

This confirms the expected safe blocked path:

```text
technical ops disabled -> no delivery mutation -> blocked audit event only
```

## Audit secret-safety smoke

SQL check:

```sql
select
  count(*) filter (
    where
      request_path like '%api_key%'
      or metadata_json::text ~* 'admin_technical_write_key|confirm_action|x-api-key|authorization|cookie|script\.google\.com|payload_json'
      or coalesce(error_message, '') ~* 'admin_technical_write_key|x-api-key|authorization|cookie|script\.google\.com|payload_json'
  ) as possible_secret_leaks
from admin_audit_events;
```

Result:

```text
possible_secret_leaks = 0
```

Conclusion:

```text
No key names, request body fields, headers, cookies, Apps Script URL, or payload dumps were found in audit fields checked by the smoke.
```

## Runtime log secret grep

Commands:

```bash
if [ -n "$ADMIN_READ_KEY" ] && [ -n "$ADMIN_TECH_KEY" ]; then
  docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=300 app worker \
    | grep -F "$ADMIN_READ_KEY" || true

  docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=300 app worker \
    | grep -F "$ADMIN_TECH_KEY" || true
else
  echo "Keys are empty in shell; refusing log grep"
fi

docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=300 app worker \
  | grep -E "script\.google\.com/macros|admin_technical_write_key=[^[:space:]]+|X-API-Key|Authorization|Cookie" || true
```

Result:

```text
No matches.
```

Conclusion:

```text
No admin read key, technical key, full Apps Script URL, X-API-Key, Authorization, or Cookie leak was found in the checked runtime logs.
```

## Production health observations from admin system page

At the time of smoke:

```text
alert_delivery_attempts: 415
alerts_sent: 3328
failed delivery attempts last 24h / 7d: 3 / 3
manual_retry attempts: 0
success_without_alert_sent: 0
success_missing_sent_at: 0
non_success_with_sent_at: 0
bad_payload_hash_count: 0
non_success_after_alert_sent: 0
resolved_non_success_with_later_alert_sent: 3
monitor cycles last 24h: 352 total, 352 success, 0 failed
admin_audit_events: 1 before PR23b blocked retry smoke, 2 after blocked retry smoke
```

The three failed delivery attempts were Google Sheets `HTTPStatusError` 500 responses. Runtime and admin UI redacted the Apps Script URL to:

```text
https://script.google.com/.../exec
```

This is expected post-PR21d behavior.

## Final conclusion

PR23b is closed in production:

```text
merged ✅
deployed ✅
production-smoked ✅
docs handoff ✅
```

The production smoke confirms:

- admin read boundary is fail-closed;
- valid read key can access read-only admin system page;
- read-only GET does not create audit noise;
- technical POST path requires explicit technical form key and still respects `ADMIN_UI_TECHNICAL_OPS_ENABLED=false`;
- blocked technical POST writes a minimal audit event;
- blocked technical POST does not mutate delivery attempts or alerts;
- audit request path is path-only;
- audit and logs did not expose checked secret material.

## Next recommended step

Do not start autonomous operations yet.

Recommended next PR:

```text
PR24a — Backtesting dashboard read model hardening
```

Scope should remain read-only:

- improve outcome analytics visibility;
- keep scoring unchanged;
- no automatic calibration;
- no agent-driven filter changes;
- no retention execution;
- no new auth model.
