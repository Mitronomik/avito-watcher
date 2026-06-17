# PR29 — Admin API v1 foundation production smoke

Date: 2026-06-17  
Environment: production (`avito-watcher-prod`)  
Repository: `Mitronomik/avito-watcher`  
Production path: `~/apps/avito-watcher`  
Compose file: `deploy/docker-compose.prod.yml`  
Branch: `main`  
Smoke status: passed

## Scope

PR29 adds the read-only Admin API v1 foundation under:

```text
/api/admin/v1
```

PR29 is an API foundation PR only.

It adds:

- `/api/admin/v1/status`;
- `/api/admin/v1/meta`;
- Admin API v1 response envelope;
- Admin API v1 scoped error envelope for API HTTP / validation errors;
- read-auth dependency that reuses existing PR23b centralized admin read key validation;
- response-boundary redaction helper;
- bounded pagination helper;
- allowlisted ordering helper;
- documentation in `docs/admin_api_v1.md`;
- tests in `tests/test_admin_api_v1.py`.

PR29 intentionally does not add:

- UI;
- frontend;
- listing/review/evidence domain endpoints;
- decision cards;
- workflow state;
- allowed actions;
- technical operations;
- run-once;
- delivery retry;
- search edit/pause/resume;
- scoring changes;
- alert changes;
- parser changes;
- market evidence changes;
- source quality changes;
- sale/cap-rate model changes;
- agent/orchestration changes;
- CORS changes;
- new auth transport;
- query/cookie/Bearer auth;
- public test-only endpoints;
- DB migration.

## Repository update

Production was updated from PR28 to PR29.

Before pull:

```text
HEAD:        c7596b7260d37f9f4aa402d504a4ad0d20267c79
origin/main: 0c41d936aedc253463002f41a325165d6148f68b
```

Pull result:

```text
Updating c7596b7..0c41d93
Fast-forward
```

After pull:

```text
0c41d93 (HEAD -> main, origin/main, origin/HEAD) Add Admin API v1 foundation (#224)
76cf831 Add PR28 production smoke handoff (#223)
c7596b7 Add deterministic sale and cap-rate evidence read model (#222)
72eeb51 Add PR27 production smoke handoff (#221)
48c523e Add deterministic source quality discipline v0 (#220)
b6ad8e5 Add PR26 production smoke handoff (#219)
0729cd7 Add PR25 production smoke handoff (#217)
81e1f65 Add deterministic adjusted comparable model v0 (#218)
```

Changed files pulled to production:

```text
app/api/admin_v1/__init__.py
app/api/admin_v1/dependencies.py
app/api/admin_v1/ordering.py
app/api/admin_v1/pagination.py
app/api/admin_v1/redaction.py
app/api/admin_v1/routes.py
app/api/admin_v1/schemas.py
app/main.py
docs/admin_api_v1.md
docs/handoff/pr28_sale_cap_rate_evidence_v0_smoke_2026-06-17.md
tests/test_admin_api_v1.py
```

No migration files were added.

## Build and restart

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Image deploy-app Built
Image deploy-worker Built
Container deploy-postgres-1 Healthy
Container deploy-redis-1 Healthy
Container deploy-app-1 Started
Container deploy-worker-1 Started
```

## Health check

Initial `/health` check was executed immediately after restart and returned:

```text
curl: (52) Empty reply from server
```

The app was still starting. A later repeat check succeeded.

Repeat command:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```text
HTTP/1.1 200 OK
server: uvicorn
content-length: 15
content-type: application/json
```

The response body was not displayed in the captured terminal output, but the route returned HTTP 200 and subsequent admin/API calls succeeded.

## Alembic

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec app \
  sh -lc 'alembic current'

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec app \
  sh -lc 'alembic heads'
```

Result:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

Verdict:

```text
No migration.
Alembic unchanged.
One head.
```

## Existing HTML admin smoke

Read key was loaded from `.env` and was not printed.

Only length was checked:

```text
read_key_len=64
```

Commands:

```bash
curl -sS -o /tmp/pr29_admin_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

curl -sS -o /tmp/pr29_admin_analyses.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/listing-analyses"

curl -sS -o /tmp/pr29_admin_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Result:

```text
200
200
200
```

Verdict:

```text
Existing /admin HTML routes remain compatible.
```

## Admin API v1 smoke

### GET /api/admin/v1/status

Command:

```bash
curl -sS -i \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/api/admin/v1/status"
```

Result:

```http
HTTP/1.1 200 OK
content-type: application/json
```

Response:

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "service": "avito-watcher",
    "api": "admin-v1"
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T05:19:44.325660+00:00"
  }
}
```

### GET /api/admin/v1/meta

Command:

```bash
curl -sS -i \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/api/admin/v1/meta"
```

Result:

```http
HTTP/1.1 200 OK
content-type: application/json
```

Response:

```json
{
  "ok": true,
  "data": {
    "api_version": "admin-v1",
    "service": "avito-watcher",
    "status": "ok"
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T05:19:44.338460+00:00"
  }
}
```

Verified:

```text
/meta is minimal smoke/self-description only.
No capabilities field.
No technical_actions field.
No domain_endpoints field.
No PR30 permission/enum/label/role/error registry.
```

## Auth smoke

### Query auth rejected

Command:

```bash
curl -sS -i \
  "http://127.0.0.1:8010/api/admin/v1/status?api_key=$ADMIN_READ_KEY"
```

Result:

```http
HTTP/1.1 403 Forbidden
content-type: application/json
```

Response:

```json
{
  "ok": false,
  "error": {
    "code": "forbidden",
    "message": "Invalid admin key",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

Verdict:

```text
Query-string auth is not accepted by Admin API v1.
```

### Missing key rejected

Command:

```bash
curl -sS -i \
  "http://127.0.0.1:8010/api/admin/v1/status"
```

Result:

```http
HTTP/1.1 403 Forbidden
content-type: application/json
```

Response:

```json
{
  "ok": false,
  "error": {
    "code": "forbidden",
    "message": "Invalid admin key",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

### Invalid key rejected

Command:

```bash
curl -sS -i \
  -H "X-API-Key: invalid" \
  "http://127.0.0.1:8010/api/admin/v1/status"
```

Result:

```http
HTTP/1.1 403 Forbidden
content-type: application/json
```

Response:

```json
{
  "ok": false,
  "error": {
    "code": "forbidden",
    "message": "Invalid admin key",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

Verdict:

```text
Missing/invalid key returns scoped Admin API v1 JSON error envelope.
```

## UI safety grep

Command:

```bash
grep -Ei "payload_json|result_json|raw evidence_json|admin_technical_write_key|Authorization:|Cookie:|X-API-Key:|script\.google\.com/macros/s/|capabilities|technical_actions|domain_endpoints|permissions|role_matrix|workflow_actions" \
  /tmp/pr29_admin_system.html /tmp/pr29_admin_analyses.html /tmp/pr29_admin_review_queue.html || true
```

Result:

```text
(no output)
```

Verdict:

```text
Existing HTML admin smoke artifacts did not expose raw payload/result JSON, technical keys, auth headers, Apps Script URL, or PR30/capability-style terms.
```

## Runtime log safety grep

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=450 app worker \
  | grep -Ei "Traceback|ERROR|Exception|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros|CORS|Bearer|capabilities|technical_actions|domain_endpoints" || true
```

Result contained only benign worker cycle logs where `engine_error_count=0` and `engine_errors=0` appear inside normal stats:

```text
worker-1 | avito_parser.end_cycle stats={... 'engine_error_count': 0 ...}
worker-1 | monitor_service.cycle_summary ... engine_errors=0 ...
```

No Traceback, ERROR, Exception, secrets, auth headers, CORS/Bearer, Apps Script URL, or capability-style leakage was found.

Known environment warning remains:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning is unrelated to PR29.

## DB / audit side-effect check

Counts before valid `/status` + `/meta` calls:

```text
listing_analyses_total:      730
market_evidence_items_total: 0
alerts_sent_total:           3730
agent_tasks_total:           2
admin_audit_events_total:    2
```

Valid API calls:

```bash
curl -sS -o /tmp/pr29_api_status.json -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/api/admin/v1/status"

curl -sS -o /tmp/pr29_api_meta.json -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/api/admin/v1/meta"
```

Result:

```text
200
200
```

Counts after valid `/status` + `/meta` calls:

```text
listing_analyses_total:      730
market_evidence_items_total: 0
alerts_sent_total:           3730
agent_tasks_total:           2
admin_audit_events_total:    2
```

Verdict:

```text
No DB/audit side effects for valid /api/admin/v1/status and /api/admin/v1/meta calls.
```

## Container status

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

Result:

```text
deploy-app-1        Up About a minute (healthy)   127.0.0.1:8010->8000/tcp
deploy-postgres-1   Up 2 days (healthy)           5432/tcp
deploy-redis-1      Up 2 weeks (healthy)          6379/tcp
deploy-worker-1     Up About a minute
```

Worker logs show monitor cycles completed:

```text
monitor cycle completed
```

## Production smoke verdict

```text
PR29 — Admin API v1 foundation ✅
Merged ✅
Pulled to production ✅
Built app + worker ✅
Restarted app + worker ✅
Health OK ✅
No migration ✅
Alembic unchanged ✅
Existing /admin HTML routes OK ✅
/api/admin/v1/status OK ✅
/api/admin/v1/meta OK ✅
/meta minimal and PR30-free ✅
Query auth rejected ✅
Missing/invalid key returns JSON API error envelope ✅
No DB/audit side effects for valid status/meta ✅
UI/log safety grep clean ✅
Worker running ✅
Production smoke passed ✅
```

## Notes and limitations

- PR29 intentionally does not add listing/review/evidence APIs.
- PR29 intentionally does not add permissions registry, enum registry, label registry, role matrix, or full error catalog.
- Those belong to PR30.
- PR29 intentionally does not add technical actions.
- PR29 intentionally does not alter scoring, alerts, parser, evidence, agents, or orchestration.
- No migration was required.
- The initial `/health` empty reply was a startup timing artifact; repeated health check and all admin/API checks passed.
- `PROXY_URLS not set` remains a known environment warning unrelated to PR29.
- Server still previously reported `System restart required` and zombie processes; this is maintenance work, not PR29 scope.

## Next step

After this handoff is merged, proceed to:

```text
PR30 — Meta contract: permissions, enums, labels and errors
```

PR30 should implement the registry/capability/label/error contract that PR29 deliberately avoided.
