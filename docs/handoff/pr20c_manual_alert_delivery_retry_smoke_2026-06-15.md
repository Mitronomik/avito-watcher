# PR20c — Manual alert delivery retry production smoke

Date: 2026-06-15  
Environment: `avito-watcher-prod`  
Repository: `Mitronomik/avito-watcher`  
PR: #192 — `Add manual retry for alert delivery attempts`  
Merge commit: `7e6bfe6496a5df216709affbfcb31283023c3c10`  
Smoke type: safe production deploy/smoke  

## Scope

PR20c added a controlled manual retry action for one alert delivery attempt:

- `POST /admin/alerts/delivery-attempts/{attempt_id}/retry`
- active form only on attempt detail page
- technical write key required
- typed confirmation required: `retry_delivery_attempt_{attempt_id}`
- retry allowed only for `failed`, `skipped`, or `unknown` attempts
- retry targets exactly the original channel
- matching `AlertSent` is checked before retry and again immediately before external send
- success creates `AlertSent`
- failed/skipped/unknown outcomes create only a new `AlertDeliveryAttempt`
- no automatic retry
- no scheduler
- no queue
- no migration
- no parser/scoring/agent/research/human-review changes

The production smoke deliberately did **not** execute a real retry, because real retry can send an external alert again. Technical operations remained disabled.

## Deployment commands

```bash
cd ~/apps/avito-watcher
git pull --ff-only origin main
git log -1 --oneline
```

Observed:

```text
Updating ef99fb0..7e6bfe6
Fast-forward
 app/admin.py           | 218 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++----
 docs/admin_ui.md       |  10 +++++
 docs/alert_delivery.md |  16 ++++++++
 tests/test_admin_ui.py | 188 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 425 insertions(+), 7 deletions(-)
7e6bfe6 (HEAD -> main, origin/main, origin/HEAD) Add manual retry for alert delivery attempts (#192)
```

## Alembic

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic heads

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current
```

Observed:

```text
0015_alert_delivery_attempts (head)
0015_alert_delivery_attempts (head)
```

Result:

```text
Alembic head/current unchanged ✅
No PR20c migration ✅
```

## App build and restart

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
curl -i http://127.0.0.1:8010/health
```

Build/restart observed:

```text
[+] build 1/1
 ✔ Image deploy-app Built
[+] up 3/3
 ✔ Container deploy-redis-1    Healthy
 ✔ Container deploy-postgres-1 Healthy
 ✔ Container deploy-app-1      Started
```

The first immediate health request returned:

```text
curl: (56) Recv failure: Connection reset by peer
```

This happened immediately after `up -d app` while the app was still starting. A retry succeeded.

Health retry:

```bash
for i in $(seq 1 20); do
  echo "try $i"
  curl -fsS http://127.0.0.1:8010/health && break
  sleep 2
done

curl -i http://127.0.0.1:8010/health

docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

Observed:

```text
try 1
{"status":"ok"}
HTTP/1.1 200 OK
date: Mon, 15 Jun 2026 11:35:22 GMT
server: uvicorn
content-length: 15
content-type: application/json

{"status":"ok"}
```

Container status:

```text
NAME                IMAGE           COMMAND                  SERVICE    CREATED              STATUS                        PORTS
deploy-app-1        deploy-app      "uvicorn app.main:ap…"   app        About a minute ago   Up About a minute (healthy)   127.0.0.1:8010->8000/tcp
deploy-postgres-1   postgres:16     "docker-entrypoint.s…"   postgres   8 hours ago          Up 8 hours (healthy)          5432/tcp
deploy-redis-1      redis:7         "docker-entrypoint.s…"   redis      2 weeks ago          Up 2 weeks (healthy)          6379/tcp
deploy-worker-1     deploy-worker   "python -m app.worke…"   worker     7 hours ago          Up 7 hours
```

Result:

```text
App rebuilt ✅
App restarted ✅
Health 200 ✅
App container healthy ✅
```

Note: only `app` was rebuilt/restarted. This is expected for PR20c safe smoke because the changed runtime behavior is the Admin UI retry route. No worker-side retry behavior is introduced by PR20c.

## App logs after restart

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs app --tail=200
```

Observed:

```text
app-1  | INFO:     Started server process [1]
app-1  | INFO:     Waiting for application startup.
app-1  | INFO:     Application startup complete.
app-1  | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
app-1  | INFO:     127.0.0.1:49490 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     127.0.0.1:48622 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     127.0.0.1:40790 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     127.0.0.1:34232 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     127.0.0.1:37754 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     172.18.0.1:51770 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     172.18.0.1:51774 - "GET /health HTTP/1.1" 200 OK
app-1  | INFO:     127.0.0.1:35556 - "GET /health HTTP/1.1" 200 OK
```

Result:

```text
No startup traceback ✅
Health checks logged as 200 ✅
```

## Admin settings check

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T app \
  python - <<'PY'
