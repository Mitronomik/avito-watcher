# PR30 — Admin API v1 meta contract production smoke handoff

Date: 2026-06-17

Status: merged, deployed to production, smoked successfully.

## PR

- PR: #226 — `Add Admin API v1 meta contract`
- Merge commit deployed to production: `aeb6b03f5b9abc5e4274249682c92917d141378e`
- Previous production HEAD before pull: `0c41d936aedc253463002f41a325165d6148f68b`
- Base handoff dependency: PR29 production smoke handoff was already merged as `8135754`.

## Purpose

PR30 extends the Admin API v1 `/meta` endpoint from the PR29 minimal smoke/self-description response into a stable read-only frontend metadata contract.

The contract provides static display metadata for future UI clients:

- `meta_contract_version`
- frontend personas / role ids
- permission metadata
- enum registry grounded in existing code constants
- RU/EN labels
- legacy label overrides
- stable public error catalog
- contract-level capabilities

## Architecture boundaries preserved

PR30 is metadata-only.

It does not add:

- UI / frontend
- write endpoints
- technical API actions
- decision cards
- report export
- listing/review/evidence domain data endpoints
- parser changes
- scoring changes
- alert logic changes
- agent changes
- migrations
- DB/session dependency for `/meta`
- new auth transport
- CORS changes

Important invariant:

```text
Permissions metadata is not backend authorization.
Frontend may use permissions to hide/show controls.
Backend must still enforce authorization independently on every endpoint.
PR30 does not activate write permissions because write endpoints do not exist yet.
```

## Production deploy evidence

Production path:

```bash
~/apps/avito-watcher
```

Observed pull result:

```text
From github.com:Mitronomik/avito-watcher
 * branch            main       -> FETCH_HEAD
   0c41d93..aeb6b03  main       -> origin/main
0c41d936aedc253463002f41a325165d6148f68b
aeb6b03f5b9abc5e4274249682c92917d141378e
Updating 0c41d93..aeb6b03
Fast-forward
```

Top of production log after pull:

```text
aeb6b03 (HEAD -> main, origin/main, origin/HEAD) Add Admin API v1 meta contract (#226)
8135754 Add PR29 production smoke handoff (#225)
0c41d93 Add Admin API v1 foundation (#224)
76cf831 Add PR28 production smoke handoff (#223)
c7596b7 Add deterministic sale and cap-rate evidence read model (#222)
72eeb51 Add PR27 production smoke handoff (#221)
48c523e Add deterministic source quality discipline v0 (#220)
b6ad8e5 Add PR26 production smoke handoff (#219)
```

Changed files pulled:

```text
app/api/admin_v1/meta_contract.py
app/api/admin_v1/routes.py
docs/admin_api_v1.md
docs/handoff/pr29_admin_api_v1_foundation_smoke_2026-06-17.md
tests/test_admin_api_v1.py
```

No migrations were pulled.

## Build and restart

Production build/restart completed successfully for `app` and `worker`.

Observed:

```text
[+] build 2/2
 ✔ Image deploy-app    Built
 ✔ Image deploy-worker Built
[+] up 4/4
 ✔ Container deploy-redis-1    Healthy
 ✔ Container deploy-postgres-1 Healthy
 ✔ Container deploy-app-1      Started
 ✔ Container deploy-worker-1   Started
```

## Health and migration state

Observed:

```text
HTTP/1.1 200 OK
{"status":"ok"}
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

Result:

```text
Health OK ✅
No migration ✅
Alembic unchanged ✅
```

## Admin read key handling

The configured read key was loaded for smoke testing without printing the key value.

Observed:

```text
read_key_len=64
```

## Existing `/admin` HTML smoke

The existing server-rendered admin pages were checked with the configured read key.

Observed:

```text
/admin/system            200
/admin/listing-analyses  200
/admin/review-queue      200
```

Result:

```text
Existing /admin HTML routes OK ✅
```

## Admin API v1 smoke

The Admin API v1 status and meta endpoints were checked with the configured read key.

Observed:

```text
/api/admin/v1/status  200
/api/admin/v1/meta    200
```

Result:

```text
/api/admin/v1/status OK ✅
/api/admin/v1/meta OK ✅
```

## `/meta` contract checks

Observed `/api/admin/v1/meta` envelope starts with:

```json
{
  "ok": true,
  "data": {
    "api_version": "admin-v1",
    "meta_contract_version": "v1",
    "service": "avito-watcher",
    "status": "ok",
    "roles": [],
    "permissions": {},
    "enums": {},
    "labels": {},
    "legacy_labels": {},
    "errors": {},
    "capabilities": {}
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T08:56:43.799131+00:00"
  }
}
```

Confirmed contract fields:

```text
meta_contract_version=v1 ✅
roles present ✅
permissions present ✅
enums present ✅
labels present ✅
legacy_labels present ✅
errors present ✅
capabilities present ✅
```

Confirmed capabilities:

```json
{
  "admin_api_v1": true,
  "read_api": true,
  "write_api": false,
  "technical_api_actions": false,
  "decision_card": false,
  "report_export": false
}
```

Result:

```text
capabilities are contract-level only ✅
write_api=false ✅
technical_api_actions=false ✅
```

Confirmed old ambiguous capability fields were absent:

```text
technical_actions absent ✅
domain_endpoints absent ✅
```

Confirmed safe legacy label:

```json
"legacy_labels": {
  "sent_to_expert": {
    "ru": "Сформировать экспертное заключение системы",
    "en": "Prepare system expert memo"
  }
}
```

Result:

```text
legacy sent_to_expert label safe ✅
unsafe legacy expert wording absent ✅
```

Confirmed enum unknown fallback exists:

```json
"unknown_value": {
  "label": {
    "ru": "Неизвестно",
    "en": "Unknown"
  },
  "display": "fallback"
}
```

Result:

```text
unknown enum fallback exists ✅
```

## Auth safety

Query-string auth against `/api/admin/v1/meta` was rejected.

Observed:

```text
HTTP/1.1 403 Forbidden
{"ok":false,"error":{"code":"forbidden","message":"Invalid admin key","details":null},"meta":{"api_version":"admin-v1"}}
```

Missing and invalid read credentials were also rejected with the scoped JSON API error envelope.

Result:

```text
Query auth rejected ✅
Missing key rejected ✅
Invalid key rejected ✅
JSON API error envelope preserved ✅
```

## No DB/audit side effects for valid `/status` + `/meta`

Before valid status/meta smoke:

```text
listing_analyses_total: 730
market_evidence_items_total: 0
alerts_sent_total: 3828
agent_tasks_total: 2
admin_audit_events_total: 2
```

After valid status/meta smoke:

```text
listing_analyses_total: 730
market_evidence_items_total: 0
alerts_sent_total: 3828
agent_tasks_total: 2
admin_audit_events_total: 2
```

Result:

```text
No listing analysis side effect ✅
No market evidence side effect ✅
No alert side effect from status/meta ✅
No agent task side effect ✅
No admin audit side effect ✅
```

Note: `alerts_sent_total` was already `3828` before the explicit before/after status/meta check. It remained unchanged across the valid `/status` and `/meta` calls. This confirms PR30 API smoke did not create alert rows.

## Safety grep

Safety checks were run against:

- rendered HTML admin responses
- `/api/admin/v1/meta` response
- recent app/worker logs

The checks searched for raw sensitive markers, credential transport markers, known webhook/key/DSN/token names, stack traces, and exception markers.

Observed:

```text
HTML admin safety grep: clean
API meta safety grep: clean
Logs safety grep: clean for requested patterns
```

Result:

```text
Existing HTML admin safety grep clean ✅
API meta safety grep clean ✅
Logs safety grep clean for requested patterns ✅
```

## Runtime status

Observed containers:

```text
deploy-app-1        Up 2 minutes (healthy)   127.0.0.1:8010->8000/tcp
deploy-postgres-1   Up 2 days (healthy)      5432/tcp
deploy-redis-1      Up 2 weeks (healthy)     6379/tcp
deploy-worker-1     Up 2 minutes
```

Worker logs show normal monitor cycles:

```text
monitor cycle completed
searches_processed=0
```

Known existing environment warning remains:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning predates PR30 and is not a PR30 regression.

Worker diagnostics still include boolean configuration presence fields. The API response and admin HTML safety checks did not expose secrets. These boolean diagnostics are a pre-existing logging practice and may be addressed in a future hardening PR if desired.

## Final production smoke result

```text
PR30 — Admin API v1 meta contract ✅
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
meta_contract_version=v1 ✅
capabilities are contract-level only ✅
write_api=false ✅
technical_api_actions=false ✅
legacy sent_to_expert label safe ✅
query auth rejected ✅
missing/invalid key rejected ✅
no DB/audit side effects for valid status/meta ✅
UI/API safety grep clean ✅
logs have no errors/secrets from requested grep ✅
worker running ✅
Production smoke passed ✅
```

## Next step

After this handoff is merged, continue with the next roadmap step.

Recommended next PR:

```text
PR31 — Admin API v1 read model: review queue / listing analysis summary endpoint
```

Rationale: PR29 created the API foundation; PR30 created the frontend metadata contract. The next useful backend step is the first decision-ready read endpoint that a future UI can consume, still read-only and still without technical actions or scoring changes.
