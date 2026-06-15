# PR23b — Admin access control hardening production smoke

Date: 2026-06-15
Environment: `avito-watcher-prod`
Repository: `Mitronomik/avito-watcher`
Branch: `main`
Production commit: `f7cd12018ce32a9b2e9753e4e93965cc3895a1d8`
PR: #210 — `Harden admin key-based access control`

## Scope

PR23b hardens the existing key-based admin access model.

The PR intentionally does not add:

- users;
- login/password;
- sessions;
- RBAC;
- CSRF framework;
- new migrations;
- new destructive admin actions;
- retention execution;
- parser/scoring/agent behavior changes;
- alert delivery semantics changes.

The intended access model after PR23b:

```text
GET /admin/...:
  requires valid ADMIN_UI_READ_KEY

technical POST actions:
  require valid ADMIN_UI_READ_KEY
  require valid ADMIN_UI_TECHNICAL_WRITE_KEY from existing form field
  require existing action-specific confirmation where applicable
```

Important hardening constraints:

- fail closed when configured read key is missing;
- fail closed when configured technical write key is missing for technical POST;
- no fallback between read key and technical key;
- read key cannot substitute technical key;
- technical key cannot substitute read key;
- no technical key accepted through query string;
- no new key transports;
- constant-time comparison is used in the centralized helper;
- read-only GET requests are not audited;
- existing PR23a audit behavior for blocked manual retry POST is preserved.

## Deployment

Production was updated from PR23a to PR23b.

Pre-deploy state:

```text
local HEAD: 0aa7545bab6e9ae5448b095f177406ed6b068017
origin/main: f7cd12018ce32a9b2e9753e4e93965cc3895a1d8
```

Deploy commands:

```bash
cd ~/apps/avito-watcher

git status --short
git branch --show-current
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main

git checkout main
git pull --ff-only origin main

docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Fast-forward result:

```text
Updating 0aa7545..f7cd120
Fast-forward
 alembic/env.py                                                |  26 +--
 app/admin.py                                                  |  56 +-----
 app/services/admin_auth.py                                    | 100 ++++++++++
 docs/admin_ui.md                                              |  24 ++-
 docs/handoff/pr23a_admin_audit_log_ledger_smoke_2026-06-15.md | 608 ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 tests/test_admin_access_control.py                            | 139 ++++++++++++++
 tests/test_admin_ui.py                                        |  27 +--
 7 files changed, 907 insertions(+), 73 deletions(-)
 create mode 100644 app/services/admin_auth.py
 create mode 100644 docs/handoff/pr23a_admin_audit_log_ledger_smoke_2026-06-15.md
 create mode 100644 tests/test_admin_access_control.py
