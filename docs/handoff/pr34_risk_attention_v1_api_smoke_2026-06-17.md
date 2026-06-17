# PR34 - Risk Attention v1 API production smoke

Date: 2026-06-17
Environment: production (`avito-watcher-prod`)
Scope: PR34 - deterministic Risk Attention v1 read API
Result: PASSED

## Summary

PR34 adds a deterministic, read-only Risk Attention v1 contract for Admin API v1.

The production smoke confirms:

- PR34 was pulled to production from `main`.
- `app` and `worker` images were rebuilt.
- `app` and `worker` were restarted.
- `/health` returned `200 OK`.
- Alembic remained at the existing head `0017_admin_audit_events`.
- Existing admin HTML and Admin API v1 read routes remained healthy.
- New Risk Attention endpoint returned `200`.
- Decision Card now includes `risk_attention`.
- Decision Source now exposes compact `risk_attention_ref`.
- Meta contract exposes Risk Attention v1 contract metadata.
- Read-only smoke showed no DB/audit side effects for PR34 read endpoints.
- Boundary checks passed after correcting smoke checks to avoid false positives on allowed limitation strings.

## Git state

Before pull:

```text
CURRENT_HEAD=7910e70b908e21fc6f4aa4111f7b36c45843ce37
ORIGIN_MAIN=a1ef30aedcd267b5d40a6a84cc78c2ca9a841a01
```

Pull result:

```text
Updating 7910e70..a1ef30a
Fast-forward
```

Top git log after pull:

```text
a1ef30a (HEAD -> main, origin/main, origin/HEAD) Add Risk Attention v1 read API (#233)
966ee00 Add PR33 production smoke handoff
7910e70 Add deterministic Decision Card v1 API (#232)
303114a Add PR32 production smoke handoff (#231)
1d45080 Add derived workflow state read API (#230)
397f768 Add PR31 production smoke handoff (#229)
c9dfbac Add Admin API v1 listing and review queue reads (#228)
ce5f56c Add PR30 production smoke handoff (#227)
aeb6b03 Add Admin API v1 meta contract (#226)
8135754 Add PR29 production smoke handoff (#225)
```

Changed files from deploy pull:

```text
app/api/admin_v1/decision_card.py
app/api/admin_v1/listings.py
app/api/admin_v1/meta_contract.py
app/api/admin_v1/risk_attention.py
docs/admin_api_v1.md
docs/handoff/pr33_decision_card_v1_api_smoke_2026-06-17.md
tests/test_admin_api_v1.py
tests/test_admin_api_v1_decision_card.py
tests/test_admin_api_v1_risk_attention.py
```

## Build and restart

Commands were executed with the production compose file and env file.

Result:

```text
Image deploy-app    Built
Image deploy-worker Built
Container deploy-redis-1    Healthy
Container deploy-postgres-1 Healthy
Container deploy-worker-1   Started
Container deploy-app-1      Started
```

## Health and migration state

Health:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Alembic:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

No migration was introduced by PR34.

## Existing route smoke

The following routes returned `200`:

```text
/health
/admin/system
/admin/listing-analyses
/admin/review-queue
/api/admin/v1/status
/api/admin/v1/meta
/api/admin/v1/listings?limit=10&offset=0
/api/admin/v1/review-queue?limit=10&offset=0
```

## Meta contract smoke

Validated from `/api/admin/v1/meta`:

```text
meta_contract_version = v1
workflow_contract_version = workflow-state-v1
decision_card_contract_version = decision-card-v1
risk_attention_contract_version = risk-attention-v1
```

Capabilities:

```text
decision_card = true
risk_attention = true
write_api = false
technical_api_actions = false
workflow_actions_execute = false
report_export = false
```

Enums validated:

```text
risk_category = data_quality, market, financial, legal, location, object_quality, source_quality, system
risk_severity = info, low, medium, high, critical
```

The stale market-evidence-unavailable semantic was confirmed absent from the meta payload.

Result:

```text
PR34 meta contract OK
```

## Smoke listing

