# PR35 Readiness Checklist v1 API — production smoke handoff

Date: 2026-06-17
Environment: production, `avito-watcher-prod`
Branch after pull: `main`
Production commit after deploy: `3dc6e451a21af7356a65462da5c7a9685239c3cd`
Merged PR: `#235 Add Readiness Checklist v1 read API`

## Scope

PR35 added a deterministic, read-only Readiness Checklist v1 API for Admin API v1.

Implemented production-facing surfaces:

- `GET /api/admin/v1/listings/{listing_id}/readiness-checklist`
- `readiness_checklist` embedded into `decision-card-v1`
- `available_sections.readiness_checklist = true` in `decision-source-v1`
- compact `readiness_checklist_ref` in `decision-source-v1`
- meta contract addition: `readiness_checklist_contract_version = readiness-checklist-v1`
- meta capability: `capabilities.readiness_checklist = true`
- meta enums for readiness status, item status, group, and item id

Explicit non-goals preserved:

- no write endpoints
- no action execution
- no workflow mutation
- no recommendation mutation
- no risk-attention mutation
- no price-position DTO
- no scenario, DCF, financing, tax, or confirmed-data workflow
- no raw JSON exposure
- no LLM, agents, parser run, or external research call in the read API path

## Deployment summary

Production was updated from PR34 to PR35 by fast-forward pull:

```text
Before: a1ef30aedcd267b5d40a6a84cc78c2ca9a841a01
After:  3dc6e451a21af7356a65462da5c7a9685239c3cd
Top commit: Add Readiness Checklist v1 read API (#235)
```

Build and restart completed:

```text
app image: built
worker image: built
app container: started and healthy
worker container: started
postgres: healthy
redis: healthy
```

Health and migration state:

```text
/health: 200 OK
Alembic current: 0017_admin_audit_events (head)
Alembic heads:   0017_admin_audit_events (head)
No migration was introduced by PR35.
```

## Existing route smoke

All existing routes returned HTTP 200:

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

Meta contract validation passed.

Confirmed:

```text
meta_contract_version = v1
workflow_contract_version = workflow-state-v1
decision_card_contract_version = decision-card-v1
risk_attention_contract_version = risk-attention-v1
readiness_checklist_contract_version = readiness-checklist-v1
```

Capabilities:

```text
decision_card = true
risk_attention = true
readiness_checklist = true
write_api = false
technical_api_actions = false
workflow_actions_execute = false
report_export = false
```

Readiness enum sets validated exactly:

```text
readiness_status:
- ready
- partial
- blocked
- not_applicable

readiness_item_status:
- ok
- warning
- missing
- blocked
- not_applicable

readiness_group:
- listing_data
- freshness
- price_area
- market_evidence
- source_quality
- financial_assumptions
- object_quality
- human_confirmation
- report_readiness

readiness_item_id:
- listing_exists
- listing_url_present
- analysis_available
- freshness_known
- price_present
- area_present
- market_evidence_checked
- source_trace_available
- financial_assumptions_present
- object_quality_available
- human_review_available
- report_inputs_ready
```

## Main listing used for detailed smoke

Selected listing id:

```text
listing_id = 597
listing_external_id = 8081430175
latest_analysis_id = 677
latest_analysis_verdict = strong
workflow_state = ready_for_work
```

Endpoint checks returned HTTP 200:

```text
/api/admin/v1/listings/597/readiness-checklist
/api/admin/v1/listings/597/decision-card
/api/admin/v1/listings/597/risk-attention
/api/admin/v1/listings/597/decision-source
```

## Readiness Checklist DTO result

Readiness checklist response for listing `597`:

```text
schema_version = readiness-checklist-v1
readiness_model_version = readiness-checklist-v1
readiness_policy_version = readiness-policy-v1
readiness_label_version = readiness-labels-v1
status = partial
checked_count = 8
total_count = 8
critical_missing_count = 0
blocking_item_count = 0
```

The corrected PR35 counter semantics were confirmed:

```text
checked_count == count(items where status != not_applicable)
total_count == count(items where status != not_applicable)
checked_count == total_count
not_applicable items are excluded from both checked_count and total_count
```

Item statuses observed for listing `597`:

```text
listing_exists: ok, critical=true
listing_url_present: ok, critical=false
analysis_available: ok, critical=true
freshness_known: ok, critical=false
price_present: ok, critical=true
area_present: ok, critical=true
market_evidence_checked: not_applicable, critical=false
source_trace_available: ok, critical=false
financial_assumptions_present: not_applicable, critical=false
object_quality_available: not_applicable, critical=false
human_review_available: warning, critical=false
report_inputs_ready: not_applicable, critical=false
```

Important PR35 boundary semantics verified:

- `market_evidence_checked` is `not_applicable` by default.
- `financial_assumptions_present` is `not_applicable` by default.
- `object_quality_available` is `not_applicable` by default.
- `report_inputs_ready` is `not_applicable` by default.
- `human_review_available` is warning-only and non-critical.
- `freshness_known` is non-critical for this case.
- no report/export action is recommended from PR35.
- recommended actions remain metadata-only.

## Decision Card integration smoke

Decision Card response for listing `597` included the embedded readiness checklist.

Validation passed:

```text
decision_card.schema_version = decision-card-v1
recommendation_scope = internal_workflow
primary_recommendation.code = take_in_work
workflow.workflow_state = ready_for_work
readiness_checklist.status = partial
```

Strong invariant confirmed:

```text
decision_card.readiness_checklist == standalone readiness-checklist endpoint data
```

This confirms the embedded Decision Card section is produced by the same readiness service as the standalone endpoint.

