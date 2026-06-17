# PR31 — Admin API v1 listing and review queue reads production smoke

Date: 2026-06-17
Environment: production
Host: avito-watcher-prod
Scope: production deployment and smoke verification for PR31

## PR

PR:

```text
#228 — Add Admin API v1 listing and review queue reads
```

Merged commit deployed to production:

```text
c9dfbac6d1da12515fc58df11bfe6f615cc91b97
```

Previous production HEAD before pull:

```text
aeb6b03f5b9abc5e4274249682c92917d141378e
```

Pull result:

```text
Fast-forward
```

Top production log after pull:

```text
c9dfbac Add Admin API v1 listing and review queue reads (#228)
ce5f56c Add PR30 production smoke handoff (#227)
aeb6b03 Add Admin API v1 meta contract (#226)
8135754 Add PR29 production smoke handoff (#225)
0c41d93 Add Admin API v1 foundation (#224)
76cf831 Add PR28 production smoke handoff (#223)
c7596b7 Add deterministic sale and cap-rate evidence read model (#222)
72eeb51 Add PR27 production smoke handoff (#221)
```

Changed files from pull:

```text
app/api/admin_v1/listing_dtos.py
app/api/admin_v1/listings.py
app/api/admin_v1/review_queue.py
app/api/admin_v1/routes.py
app/services/human_review_queue.py
docs/admin_api_v1.md
docs/handoff/pr30_admin_api_v1_meta_contract_smoke_2026-06-17.md
tests/test_admin_api_v1.py
tests/test_admin_api_v1_listings.py
```

Summary:

```text
9 files changed, 988 insertions(+), 7 deletions(-)
```

No migration file was added.

## Build and restart

Compose config validation passed.

Build completed:

```text
Image deploy-worker Built
Image deploy-app Built
```

Restart completed:

```text
Container deploy-redis-1    Healthy
Container deploy-postgres-1 Healthy
Container deploy-app-1      Started
Container deploy-worker-1   Started
```

## Health and migrations

Health endpoint:

```http
HTTP/1.1 200 OK
```

Body:

```json
{"status":"ok"}
```

Alembic current/head:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

Result:

```text
No migration required.
Alembic unchanged.
```

## Authentication setup

The existing admin read key was loaded from production configuration with expected length:

```text
read_key_len=64
```

No key value was printed.

## Existing route smoke

Existing health and admin HTML/API routes remained available:

```text
GET /health                         -> 200
GET /admin/system                   -> 200
GET /admin/listing-analyses         -> 200
GET /admin/review-queue             -> 200
GET /api/admin/v1/status            -> 200
GET /api/admin/v1/meta              -> 200
```

PR30 meta contract remained available and included:

```text
meta_contract_version = v1
```

## New PR31 endpoint smoke

New read-only Admin API v1 endpoints returned success:

```text
GET /api/admin/v1/listings?limit=5&offset=0                                      -> 200
GET /api/admin/v1/listings?limit=5&offset=0&order_by=last_seen_at&order_dir=desc  -> 200
GET /api/admin/v1/review-queue?limit=5&offset=0                                  -> 200
GET /api/admin/v1/review-queue?limit=5&offset=0&order_by=score&order_dir=desc     -> 200
```

A listing id was extracted from the listing list response:

```text
listing_id=1969
```

Detail and decision-source endpoints returned success:

```text
GET /api/admin/v1/listings/1969                  -> 200
GET /api/admin/v1/listings/1969/decision-source  -> 200
```

## Listing list response shape

The listing list endpoint returned the Admin API v1 envelope:

```json
{
  "ok": true,
  "data": {
    "schema_version": "listing-list-v1",
    "items": []
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "...",
    "pagination": {
      "limit": 5,
      "offset": 0,
      "has_more": true
    }
  }
}
```

Observed first response items contained allowlisted listing summary fields only:

```text
schema_version
id
external_id
url
title
price
area_m2
address
rooms
is_active
published_label
published_at
first_seen_at
last_seen_at
latest_analysis
```

Example first listing summary:

```text
id: 1969
external_id: 8269761386
title: Помещение 44.9 м² в Мультиплейсе Пламя
price: 53880.0
area_m2: 44.9
latest_analysis: null
```

## Review queue response shape

The review queue endpoint returned the Admin API v1 envelope:

```json
{
  "ok": true,
  "data": {
    "schema_version": "review-queue-v1",
    "items": []
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "...",
    "pagination": {
      "limit": 5,
      "offset": 0,
      "has_more": true
    }
  }
}
```

Observed review queue items contained allowlisted sections:

```text
listing
analysis
review
```

Observed first review queue item:

```text
listing.id: 597
listing.external_id: 8081430175
analysis.id: 677
analysis.profile: commercial_rent
analysis.status: success
analysis.score: 95.0
analysis.verdict: strong
review.queue_status: needs_review
review.latest_human_verdict: null
review.reviewed_at: null
```

## Listing detail response shape

The listing detail endpoint returned:

```json
{
  "ok": true,
  "data": {
    "schema_version": "listing-detail-v1",
    "id": 1969,
    "latest_analysis": null,
    "latest_human_review": null,
    "alert_summary": null
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "..."
  }
}
```

The response stayed within the PR31 listing-detail DTO boundary.

## Decision-source response shape

The decision-source endpoint returned:

