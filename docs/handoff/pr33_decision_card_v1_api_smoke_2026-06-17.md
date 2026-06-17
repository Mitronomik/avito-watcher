# PR33 — Deterministic Decision Card v1 API production smoke handoff

**Date:** 2026-06-17  
**Environment:** production, `avito-watcher-prod`  
**Repository:** `Mitronomik/avito-watcher`  
**PR:** #232 — `Add deterministic Decision Card v1 API`  
**Production commit:** `7910e70 Add deterministic Decision Card v1 API (#232)`  
**Previous production commit before pull:** `1d45080 Add derived workflow state read API (#230)`  
**Smoke result:** passed

---

## Summary

PR33 was merged, pulled to production, built, restarted, and smoke-tested successfully.

PR33 adds a deterministic, read-only Decision Card v1 API:

```text
GET /api/admin/v1/listings/{listing_id}/decision-card
```

It also extends existing decision-source responses with compact Decision Card availability/reference metadata:

```text
available_sections.decision_card = true
decision_card_ref.route_name = admin_api_v1_decision_card
decision_card_ref.schema_version = decision-card-v1
```

The implementation remains within the intended PR33 boundary:

- deterministic backend DTO only;
- internal workflow recommendation only;
- no investment advice;
- no certified appraisal;
- no valuation report;
- no LLM/agent/RAG/external calls from the read API;
- no action execution;
- no report/memo/offer generation;
- no risk visual severity;
- no readiness checklist;
- no price position DTO;
- no score/verdict mutation;
- no database writes from read endpoints;
- no migration.

---

## Git pull evidence

Production was updated from PR32 to PR33:

```text
CURRENT_HEAD=1d4508003a878ca702cdb86b260348e5f53e556f
ORIGIN_MAIN=7910e70b908e21fc6f4aa4111f7b36c45843ce37
Updating 1d45080..7910e70
Fast-forward
```

Top of `git log --oneline -12` after pull:

```text
7910e70 (HEAD -> main, origin/main, origin/HEAD) Add deterministic Decision Card v1 API (#232)
303114a Add PR32 production smoke handoff (#231)
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

Files changed by pull:

```text
app/api/admin_v1/decision_card.py
app/api/admin_v1/listings.py
app/api/admin_v1/meta_contract.py
docs/admin_api_v1.md
docs/handoff/pr32_derived_workflow_state_read_api_smoke_2026-06-17.md
tests/test_admin_api_v1.py
tests/test_admin_api_v1_decision_card.py
tests/test_admin_api_v1_listings.py
tests/test_admin_api_v1_workflow.py
```

---

## Build and restart evidence

Commands run:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Image deploy-app    Built
Image deploy-worker Built
Container deploy-redis-1    Healthy
Container deploy-postgres-1 Healthy
Container deploy-worker-1   Started
Container deploy-app-1      Started
```

---

## Health and migration state

Health:

```text
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok"}
```

Alembic state:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

Conclusion:

```text
Health OK ✅
No new migration ✅
Alembic unchanged ✅
```

---

## Auth key check

Read key length check:

```text
read_key_len=64
```

No key value was printed.

---

## Existing route smoke

The following routes returned `200`:

```text
/health
/admin/system
/admin/listing-analyses
/admin/review-queue
/api/admin/v1/status
/api/admin/v1/meta
/api/admin/v1/listings?limit=5&offset=0
/api/admin/v1/review-queue?limit=5&offset=0
```

Observed status sequence:

```text
200
200
200
200
200
200
200
200
```

Conclusion:

```text
Existing /admin HTML routes OK ✅
Existing Admin API v1 routes OK ✅
```

---

## PR33 meta contract smoke

Meta contract validation script passed:

```text
PR33 meta contract OK
```

Validated fields:

```text
meta_contract_version = v1
workflow_contract_version = workflow-state-v1
decision_card_contract_version = decision-card-v1
capabilities.decision_card = true
capabilities.write_api = false
capabilities.technical_api_actions = false
capabilities.workflow_actions_execute = false
capabilities.report_export = false
decision_recommendation enum present
```

Validated decision recommendation vocabulary:

```text
take_in_work
needs_data
watchlist
reject
analysis_pending
insufficient_evidence
```

Conclusion:

```text
PR33 meta contract OK ✅
write/report/action-execution capabilities remain false ✅
```

---

## Listing used for Decision Card smoke

Selected listing id:

```text
decision_card_listing_id=597
```