Selected listing id from review queue/listings sample:

```text
risk_attention_listing_id = 597
listing_external_id = 8081430175
latest_analysis_id = 677
workflow_state = ready_for_work
```

## Risk Attention endpoint smoke

Endpoint:

```text
GET /api/admin/v1/listings/597/risk-attention
```

Status:

```text
200
```

Key response fields:

```text
schema_version = risk-attention-v1
risk_attention_model_version = risk-attention-v1
risk_attention_policy_version = risk-attention-policy-v1
risk_attention_label_version = risk-attention-labels-v1
risk_count = 0
blocking_risk_count = 0
max_severity = info
max_visual_weight = 0.0
risks = []
```

Source refs:

```text
listing_id = 597
listing_external_id = 8081430175
listing_analysis_id = 677
human_review_id = null
decision_card_input_hash = present
workflow_source_hash = present
```

Input hashes:

```text
risk_attention_input_hash = present
decision_card_input_hash = present
workflow_source_hash = present
```

Limitations:

```text
risk_attention_v1_enriches_decision_card_top_risks_only
not_investment_advice
not_appraisal
not_valuation_report
visual_attention_only
```

Corrected contract validation result:

```text
PR34 risk-attention contract OK
risk_count: 0
blocking_risk_count: 0
max_severity: info
max_visual_weight: 0.0
risk_ids: []
```

## Decision Card integration smoke

Endpoint:

```text
GET /api/admin/v1/listings/597/decision-card
```

Status:

```text
200
```

Validated:

```text
schema_version = decision-card-v1
recommendation_scope = internal_workflow
primary_recommendation.code = take_in_work
workflow_state = ready_for_work
risk_attention section present
risk_attention.schema_version = risk-attention-v1
risk_attention.risks = []
top_risks = []
```

Invariant validated:

```text
len(decision_card.risk_attention.risks) == len(decision_card.top_risks)
risk_attention.risks matches enriched top_risks by id, rank, category, severity, severity_score, visual_weight, blocking, blocking_scope and recommended_action
```

Corrected integration validation result:

```text
PR34 decision-card integration OK
recommendation: take_in_work
workflow_state: ready_for_work
top_risk_ids: []
```

## Decision Source integration smoke

Endpoint:

```text
GET /api/admin/v1/listings/597/decision-source
```

Status:

```text
200
```

Validated:

```text
schema_version = decision-source-v1
available_sections.decision_card = true
available_sections.risk_attention = true
```

Decision Card ref:

```json
{
  "route_name": "admin_api_v1_decision_card",
  "listing_id": 597,
  "schema_version": "decision-card-v1"
}
```

Risk Attention ref:

```json
{
  "route_name": "admin_api_v1_risk_attention",
  "listing_id": 597,
  "schema_version": "risk-attention-v1"
}
```

Boundary validated:

- No absolute URL in refs.
- No auth params in refs.
- Decision Source does not embed full `risk_attention`.
- Decision Source does not embed full Decision Card.
- No execution endpoint.

Result:

```text
PR34 decision-source compatibility OK
```

## Auth and validation smoke

Validated on:

```text
GET /api/admin/v1/listings/597/risk-attention
```

Results:

```text
query auth -> 403 forbidden
missing key -> 403 forbidden
invalid key -> 403 forbidden
non-integer listing_id -> 422 validation_error
missing listing -> 404 not_found
```

This preserves Admin API v1 read auth semantics.

## Read-only side-effect smoke

Counts before PR34 read endpoints:

```text
listings_total = 2184
listing_analyses_total = 730
human_reviews_total = 0
market_evidence_items_total = 0
alerts_sent_total = 4188
agent_tasks_total = 2
admin_audit_events_total = 2
```

Read endpoints executed:

```text
GET /risk-attention -> 200
GET /decision-card -> 200
GET /decision-source -> 200
GET /meta -> 200
```

Counts after read endpoints:

```text
listings_total = 2184
listing_analyses_total = 730
human_reviews_total = 0
market_evidence_items_total = 0
alerts_sent_total = 4188
agent_tasks_total = 2
admin_audit_events_total = 2
```