```json
{
  "ok": true,
  "data": {
    "schema_version": "decision-source-v1",
    "listing": {},
    "latest_analysis": null,
    "human_review": null,
    "available_sections": {
      "listing": true,
      "analysis": false,
      "market_facts": false,
      "human_review": false,
      "alerts": false
    },
    "source_refs": {
      "listing_id": 1969,
      "listing_external_id": "8269761386",
      "listing_analysis_id": null,
      "human_review_id": null
    },
    "limitations": [
      "decision_card_not_implemented_in_pr31",
      "workflow_state_not_implemented_in_pr31",
      "allowed_actions_not_implemented_in_pr31"
    ]
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "..."
  }
}
```

Result:

```text
Decision-source is a safe source bundle.
It does not implement decision card.
It does not implement workflow state.
It does not implement allowed actions.
```

## Response shape checks

Schema version checks passed:

```text
listing-list-v1
review-queue-v1
listing-detail-v1
decision-source-v1
```

Decision-source limitation markers were present as expected:

```text
decision_card_not_implemented_in_pr31
workflow_state_not_implemented_in_pr31
allowed_actions_not_implemented_in_pr31
```

These markers are intentional limitations, not leaked implemented sections.

## Negative auth and validation smoke

Query-string auth attempt was rejected:

```http
HTTP/1.1 403 Forbidden
```

Missing key was rejected:

```http
HTTP/1.1 403 Forbidden
```

Invalid key was rejected:

```http
HTTP/1.1 403 Forbidden
```

Too-large listing pagination limit returned API error envelope:

```http
HTTP/1.1 400 Bad Request
```

```json
{
  "ok": false,
  "error": {
    "code": "pagination_limit_exceeded",
    "message": "pagination limit exceeded",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

Unknown listing order field returned API error envelope:

```http
HTTP/1.1 422 Unprocessable Entity
```

```json
{
  "ok": false,
  "error": {
    "code": "validation_error",
    "message": "unknown order field",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

Unknown review queue order field returned API error envelope:

```http
HTTP/1.1 422 Unprocessable Entity
```

Non-integer listing id returned validation error envelope:

```http
HTTP/1.1 422 Unprocessable Entity
```

```json
{
  "ok": false,
  "error": {
    "code": "validation_error",
    "message": "Validation error",
    "details": [
      {
        "type": "int_parsing",
        "loc": ["path", "listing_id"],
        "msg": "Input should be a valid integer, unable to parse string as an integer",
        "input": "not-int"
      }
    ]
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

Unknown listing id returned not-found envelope:

```json
{
  "ok": false,
  "error": {
    "code": "not_found",
    "message": "Listing not found",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

## No side effects check

Database counts before representative read requests:

```text
listing_analyses_total:       730
human_reviews_total:          0
market_evidence_items_total:  0
alerts_sent_total:            3884
agent_tasks_total:            2
admin_audit_events_total:     2
```

Representative read requests executed:

```text
GET /api/admin/v1/listings?limit=5&offset=0                   -> 200
GET /api/admin/v1/review-queue?limit=5&offset=0               -> 200
GET /api/admin/v1/listings/1969                               -> 200
GET /api/admin/v1/listings/1969/decision-source               -> 200
```

Database counts after representative read requests:

```text
listing_analyses_total:       730
human_reviews_total:          0
market_evidence_items_total:  0
alerts_sent_total:            3884
agent_tasks_total:            2
admin_audit_events_total:     2
```

Result:

```text
No DB side effects from PR31 read endpoints.
No audit side effects from PR31 read endpoints.
No human review writes.
No agent task creation.
No alert sent writes.
No evidence writes.
No analysis writes.
```

## Safety grep

API grep for raw analysis/debug/report/action fields:

```text
Only expected decision-source limitation markers matched.
No raw JSON fields or implemented decision-card/workflow/actions leaked.
```

Credential/header/secret marker grep over PR31 API responses:

```text
No matches.
```

Admin HTML safety grep:

```text
No matches.
```

App/worker logs grep:

```text
No traceback or runtime error found.
No sensitive value found.
```

Known benign worker log entries matched broad patterns because counters include words such as `engine_error_count: 0`.

## Containers and worker

Container status after deploy:

```text
deploy-app-1        Up, healthy
deploy-postgres-1   Up, healthy
deploy-redis-1      Up, healthy
deploy-worker-1     Up
```

Worker logs showed normal monitor cycles:

```text
monitor cycle completed
searches_processed=0
```

Known production warning remains:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This is an existing production configuration warning, not a PR31 regression.

Worker diagnostics still include boolean runtime flags. No secret value was printed.

## Production caveats

Current production data state affects what the new endpoints display:

```text
Many newest listings have latest_analysis = null.
Review queue currently contains older analyzed rows with successful analyses.
market_evidence_items_total = 0.
human_reviews_total = 0.
```

These are data-state caveats, not PR31 runtime failures.

## Final smoke result

```text
PR31 — Listings / review queue read API ✅
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
/api/admin/v1/listings OK ✅
/api/admin/v1/listings ordering/pagination OK ✅
/api/admin/v1/listings/{id} OK ✅
/api/admin/v1/listings/{id}/decision-source OK ✅
/api/admin/v1/review-queue OK ✅
/api/admin/v1/review-queue ordering/pagination OK ✅
query auth rejected ✅
missing/invalid key rejected ✅
validation/not_found envelopes OK ✅
no DB/audit side effects for read endpoints ✅
no raw JSON / decision-card / workflow/actions leak ✅
UI/API/log safety grep clean ✅
worker running ✅
Production smoke passed ✅
```

## Handoff conclusion

PR31 is production-smoked successfully.

The system now has the first decision-ready read-only Admin API v1 domain endpoints for:

```text
listing list
listing detail
review queue
decision-source snapshot
```

The API remains read-only and does not yet implement:

```text
decision card
workflow state
allowed actions
human review write API
technical actions
report/export API
agent/evidence/system read APIs
```