PR35 readiness metadata did not mutate:

- PR32 workflow state
- PR32 allowed/blocked actions
- PR33 primary recommendation
- PR33 top reasons
- PR33 top risks
- PR34 risk attention

## Decision Source compatibility smoke

Decision Source response for listing `597` passed validation.

Confirmed:

```text
schema_version = decision-source-v1
available_sections.decision_card = true
available_sections.risk_attention = true
available_sections.readiness_checklist = true
```

Refs present:

```text
decision_card_ref.route_name = admin_api_v1_decision_card
decision_card_ref.schema_version = decision-card-v1

risk_attention_ref.route_name = admin_api_v1_risk_attention
risk_attention_ref.schema_version = risk-attention-v1

readiness_checklist_ref.route_name = admin_api_v1_readiness_checklist
readiness_checklist_ref.schema_version = readiness-checklist-v1
```

Decision Source remained compact:

- no full Decision Card embedded
- no full Risk Attention embedded
- no full Readiness Checklist embedded
- no action execution metadata
- no absolute API URL in refs
- no auth parameter in refs

## Auth and validation smoke

Readiness endpoint negative checks passed:

```text
query-based auth attempt: 403
missing read credential: 403
invalid read credential: 403
non-integer listing id: 422 validation_error envelope
missing listing id: 404 not_found envelope
```

## Side-effect smoke

Before/after counts around read endpoints were stable for the tables that matter for PR35 read-only behavior:

```text
human_reviews: unchanged
market_evidence_items: unchanged
agent_tasks: unchanged
admin_audit_events: unchanged
```

The worker was running during the smoke, so listing and alert counts can change independently of the read endpoint tests. In the captured before/after block for the actual read endpoint sequence, counts remained stable as well.

## Blocked readiness sample

A blocked readiness sample was found in the current listing sample:

```text
found_blocked_readiness_listing_id = 2188
critical_missing_count = 2
```

Observed blocked sample item states:

```text
listing_exists: ok, critical=true
listing_url_present: ok, critical=false
analysis_available: blocked, critical=true
freshness_known: ok, critical=false
price_present: ok, critical=true
area_present: missing, critical=true
market_evidence_checked: not_applicable, critical=false
source_trace_available: ok, critical=false
financial_assumptions_present: not_applicable, critical=false
object_quality_available: not_applicable, critical=false
human_review_available: not_applicable, critical=false
report_inputs_ready: not_applicable, critical=false
```

This confirms PR35 handles both a partial/usable checklist and a blocked checklist case in production.

## Boundary checks

Key-boundary safety passed.

Confirmed no actual DTO keys for:

```text
raw analysis blobs
raw result payloads
execution endpoint
price position
scenario
DCF
IRR
NPV
loan
 tax
confirmed rent
confirmed price
confirmed area
confirmed operating expense
confirmed capital expense
valuation opinion
success probability
expected profit/loss
```

Text safety passed for unsafe advisory phrases:

```text
must buy
must sell
guaranteed yield
guaranteed rent
guaranteed market value
legal advice
tax advice
certified appraisal conclusion
valuation opinion conclusion
probability of success
expected profit
expected loss
```

Allowed boundary limitation strings were present as expected, including:

```text
not_investment_advice
not_appraisal
not_valuation_report
readiness_is_not_action_authorization
```

Secret-pattern grep initially produced a false positive because the response contains the safe limitation string `readiness_is_not_action_authorization`. After narrowing grep to header/env/value-like forms, the check returned no matches.

Logs check after narrowing to log-level and secret-like patterns returned no matches.

## Worker / runtime state

Container state after deploy:

```text
app: up and healthy
worker: up
postgres: healthy
redis: healthy
```

Worker logs showed normal cycle summaries. Known warning remains:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

Known diagnostic remains visible as a boolean, not a secret value:

```text
llm_api_key_set: True
```

No traceback, critical log, or real error log was observed in the final narrowed logs check.

## False positives encountered during smoke

The following smoke failures were not product failures:

1. A broad text check matched `not_investment_advice`. This is an expected safety limitation, not advisory leakage.
2. A broad text check matched `price_position_not_implemented_in_pr33`. This is an expected boundary limitation, not PR36 DTO leakage.
3. A broad grep matched `readiness_is_not_action_authorization`. This is an expected boundary limitation, not an auth header leak.
4. A broad grep matched the word `database` inside a normal explanation sentence. This is not a database URL or secret.
5. A broad logs grep matched `engine_error_count=0`. This is a metric with zero errors, not an error log.
6. One terminal command had a Cyrillic prefix typo before `python3`; corrected command was used successfully.

## Final verdict

```text
PR35 — Readiness Checklist v1 read API: production smoke passed
```

Checklist:

```text
Merged: yes
Pulled to production: yes
Built app and worker: yes
Restarted app and worker: yes
Health OK: yes
No migration: yes
Alembic unchanged: yes
Existing Admin UI routes OK: yes
Existing Admin API routes OK: yes
Meta contract OK: yes
Readiness endpoint OK: yes
Decision Card integration OK: yes
Decision Source compatibility OK: yes
Auth and validation OK: yes
Counter semantics OK: yes
Blocked readiness sample found: yes
No relevant DB/audit side effects: yes
No raw JSON leak: yes
No write/action execution leak: yes
No price/scenario/DCF/financing/tax leak: yes
No confirmed-data workflow leak: yes
No unsafe advisory wording: yes
Secret-pattern grep OK after false-positive narrowing: yes
Logs OK after false-positive narrowing: yes
Worker running: yes
```