```

Current production git state after deploy:

```text
f7cd120 (HEAD -> main, origin/main, origin/HEAD) Harden admin key-based access control (#210)
5b3174d Add PR23a production smoke handoff (#209)
0aa7545 Add admin audit log ledger (#208)
f11320d Add PR22b production smoke handoff (#207)
dc4de77 Add read-only retention dry-run report (#206)
```

Docker result:

```text
Image deploy-worker Built
Image deploy-app Built
Container deploy-postgres-1 Healthy
Container deploy-redis-1 Healthy
Container deploy-app-1 Started
Container deploy-worker-1 Started
```

## Health and Alembic

First health request after restart returned a transient connection reset while app was starting:

```text
curl: (56) Recv failure: Connection reset by peer
```

Immediate retry passed:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Alembic current:

```text
0017_admin_audit_events (head)
```

No new migration was added by PR23b.

## Initial smoke command mistakes

The first admin smoke attempt was invalid because shell variables were not loaded from `.env`:

```text
$ADMIN_UI_READ_KEY was empty in the shell
$ADMIN_UI_TECHNICAL_WRITE_KEY was empty in the shell
```

As a result, requests with:

```bash
-H "X-API-Key: $ADMIN_UI_READ_KEY"
```

sent an empty key and returned `403`.

There were also invalid route probes:

```text
POST /admin/alerts/retry -> 404
POST /admin/alerts/retry?admin_technical_write_key= -> 404
```

These were not valid PR23b smoke checks because the correct manual retry route is:

```text
POST /admin/alerts/delivery-attempts/{attempt_id}/retry
```

Important observation: invalid GET attempts did not create audit events.

```text
admin_audit_events count remained 1
```

## Correct key loading

Keys were then loaded from `.env` into shell variables without printing secret values:

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

The key values themselves were not printed.

## Read-only admin access smoke

Baseline before GET:

```sql
select count(*) as audit_before_get from admin_audit_events;
```

Result:

```text
audit_before_get = 1
```

Request:

```bash
curl -sS -o /tmp/pr23b_admin_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Result:

```text
200
```

The page rendered expected sections including:

- `Recent admin audit events`;
- `admin_audit_events` in Data volume summary;
- `0017_admin_audit_events` in Alembic section.

Audit after valid GET:

```text
audit_after_get = 1
```

Verdict:

```text
GET /admin/system with valid read key works ✅
GET /admin/system does not create audit events ✅
```

## Fail-closed read access smoke

Request without read key:

```bash
curl -sS -o /tmp/pr23b_admin_system_no_key.json -w "%{http_code}\n" \
  "http://127.0.0.1:8010/admin/system"
```

Result:

```text
403
{"detail":"Invalid admin key"}
```

Audit after invalid GET:

```text
audit_after_invalid_get = 1
```

Verdict:

```text
GET /admin/system without read key is denied ✅
Invalid/missing read-key GET does not create audit events ✅
```

## Technical POST / manual retry smoke

Baseline before POST:

```text
audit_before_post = 1
attempts_before_post = 415
alerts_sent_before_post = 3328
```

Request:

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

This is expected because technical ops are disabled in production.

Audit rows after POST:

```text
 id | actor_kind          | actor_label     | action               | target_type            | target_id | status  | request_method | request_path                               | metadata_json
----+---------------------+-----------------+----------------------+------------------------+-----------+---------+----------------+--------------------------------------------+----------------------------------------------------------------
  2 | admin_technical_key | technical_admin | alert_delivery_retry | alert_delivery_attempt | 140       | blocked | POST           | /admin/alerts/delivery-attempts/140/retry  | {"reason": "technical_ops_disabled", "source_attempt_id": 140}
  1 | admin_technical_key | technical_admin | alert_delivery_retry | alert_delivery_attempt | 140       | blocked | POST           | /admin/alerts/delivery-attempts/140/retry  | {"reason": "technical_ops_disabled", "source_attempt_id": 140}
```

Counters after POST:

```text
audit_after_post = 2
attempts_after_post = 415
alerts_sent_after_post = 3328
```

Verdict:

```text
Technical POST requires valid read key + technical form key ✅
Technical ops disabled still blocks request ✅
Blocked retry is audited ✅
No new alert_delivery_attempts created ✅
No new alerts_sent created ✅
PR23a blocked audit behavior preserved ✅
```

## Secret-safety checks

Audit table leak check:

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

Logs were checked without empty-regex expansion:

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

Result: no sensitive matches.

Verdict:

```text
No read key in logs ✅
No technical key in logs ✅
No raw Apps Script macro URL in logs ✅
No technical key query value in logs ✅
No X-API-Key / Authorization / Cookie material in logs ✅
No audit-table secret leak ✅
```

## Background worker note

During smoke, the worker continued running normal monitor cycles. This increased global delivery counters before the POST baseline:

```text
delivery attempts: 409 -> 415
alerts_sent: 3322 -> 3328
```

This was not caused by manual retry.

The relevant POST-local baseline proved no retry side effect:

```text
attempts_before_post = 415
attempts_after_post = 415
alerts_sent_before_post = 3328
alerts_sent_after_post = 3328
```

## Final production smoke verdict

```text
PR23b — Admin access control hardening ✅
Merged ✅
Deployed ✅
Production-smoked ✅
No migration ✅
Read access fail-closed ✅
Valid read access works ✅
GET no-audit preserved ✅
Technical POST boundary preserved ✅
Blocked retry audit preserved ✅
Technical ops disabled behavior preserved ✅
No delivery mutation on blocked retry ✅
No secret leak in audit ✅
No secret leak in logs ✅
```

## Follow-up recommendation

Next safe step should be documentation-only merge of this handoff.

After that, possible next roadmap options:

```text
PR23c — Admin audit read-only detail/page hardening
or
PR24a — Backtesting/outcome analytics operator read model hardening
```

Do not jump yet to:

- retention execution;
- autonomous agent actions;
- automatic scoring calibration;
- users/RBAC/login;
- agent-driven filter/scoring changes.
