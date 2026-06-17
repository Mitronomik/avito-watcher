# PR32 — Derived workflow state read API production smoke

Date: 2026-06-17
Environment: production
Repository: `Mitronomik/avito-watcher`

## Status

PR32 is merged, deployed, and production-smoked successfully.

```text
PR32 — Derived workflow state read API ✅
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
meta_contract_version=v1 preserved ✅
workflow_contract_version=workflow-state-v1 ✅
workflow_state_read=true ✅
workflow_actions_execute=false ✅
/api/admin/v1/listings OK ✅
/api/admin/v1/review-queue OK ✅
/api/admin/v1/listings/{id}/workflow OK ✅
/api/admin/v1/listings/{id}/decision-source includes workflow OK ✅
write/report actions implemented=false and available_now=false ✅
open_listing executable-style only when safe public listing URL exists ✅
query auth rejected ✅
missing/invalid key rejected ✅
validation/not_found behavior OK ✅
no DB/audit side effects for read endpoints ✅
no Decision Card / execution / raw JSON leak ✅
UI/API/log safety grep clean ✅
worker running ✅
Production smoke passed ✅
```

## Scope deployed

PR32 adds a read-only derived workflow state contract for Admin API v1.

Implemented in this PR:

```text
GET /api/admin/v1/listings/{listing_id}/workflow
```

Also extended:

```text
GET /api/admin/v1/listings/{listing_id}/decision-source
```

The decision-source response now includes the same workflow DTO under `workflow`.

PR32 does not implement write transitions, action execution, report generation, Decision Card v1, agents, parser runs, scoring recalculation, LLM calls, RAG calls, external retrieval, or migrations.

## Git state

Production was pulled forward from PR31 to PR32.

```text
Before: c9dfbac6d1da12515fc58df11bfe6f615cc91b97
After:  1d4508003a878ca702cdb86b260348e5f53e556f
```

Latest production log after pull:

```text
1d45080 Add derived workflow state read API (#230)
397f768 Add PR31 production smoke handoff (#229)
c9dfbac Add Admin API v1 listing and review queue reads (#228)
ce5f56c Add PR30 production smoke handoff (#227)
aeb6b03 Add Admin API v1 meta contract (#226)
8135754 Add PR29 production smoke handoff (#225)
0c41d93 Add Admin API v1 foundation (#224)
76cf831 Add PR28 production smoke handoff (#223)
c7596b7 Add deterministic sale and cap-rate evidence read model (#222)
72eeb51 Add PR27 production smoke handoff (#221)
```

Changed files pulled with PR32 and the PR31 handoff docs:

```text
app/api/admin_v1/listings.py
app/api/admin_v1/meta_contract.py
app/api/admin_v1/workflow.py
docs/admin_api_v1.md
docs/handoff/pr31_admin_api_v1_listing_review_queue_reads_smoke_2026-06-17.md
tests/test_admin_api_v1.py
tests/test_admin_api_v1_listings.py
tests/test_admin_api_v1_workflow.py
```

## Build and restart

Production compose config validated, app and worker images were rebuilt, and app/worker were restarted.

Result:

```text
Image deploy-app built ✅
Image deploy-worker built ✅
redis healthy ✅
postgres healthy ✅
worker started ✅
app started ✅
```

## Health and migration state

Initial health immediately after restart returned an empty reply once, consistent with previous warm-up behavior. Subsequent health smoke passed.

```text
GET /health -> 200 ✅
```

Alembic state:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

No migration was introduced by PR32.

## Auth setup

The production read key was loaded from the existing environment file into a shell variable without printing the value.

Observed key length:

```text
read_key_len=64
```

No key value was printed.

## Existing route smoke

Existing HTML/admin and Admin API v1 routes remained available.

```text
GET /health -> 200
GET /admin/system -> 200
GET /admin/listing-analyses -> 200
GET /admin/review-queue -> 200
GET /api/admin/v1/status -> 200
GET /api/admin/v1/meta -> 200
GET /api/admin/v1/listings?limit=5&offset=0 -> 200
GET /api/admin/v1/review-queue?limit=5&offset=0 -> 200
```

## Meta contract smoke

The PR30 meta contract remains compatible and PR32 workflow fields are present.

Confirmed:

```text
meta_contract_version = v1
workflow_contract_version = workflow-state-v1
workflow_state_read = true
workflow_actions_execute = false
workflow_state enum present
workflow_action enum present
```

Relevant capabilities observed:

```json
{
  "admin_api_v1": true,
  "read_api": true,
  "write_api": false,
  "technical_api_actions": false,
  "decision_card": false,
  "report_export": false,
  "workflow_state_read": true,
  "workflow_actions_execute": false
}
```

## Workflow endpoint smoke

The smoke test selected listing id `597` from the review queue/listings API.

```text
workflow_listing_id=597
```

Requests:

```text
GET /api/admin/v1/listings/597/workflow -> 200
GET /api/admin/v1/listings/597/decision-source -> 200
```

Workflow response summary:

