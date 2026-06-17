# PR36 Price Position v1 API production smoke handoff

Date: 2026-06-17

Environment: production

Repository: `Mitronomik/avito-watcher`

## Scope

PR36 added the deterministic read-only Price Position v1 API:

```text
GET /api/admin/v1/listings/{listing_id}/price-position
```

It also integrated the same Price Position DTO into Decision Card v1, added a compact `price_position_ref` to decision-source, and extended `/meta` with the price-position contract version, capability, and enums.

PR36 intentionally does not create a new comparable selection engine. When selected adjusted comparable data is unavailable, it returns a safe `insufficient_data` DTO and hides the chart.

## Deployed commit

Production was updated from:

```text
3dc6e451a21af7356a65462da5c7a9685239c3cd
```

to:

```text
b0a4089bbb0dfdadb8a0480fcecf8a27bb906b77
```

Production head after deploy:

```text
b0a4089 Add Price Position v1 read API (#237)
```

## Deploy result

The app and worker images were rebuilt and restarted successfully.

Health check returned HTTP 200.

Alembic remained unchanged:

```text
0017_admin_audit_events (head)
```

No new migration was introduced by PR36.

## Existing route smoke

The following existing routes returned HTTP 200:

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

Verified:

```text
meta_contract_version = v1
workflow_contract_version = workflow-state-v1
decision_card_contract_version = decision-card-v1
risk_attention_contract_version = risk-attention-v1
readiness_checklist_contract_version = readiness-checklist-v1
price_position_contract_version = price-position-v1
```

Verified capabilities:

```text
decision_card = true
risk_attention = true
readiness_checklist = true
price_position = true
write_api = false
technical_api_actions = false
workflow_actions_execute = false
report_export = false
```

Verified Price Position enum families:

```text
price_position_code
price_position_confidence
price_position_location_basis
price_position_chart_reason
```

Optional PR36 metadata enum families were present and accepted:

```text
price_position_metric
price_position_range_basis
```

Smoke result:

```text
PR36 meta contract OK
```

## Smoke listing

The selected smoke listing was:

```text
listing_id = 597
listing_external_id = 8081430175
latest_analysis_id = 677
latest_analysis_status = success
latest_analysis_profile = commercial_rent
latest_analysis_score = 95.0
latest_analysis_verdict = strong
workflow_state = ready_for_work
readiness_status = partial
```

## PR36 endpoint smoke

The following endpoints returned HTTP 200:

```text
GET /api/admin/v1/listings/597/price-position
GET /api/admin/v1/listings/597/decision-card
GET /api/admin/v1/listings/597/readiness-checklist
GET /api/admin/v1/listings/597/risk-attention
GET /api/admin/v1/listings/597/decision-source
```

## Observed Price Position DTO

The standalone Price Position DTO returned:

```text
schema_version = price-position-v1
price_position_model_version = price-position-v1
price_position_policy_version = price-position-policy-v1
price_position_label_version = price-position-labels-v1
metric = asking_rent_per_m2
currency = RUB
period = month
area_unit = m2
range_basis = selected_adjusted_comparables
subject_price_per_m2 = 40
market_low = null
market_median = null
market_high = null
position = insufficient_data
confidence = insufficient_data
location_basis = insufficient_location
selected_comps_count = 0
excluded_comps_count = 0
selected_evidence_ids = []
chart.visible = false
chart.reason = insufficient_selected_comps
```

This is expected in current production data because selected adjusted comparable evidence is unavailable.

The DTO did not fabricate comparable ranges and did not expose a visible chart without selected comparable evidence.

## Contract validation

Price Position contract validation passed.

Validated:

```text
version constants
finite position/confidence/location/chart enums
server-side subject price per m2
selected_comps_count equals selected evidence id count
excluded_comps_count traceability
safe insufficient_data fallback
safe source refs
stable price_position_input_hash
hidden chart when selected comps are insufficient
no unsafe advisory wording
```

Smoke result:

```text
PR36 price-position contract OK
position: insufficient_data
confidence: insufficient_data
location_basis: insufficient_location
subject_price_per_m2: 40
selected_comps_count: 0
excluded_comps_count: 0
chart.visible: false
chart.reason: insufficient_selected_comps
```