This id was extracted from `/api/admin/v1/review-queue` / `/api/admin/v1/listings` output.

---

## Decision Card endpoint smoke

Endpoint:

```text
GET /api/admin/v1/listings/597/decision-card
```

Result:

```text
200
```

Core DTO fields from response:

```text
schema_version = decision-card-v1
decision_card_model_version = decision-card-v1
decision_card_template_version = decision-card-templates-v1
decision_card_policy_version = decision-card-policy-v1
recommendation_scope = internal_workflow
listing_id = 597
listing_external_id = 8081430175
primary_recommendation.code = take_in_work
primary_recommendation.confidence = medium
primary_recommendation.reason = workflow_ready_for_work
headline.code = take_in_work
workflow.schema_version = workflow-state-v1
workflow.workflow_state = ready_for_work
```

Section sizes:

```text
top_reasons = 3
top_risks = 0
next_steps = 3
missing_data = 2
```

Observed `top_reasons`:

```text
strong_verdict
required_data_present
workflow_ready_for_work
```

Observed `next_steps`:

```text
open_listing     executable_now=true
call_owner       executable_now=false
request_data     executable_now=false
```

Observed `missing_data`:

```text
market_evidence required=false
human_review    required=false
```

Observed `data_quality`:

```text
status = partial
flags = [human_review]
limitations = [raw_quality_facts_not_exposed_in_pr33]
```

Observed `source_trace.market_evidence`:

```json
{
  "present": null,
  "ref": null,
  "status": "not_checked_in_pr33"
}
```

Observed limitations include:

```text
decision_card_v1_deterministic
recommendation_scope_internal_workflow
not_investment_advice
not_certified_appraisal
not_valuation_report
no_valuation_opinion
no_llm_wording_in_v1
write_actions_not_executable_in_pr33
risk_visual_severity_not_implemented_in_pr33
readiness_checklist_not_implemented_in_pr33
price_position_not_implemented_in_pr33
market_evidence_not_checked_in_pr33
human_review_missing
```

Input hashes present:

```text
decision_card_input_hash present
analysis_input_hash present
workflow_source_hash present
```

Contract validation script passed after correcting the smoke assertion for `headline.text.ru/en`:

```text
PR33 decision-card contract OK
recommendation: take_in_work
confidence: medium
workflow_state: ready_for_work
risks: []
```

Conclusion:

```text
Decision Card endpoint OK ✅
recommendation_scope=internal_workflow ✅
primary_recommendation vocabulary OK ✅
Decision Card derives from PR32 workflow ✅
No score fallback observed ✅
Section limits OK ✅
source_trace.market_evidence=not_checked_in_pr33 ✅
market_evidence_unavailable not emitted ✅
```

---

## Decision-source compatibility smoke

Endpoint:

```text
GET /api/admin/v1/listings/597/decision-source
```

Result:

```text
200
```

Validated fields:

```text
schema_version = decision-source-v1
available_sections.decision_card = true
decision_card_ref.schema_version = decision-card-v1
decision_card_ref.route_name = admin_api_v1_decision_card
decision_card_ref.listing_id = 597
```

Decision Card reference is compact and relative:

```json
{
  "route_name": "admin_api_v1_decision_card",
  "listing_id": 597,
  "schema_version": "decision-card-v1"
}
```

No absolute URL or auth params were present in `decision_card_ref`.

Compatibility validation script passed:

```text
PR33 decision-source compatibility OK
```

Conclusion:

```text
Decision-source compatibility OK ✅
Full Decision Card not embedded ✅
No execution endpoint in decision-source ✅
No raw JSON in decision-source ✅
```

---

## Auth and validation smoke

Negative requests returned expected statuses:

```text
query auth          -> 403 forbidden
missing key         -> 403 forbidden
invalid key         -> 403 forbidden
not-int listing_id  -> 422 validation_error
missing listing     -> 404 not_found
```

Observed examples:

```text
HTTP/1.1 403 Forbidden
{"ok":false,"error":{"code":"forbidden","message":"Invalid admin key","details":null},"meta":{"api_version":"admin-v1"}}
```

```text
HTTP/1.1 422 Unprocessable Entity
{"ok":false,"error":{"code":"validation_error","message":"Validation error", ...},"meta":{"api_version":"admin-v1"}}
```

404 returned JSON content type and expected route behavior.

Conclusion:

```text
query auth rejected ✅
missing/invalid key rejected ✅
validation/not_found behavior OK ✅
```

---

## No side effects check