from app.core.config import settings
print("ADMIN_UI_ENABLED=", settings.admin_ui_enabled)
print("ADMIN_UI_ALLOW_QUERY_API_KEY=", settings.admin_ui_allow_query_api_key)
print("ADMIN_UI_TECHNICAL_OPS_ENABLED=", settings.admin_ui_technical_ops_enabled)
print("ADMIN_UI_READ_KEY set=", bool(settings.admin_ui_read_key))
print("ADMIN_UI_WRITE_KEY set=", bool(settings.admin_ui_write_key))
print("ADMIN_UI_TECHNICAL_WRITE_KEY set=", bool(settings.admin_ui_technical_write_key))
PY
```

Observed:

```text
ADMIN_UI_ENABLED= True
ADMIN_UI_ALLOW_QUERY_API_KEY= False
ADMIN_UI_TECHNICAL_OPS_ENABLED= False
ADMIN_UI_READ_KEY set= True
ADMIN_UI_WRITE_KEY set= True
ADMIN_UI_TECHNICAL_WRITE_KEY set= True
```

Result:

```text
Admin UI enabled ✅
Query API key propagation disabled ✅
Technical ops disabled ✅
Read/write/technical keys configured ✅
```

This is the expected safe configuration for PR20c smoke. Manual retry is deployed but inactive while technical ops are disabled.

## Admin delivery dashboard

Read key was loaded from `.env`:

```bash
ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"
```

Command:

```bash
curl -sS -o /tmp/pr20c_alerts.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/alerts"
```

Observed:

```text
200
```

Result:

```text
/admin/alerts accessible ✅
```

## Attempt detail page

Command:

```bash
ATTEMPT_ID="$(
  docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
    sh -lc 'psql -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select id
from alert_delivery_attempts
order by id desc
limit 1;
"'
)"

echo "ATTEMPT_ID=$ATTEMPT_ID"

curl -sS -o /tmp/pr20c_detail.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/alerts/delivery-attempts/$ATTEMPT_ID"
```

Observed:

```text
ATTEMPT_ID=187
200
```

The detail page showed:

```text
id = 187
listing_external_id = 8215179807
channel = google_sheets
dedupe_key = google_sheets:new:8215179807
payload_hash prefix = e052421a6cf0
status = success
attempt_count = 1
sent_at = 2026-06-15 11:34:19.481861
next_retry_at = —
search_job_id = —
search_name = —
error_type = —
last_error = —
matching AlertSent = yes
matching listing = 8215179807
```

Manual retry section showed:

```text
Ручной повтор доставки
Only failed, skipped, or unknown attempts can be retried.
```

Result:

```text
Attempt detail page accessible ✅
Latest attempt is success ✅
Matching AlertSent is present ✅
Success attempt is not retry-eligible ✅
Active retry form not shown for success attempt ✅
```

## Disabled retry POST check

Command:

```bash
curl -sS -o /tmp/pr20c_retry_disabled.html -w "%{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  -d "admin_technical_write_key=dummy" \
  -d "confirm_action=retry_delivery_attempt_${ATTEMPT_ID}" \
  "http://127.0.0.1:8010/admin/alerts/delivery-attempts/$ATTEMPT_ID/retry"
```

Observed:

```text
403
```

Result:

```text
POST retry while technical ops disabled returns 403 ✅
No real retry executed ✅
No external alert intentionally sent ✅
```

## Delivery table counts

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select count(*) as alert_delivery_attempts from alert_delivery_attempts;
select count(*) as alerts_sent from alerts_sent;
select status, channel, count(*)
from alert_delivery_attempts
group by status, channel
order by status, channel;
"'
```

Observed:

```text
 alert_delivery_attempts 
-------------------------
                     187
(1 row)

 alerts_sent 
-------------
        3100
(1 row)

 status  |    channel    | count 
---------+---------------+-------
 failed  | google_sheets |     3
 success | google_sheets |    92
 success | jsonl         |    92
(3 rows)
```

Result:

```text
Delivery attempts visible ✅
AlertSent count visible ✅
Production has 3 failed google_sheets attempts ✅
Failed attempts were not retried during safe smoke ✅
```

Important note: production now contains real failed attempts:

```text
failed | google_sheets | 3
```

These were deliberately not retried during this smoke. Retrying them would require an explicit operator decision, temporary enablement of `ADMIN_UI_TECHNICAL_OPS_ENABLED=true`, and immediate disablement after the controlled action.

## Secret/log check

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs app --tail=300 \
  | egrep -i 'traceback|exception|error|secret|token|api_key|password|authorization|webhook|telegram|retry' || true
```

Observed:

```text
app-1  | INFO:     172.18.0.1:53638 - "POST /admin/alerts/delivery-attempts/187/retry HTTP/1.1" 403 Forbidden
```

Result:

```text
No traceback ✅
No exception ✅
No raw secret leakage observed ✅
Only expected disabled retry 403 line ✅
```

## Final verdict

```text
PR20c production deploy/smoke: PASSED ✅
Manual retry route deployed ✅
Alembic unchanged at 0015 ✅
App healthy ✅
Admin delivery dashboard reachable ✅
Attempt detail reachable ✅
Success attempts are not retry-eligible ✅
Technical ops disabled ✅
Disabled retry POST returns 403 ✅
No real retry executed ✅
No external alert intentionally sent ✅
Logs clean ✅
```

## Follow-up

PR20c is deployed safely and ready for operational use, but manual retry should remain disabled by default.

Potential future controlled action:

```text
There are 3 failed google_sheets attempts in production.
```

If manual retry is needed, do it as a separate controlled operator action:

1. Snapshot `alert_delivery_attempts` and `alerts_sent` counts.
2. Temporarily enable `ADMIN_UI_TECHNICAL_OPS_ENABLED=true`.
3. Retry exactly one failed attempt from the detail page.
4. Confirm exactly one new delivery attempt row is created.
5. Confirm `AlertSent` is created only if retry succeeds.
6. Confirm no other channels were retried.
7. Disable technical ops immediately.
8. Recheck invariant counters and logs.