Result:

```text
No DB/audit side effects for PR34 read endpoints.
```

## Boundary and safety smoke

Key-boundary validation passed:

```text
PR34 key-boundary safety OK
```

Forbidden DTO keys checked absent from API responses:

```text
raw JSON payload keys
execution endpoint key
readiness checklist fields
price position field
scenario / DCF / financing / tax fields
investment probability / expected profit / expected loss fields
```

Text-safety validation passed:

```text
PR34 text safety OK
```

Forbidden semantic phrases checked absent from API responses:

```text
stale market-evidence-unavailable semantic
guaranteed yield/rent/value wording
must-buy/must-sell wording
legal/tax advice wording
probability/expected-loss/expected-profit wording
```

Sensitive-output checks passed for API JSON and admin HTML smoke outputs. No secrets were included in this handoff.

## Non-empty risk case

Scanner found a non-empty risk case:

```text
found_risky_listing_id = 2170
risk_ids = ['analysis_missing']
```

This confirms PR34 was not only tested against an empty risk list.

## Container and worker status

`docker compose ps`:

```text
deploy-app-1        Up (healthy)
deploy-postgres-1   Up (healthy)
deploy-redis-1      Up (healthy)
deploy-worker-1     Up
```

Worker logs:

- Known warning: proxies are not configured.
- Parser/monitor cycle summaries present.
- `engine_error_count=0` in sampled logs.
- No blocking PR34 errors observed.

No PR34 traceback or exception observed.

## False-positive smoke notes

Two initial smoke checks failed because the scripts searched broad substrings inside allowed limitation strings.

False-positive examples:

```text
not_investment_advice
readiness_checklist_not_implemented_in_pr33
price_position_not_implemented_in_pr33
```

These strings are allowed boundary limitations, not leaked DTO fields or implemented PR35/PR36 functionality.

Corrected checks were run and passed:

```text
PR34 risk-attention contract OK
PR34 decision-card integration OK
PR34 key-boundary safety OK
PR34 text safety OK
```

## Final status

```text
PR34 - Risk Attention v1 read API ✅
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
risk_attention_contract_version=risk-attention-v1 ✅
risk_attention capability=true ✅
risk_category enum exact ✅
risk_severity enum exact ✅
write/report/action execution capabilities remain false ✅
/api/admin/v1/listings OK ✅
/api/admin/v1/review-queue OK ✅
/api/admin/v1/listings/{id}/risk-attention OK ✅
/api/admin/v1/listings/{id}/decision-card includes risk_attention OK ✅
/api/admin/v1/listings/{id}/decision-source includes risk_attention_ref OK ✅
risk_attention_ref compact/no auth/no absolute URL ✅
Risk Attention derives from PR33 top_risks ✅
No second risk engine ✅
No top_risks id/rank mutation ✅
risk_attention.risks matches enriched decision_card.top_risks ✅
id is canonical risk identifier; no risk_id ✅
blocking_scope=visual_attention ✅
blocking does not disable actions ✅
severity_score bounded 0..1 ✅
visual_weight bounded 0..1 ✅
visual_weight == severity_score in v1 ✅
stale market-evidence-unavailable semantic absent ✅
market_evidence_not_checked not generated by default ✅
no market_evidence_items query effect ✅
query auth rejected ✅
missing/invalid key rejected ✅
validation/not_found envelopes OK ✅
no DB/audit side effects for read endpoints ✅
no raw JSON leak ✅
no execution endpoint ✅
no readiness checklist ✅
no price position DTO ✅
no scenario/DCF/financing/tax ✅
no advisory/probability/guarantee wording ✅
UI/API/log safety acceptable ✅
worker running ✅
non-empty risk case checked ✅
Production smoke passed ✅
```

## Next step

Proceed to the next roadmap step only after this handoff is merged:

```text
PR35 - Readiness checklist / decision readiness contract
```

PR35 must not be started inside PR34.