Counts before read endpoint calls:

```text
listings_total              2124
listing_analyses_total       730
human_reviews_total            0
market_evidence_items_total    0
alerts_sent_total           4068
agent_tasks_total              2
admin_audit_events_total       2
```

Read endpoints executed:

```text
GET /api/admin/v1/listings/597/decision-card
GET /api/admin/v1/listings/597/decision-source
GET /api/admin/v1/meta
```

Counts after read endpoint calls:

```text
listings_total              2124
listing_analyses_total       730
human_reviews_total            0
market_evidence_items_total    0
alerts_sent_total           4068
agent_tasks_total              2
admin_audit_events_total       2
```

Conclusion:

```text
No DB side effects ✅
No audit side effects ✅
No market evidence writes ✅
No human review writes ✅
No alert writes ✅
No agent task writes ✅
```

---

## Boundary and safety checks

### Key-boundary safety

A key-based JSON traversal check was used for the following forbidden keys:

```text
facts_json
result_json
payload_json
risks_json
questions_json
report_md
before_json
after_json
execution_endpoint
risk_severity
visual_weight
blocking
readiness_checklist
readiness
price_position
scenario
dcf
irr
npv
loan
tax
```

Result:

```text
PR33 key-boundary safety OK
```

### Market evidence semantics

`market_evidence_unavailable` grep returned no output.

`market_evidence_not_checked_in_pr33` and `not_checked_in_pr33` were present as intended.

Conclusion:

```text
No raw JSON leak ✅
No execution_endpoint ✅
No forbidden boundary keys ✅
market_evidence_unavailable not emitted ✅
market evidence is represented as not_checked, not unavailable ✅
```

### Secret/API/UI/log safety

API/meta JSON secret grep returned no output for sensitive patterns.

HTML route grep returned no output for raw JSON/secrets/webhook patterns.

Log grep found no Traceback/ERROR/Exception/secrets.

The log grep surfaced only regular operational worker lines, including:

```text
engine_error_count: 0
script.google.com/.../exec
script.googleusercontent.com/macros/echo?<redacted>
```

These are existing delivery/runtime logs and not PR33-specific leaks. URL details were redacted in logs.

Conclusion:

```text
API secret grep clean ✅
HTML safety grep clean ✅
No app/worker errors detected ✅
Logs acceptable ✅
```

---

## Containers and worker

Container status after deploy:

```text
deploy-app-1        Up, healthy
deploy-postgres-1   Up, healthy
deploy-redis-1      Up, healthy
deploy-worker-1     Up
```

Known worker warning remained:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning predates PR33 and is not related to Decision Card API.

Worker completed monitor cycle normally:

```text
monitor cycle completed
```

Conclusion:

```text
Containers healthy/running ✅
Worker running ✅
Known proxy warning only ✅
```

---

## Final smoke verdict

```text
PR33 — Deterministic Decision Card v1 API ✅
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
decision_card_contract_version=decision-card-v1 ✅
decision_card capability=true ✅
write/report/action execution capabilities remain false ✅
/api/admin/v1/listings OK ✅
/api/admin/v1/review-queue OK ✅
/api/admin/v1/listings/{id}/decision-card OK ✅
/api/admin/v1/listings/{id}/decision-source includes decision_card_ref OK ✅
recommendation_scope=internal_workflow ✅
primary_recommendation vocabulary OK ✅
Decision Card derives from PR32 workflow ✅
No score fallback for take_in_work ✅
Section limits OK ✅
source_trace.market_evidence=not_checked_in_pr33 ✅
market_evidence_unavailable not emitted ✅
query auth rejected ✅
missing/invalid key rejected ✅
validation/not_found behavior OK ✅
no DB/audit side effects for read endpoints ✅
no raw JSON leak ✅
no execution_endpoint ✅
no forbidden boundary keys ✅
no appraisal/valuation/guarantee recommendation wording detected in visible DTO content beyond explicit limitation codes ✅
UI/API/log safety acceptable ✅
worker running ✅
Production smoke passed ✅
```

---

## Notes for next PRs

PR33 intentionally does not implement:

```text
risk visual severity / visual weight / blocking flags
readiness checklist
price position DTO
scenario / DCF / financing / tax
report generation
memo generation
commercial offer generation
workflow action execution
human review writes
```

Expected next roadmap steps remain separate:

```text
PR34 — risk visual attention / severity DTO
PR35 — readiness checklist/status/counts
PR36 — price position/comparable chart DTO
```