```json
{
  "schema_version": "workflow-state-v1",
  "listing_id": 597,
  "listing_external_id": "8081430175",
  "workflow_state": "ready_for_work",
  "state_reasons": [
    "latest_analysis_verdict_strong"
  ],
  "source_refs": {
    "listing_id": 597,
    "listing_external_id": "8081430175",
    "listing_analysis_id": 677,
    "human_review_id": null
  },
  "limitations": [
    "derived_read_only_state",
    "write_transitions_not_implemented_in_pr32",
    "decision_card_not_implemented_in_pr32"
  ]
}
```

The selected listing had a latest successful analysis:

```json
{
  "id": 677,
  "status": "success",
  "profile": "commercial_rent",
  "score": 95.0,
  "verdict": "strong",
  "created_at": "2026-06-07T18:48:52.286191"
}
```

Human review was absent for the selected listing:

```json
{
  "human_review": null
}
```

The derived state was therefore:

```text
workflow_state = ready_for_work
state_reasons = [latest_analysis_verdict_strong]
```

## Decision-source integration smoke

The decision-source response includes the exact workflow DTO under `workflow` and preserves its normal PR31 structure:

```text
schema_version = decision-source-v1
listing section present
latest_analysis section present
human_review = null
workflow section present
available_sections.workflow = true
source_refs present
limitations present
```

The decision-source limitations are explicitly non-execution / non-decision-card:

```text
decision_card_not_implemented_in_pr32
write_transitions_not_implemented_in_pr32
action_execution_not_implemented_in_pr32
```

## Action semantics smoke

The workflow action contract was checked with a Python validation script.

Observed state:

```text
workflow_state: ready_for_work
state_reasons: ['latest_analysis_verdict_strong']
```

Observed action semantics:

```text
open_listing business_applicable=True implemented=True available_now=True requires_write_endpoint=False reason=listing_url_available
take_in_work business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
call_owner business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
watchlist business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
reject business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
generate_memo business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
generate_commercial_offer business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
export_report business_applicable=True implemented=False available_now=False requires_write_endpoint=True reason=requires_write_endpoint
request_data business_applicable=False implemented=False available_now=False requires_write_endpoint=True reason=state_not_ready
close business_applicable=False implemented=False available_now=False requires_write_endpoint=True reason=state_not_ready
```

Validation result:

```text
action semantics OK
```

This confirms the PR32 rule:

```text
open_listing is the only executable-style action when a safe public listing URL exists.
write/report actions remain implemented=false and available_now=false.
allowed_actions are metadata, not backend authorization.
```

## Negative auth and validation smoke

The new workflow endpoint keeps the existing Admin API v1 auth and error behavior.

Results:

```text
query auth -> 403 forbidden
missing key -> 403 forbidden
invalid key -> 403 forbidden
non-integer listing_id -> 422 validation_error
missing integer listing_id -> 404 JSON route
```

Observed validation envelope for non-integer `listing_id`:

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

The missing listing route returned `404` with JSON content type. Body output was not printed in the pasted terminal block, but the route remained stable and the application stayed healthy.

## Read-only side-effect check

Counts before workflow/decision-source/meta reads:

```text
listings_total = 2085
listing_analyses_total = 730
human_reviews_total = 0
market_evidence_items_total = 0
alerts_sent_total = 3990
agent_tasks_total = 2
admin_audit_events_total = 2
```

Read endpoints executed:

```text
GET /api/admin/v1/listings/597/workflow -> 200
GET /api/admin/v1/listings/597/decision-source -> 200
GET /api/admin/v1/meta -> 200
```

Counts after reads:

```text
listings_total = 2085
listing_analyses_total = 730
human_reviews_total = 0
market_evidence_items_total = 0
alerts_sent_total = 3990
agent_tasks_total = 2
admin_audit_events_total = 2
```

No DB side effects detected.

## Safety grep

API response grep for Decision Card, action execution, raw JSON, report content, and future UI fields produced only the expected PR32 limitation strings.

Expected matches:

```text
decision_card_not_implemented_in_pr32
```

No unexpected keys were observed for:

```text
primary_recommendation
recommendation
headline
top_reasons
top_risks
next_steps
missing_data
readiness
risk_severity
risk_visual
price_position
facts_json
result_json
payload_json
risks_json
questions_json
report_md
execution_endpoint
```

Secret and technical response grep was clean for workflow, decision-source, and meta responses.

HTML safety grep remained clean for existing admin pages.

Application/worker log grep was clean for tracebacks, errors, exception messages, auth header leaks, cookie leaks, technical key leaks, webhook leaks, and common secret identifiers.

## Runtime status

Container status after smoke:

```text
app: running, healthy
postgres: running, healthy
redis: running, healthy
worker: running
```

Worker log notes:

```text
PROXY_URLS not set — running without proxies (existing warning, not PR32-related)
LLM scorer calls succeeded after restart
```

No PR32-specific runtime errors were observed.

## Boundary confirmation

PR32 kept the intended architecture boundary:

```text
Deterministic derived workflow state only.
Action metadata only.
No action execution.
No write endpoints.
No report generation.
No Decision Card.
No score/verdict mutation.
No parser/agent/LLM/RAG/external retrieval from workflow routes.
No DB writes from read endpoints.
```

## Final result

```text
PR32 production smoke passed ✅
```