## Decision Card integration

Decision Card integration validation passed.

Verified:

```text
decision_card.price_position exists
decision_card.price_position equals standalone price-position DTO
recommendation remains take_in_work
workflow_state remains ready_for_work
readiness_status remains partial
risk_attention remains versioned and present
readiness_checklist remains versioned and present
old PR33 price-position placeholder limitation is gone
```

Smoke result:

```text
PR36 decision-card integration OK
```

## Decision Source integration

Decision Source compatibility validation passed.

Verified:

```text
available_sections.price_position = true
price_position_ref.route_name = admin_api_v1_price_position
price_position_ref.listing_id = 597
price_position_ref.schema_version = price-position-v1
```

The decision-source response exposes only compact refs and does not embed the full Price Position DTO.

Smoke result:

```text
PR36 decision-source compatibility OK
```

## Negative auth and validation smoke

The Price Position endpoint returned expected negative responses:

```text
query credential attempt -> HTTP 403
missing read credential -> HTTP 403
invalid read credential -> HTTP 403
non-integer listing id -> HTTP 422 validation_error
missing listing id -> HTTP 404 not_found
```

## Read-only / no side effects smoke

Controlled before/after DB counts were unchanged around repeated read-only endpoint calls.

Stable counts:

```text
listings_total = 2201
listing_analyses_total = 730
human_reviews_total = 0
market_evidence_items_total = 0
alerts_sent_total = 4222
agent_tasks_total = 2
admin_audit_events_total = 2
```

Read-only side effects check passed.

Earlier worker activity changed listings and alerts before the controlled baseline. The PR36 read endpoints themselves did not mutate the checked tables.

## Boundary and safety checks

Key-boundary check passed.

No forbidden raw/internal keys were found for raw payloads, reports, before/after snapshots, execution endpoints, scenario/financial projection fields, confirmed-data fields, or valuation/advice/probability fields.

Text safety check passed.

Forbidden advisory/probability/guarantee phrases were absent, including buy/sell imperatives, guarantees, valuation conclusions, probability claims, expected profit/loss wording, and undervalued/overvalued/fair-value wording.

Secret/header grep returned no matches.

Log safety grep returned no matches for error/critical/traceback/exception/secret/header-like patterns.

Known unrelated worker warning remained:

```text
PROXY_URLS not set — running without proxies
```

Worker cycle summaries showed zero blocks, zero engine errors, zero browser-driver crashes, zero proxy failures, and zero close failures during the observed interval.

## Visible chart sample scan

A sampled listing scan did not find a visible Price Position chart:

```text
No visible price-position chart in sampled listings; acceptable if selected adjusted comps are not available in current data.
```

This is acceptable for PR36 because current production data does not expose selected adjusted comparable rows for a visible chart.

## Final smoke verdict

```text
PR36 — Price Position v1 read API ✅
Merged ✅
Pulled to production ✅
Built app + worker ✅
Restarted app + worker ✅
Health OK ✅
No migration ✅
Alembic unchanged ✅
Existing admin/API routes OK ✅
Meta contract OK ✅
price-position endpoint OK ✅
Decision Card integration OK ✅
Decision Source compact ref OK ✅
subject price per m2 server-side OK ✅
selected/excluded comparable counters OK ✅
chart hidden when evidence insufficient ✅
safe insufficient_data fallback OK ✅
no frontend computation requirement ✅
no workflow mutation ✅
no recommendation mutation ✅
no risk/readiness mutation ✅
no score/verdict mutation ✅
no new comp selection engine ✅
no raw JSON leak ✅
no scenario/financial projection/financing/tax fields ✅
no confirmed-data workflow ✅
no geocoding/map layer ✅
no LLM/agents/external calls ✅
no write endpoints ✅
auth/validation/not-found envelopes OK ✅
no DB/audit side effects for read endpoints ✅
no advisory/probability/guarantee wording ✅
secret/header grep OK ✅
logs safety OK ✅
worker running ✅
Production smoke passed ✅
```

No redeploy is needed after merging this documentation handoff.
