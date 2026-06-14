# PR16 — Investment scoring with market comps — production smoke handoff

Date: 2026-06-14  
Environment: production server `avito-watcher-prod`  
Repository: `Mitronomik/avito-watcher`  
Base branch: `main`  
Merged PR: #166 — `Use market comps in investment analysis`  
Merge commit: `4327cae2bfc58d18ebb37ab1729b465d6e886dde`  
Status: **CLOSED ✅**

---

## 1. Purpose

This handoff records the production deploy and smoke verification for PR16.

PR16 added opt-in deterministic use of stored SQL-backed market evidence in investment analysis profiles.

PR16 belongs to the roadmap step:

```text
Investment scoring with comps seventh.
```

Architectural rule preserved:

```text
Clean data first.
Deterministic gates second.
Deterministic scoring third.
Human-readable LLM explanation fourth.
RAG memory fifth.
External research sixth.
Investment scoring with comps seventh.
Agent strategy loop last.
```

Correct model preserved:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates market assumptions.
Human approves action.
```

PR16 does **not** make `avito-watcher` a professional appraisal system. It remains an internal investment screening and decision-support system.

---

## 2. PR16 scope recap

PR16 added opt-in market evidence support for investment profiles:

```text
commercial_sale_investment
flat_sale_investment
```

The PR16 implementation introduced:

- nullable market-evidence fields in `AnalysisConfig`;
- deterministic SQL-backed market comps helper;
- same-listing market evidence selection only;
- selected evidence fingerprint before `input_hash`;
- exact selected evidence context passed to investment provider;
- median rent estimate from source-linked reusable rent comps;
- manual rent primary by default;
- evidence-only rent estimate when manual rent is missing and enough comps exist;
- weak/insufficient evidence caps only when market evidence is rent source;
- purchase price safety: market evidence never replaces missing purchase price;
- market-evidence facts and usage booleans;
- dynamic investment report text;
- deterministic human-review questions;
- tests and docs.

Important PR16 boundaries:

```text
No LLM calls during scoring.
No ResearchAgent calls during scoring.
No live external calls during scoring.
No monitor-cycle research.
No automatic task creation.
No automatic evidence ingestion.
No market evidence mutation during analysis.
No alert behavior changes.
No Google Sheets schema changes.
No cross-listing evidence reuse.
No location-level evidence pools.
No GIS/geocoding/radius/fuzzy/semantic matching.
```

Cross-listing evidence matching is explicitly deferred to a future PR, for example:

```text
PR16b / PR25 — deterministic market evidence matching policy
```

---

## 3. Production deploy evidence

### 3.1 Images built

Production rebuild completed successfully:

```text
✔ Image deploy-app    Built
✔ Image deploy-worker Built
```

### 3.2 Alembic heads/current

PR16 introduced no DB migration.

Production Alembic head remained PR15 migration:

```text
0013_market_evidence_storage (head)
```

Production Alembic current also remained:

```text
0013_market_evidence_storage (head)
```

Result:

```text
Alembic unchanged at 0013 ✅
No migration required ✅
```

### 3.3 App and worker startup

Production services were restarted:

```text
✔ Container deploy-app-1      Started
✔ Container deploy-worker-1   Started
```

### 3.4 Health check

Command:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```text
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok"}
```

### 3.5 Worker logs

Worker started and completed a monitor cycle without runtime errors:

```text
WARNING __main__ PROXY_URLS not set — running without proxies (likely blocked by Avito)
INFO __main__ monitor worker runtime diagnostics: {...}
INFO app.parsers.avito_parser avito_parser.end_cycle stats={...}
INFO app.services.monitor_service monitor_service.cycle_summary searches_processed=0 ...
INFO __main__ monitor cycle completed
```

The `PROXY_URLS not set` warning is an existing deployment/runtime warning and is not caused by PR16.

No PR16 runtime errors were observed:

```text
No Traceback ✅
No OperationalError ✅
No UndefinedTable ✅
No TypeError ✅
No unsupported analysis profile ✅
```

---

## 4. Production smoke design

Smoke was designed to be deterministic, safe, and fully cleaned up.

The smoke used temporary synthetic rows only:

```text
PREFIX = pr16-smoke-2026-06-14
LISTING_ID = pr16-smoke-2026-06-14-listing
```

Temporary setup rows:

- one temporary `listings` row;
- one temporary `market_research_runs` row;
- four temporary `market_evidence_items` rows;
- three temporary `listing_analyses` rows created by analysis scenarios.

No real external research provider was called.

No LLM was called by the smoke.

No ResearchAgent was called by the smoke.

No alert was expected or created.

---

## 5. Smoke scenarios

### 5.1 Evidence-only investment analysis

Scenario:

```text
profile = commercial_sale_investment
investment_purchase_price = 12_000_000
use_market_evidence = true
manual estimated_monthly_rent = missing
area_m2 = 50
rent_per_m2 comps = [2200, 2400, 2600, 2800]
```

Expected deterministic rent estimate:

```text
median rent_per_m2 = 2500
monthly rent = 2500 * 50 = 125000
```

Result:

```text
evidence_only_analysis_id 736
evidence_only_input_hash ba40527fcd3a74b8b453457e64cbc72f7ae99e9a823119c57a611fd8e3c48f34
market_estimated_monthly_rent 125000.0
market_estimated_rent_per_m2 2500.0
market_usable_comp_count 4
gross_yield 0.122
noi_yield 0.0954
payback_years 10.49
```

Validated:

```text
rent_estimate_source = market_evidence ✅
market evidence used as rent source ✅
median rent_per_m2 calculated ✅
monthly rent calculated ✅
gross yield calculated ✅
NOI yield calculated ✅
payback calculated ✅
LLM flag false ✅
agent flag false ✅
live external research flag false ✅
report explains stored SQL-backed market evidence ✅
report does not claim manual-only/no-comps ✅
```

### 5.2 Rerun / input_hash idempotency

The same evidence-only analysis was run again with the same listing/config/evidence context.

Result:

```text
rerun_analysis_id 736
rerun_input_hash ba40527fcd3a74b8b453457e64cbc72f7ae99e9a823119c57a611fd8e3c48f34
```

Validated:

```text
same analysis row reused ✅
same input_hash reused ✅
no duplicate analysis for same selected evidence fingerprint ✅
```

### 5.3 Manual-primary + market comparison

Scenario:

```text
profile = commercial_sale_investment
investment_purchase_price = 12_000_000
estimated_monthly_rent = 130000
use_market_evidence = true
market_evidence_manual_mismatch_threshold_pct = 0.05
```

Result:

```text
manual_primary_analysis_id 737
```

Validated:

```text
rent_estimate_source = manual ✅
manual rent remained primary ✅
market evidence was not used as rent source ✅
market evidence was used for comparison ✅
market_comps_used remained false for rent-source semantics ✅
report explains manual-primary comparison mode ✅
manual-primary comparison question present ✅
```

### 5.4 Missing purchase price safety

Scenario:

```text
profile = commercial_sale_investment
investment_purchase_price = null
use_market_evidence = true
enough market evidence exists
```

Result:

```text
missing_price_analysis_id 738
```

Validated:

```text
verdict = review ✅
risk flag includes missing_investment_purchase_price ✅
purchase_price_source is null ✅
market evidence did not replace purchase price ✅
yield not calculated without purchase price ✅
payback not calculated without purchase price ✅
```

---

## 6. Smoke output

Production smoke returned:

```text
PR16_SMOKE_OK
evidence_only_analysis_id 736
evidence_only_input_hash ba40527fcd3a74b8b453457e64cbc72f7ae99e9a823119c57a611fd8e3c48f34
rerun_analysis_id 736
rerun_input_hash ba40527fcd3a74b8b453457e64cbc72f7ae99e9a823119c57a611fd8e3c48f34
manual_primary_analysis_id 737
missing_price_analysis_id 738
market_estimated_monthly_rent 125000.0
market_estimated_rent_per_m2 2500.0
market_usable_comp_count 4
gross_yield 0.122
noi_yield 0.0954
payback_years 10.49
alerts_before 2820
alerts_after 2820
tasks_before 2
tasks_after 2
analyses_before 730
analyses_after_before_cleanup 733
runs_before 0
runs_after_before_cleanup 1
items_before 0
items_after_before_cleanup 4
PR16_SMOKE_CLEANUP_REMAINING 0
```

---

## 7. Side-effect verification

### 7.1 Alerts

```text
alerts_before 2820
alerts_after 2820
```

Result:

```text
No alerts created ✅
No alert side effects ✅
```

### 7.2 Agent tasks

```text
tasks_before 2
tasks_after 2
```

Result:

```text
No agent tasks created ✅
No ResearchAgent called ✅
```

### 7.3 Listing analyses

```text
analyses_before 730
analyses_after_before_cleanup 733
```

Expected temporary smoke analyses:

```text
+1 evidence-only market estimate analysis
+1 manual-primary comparison analysis
+1 missing-purchase-price safety analysis
```

Result:

```text
Expected listing_analyses delta +3 ✅
```

### 7.4 Market evidence rows

```text
runs_before 0
runs_after_before_cleanup 1
items_before 0
items_after_before_cleanup 4
```

These rows were temporary smoke setup rows only.

Result:

```text
Expected temporary market_research_runs delta +1 ✅
Expected temporary market_evidence_items delta +4 ✅
Analysis itself did not create evidence ✅
```

### 7.5 Other tables

Smoke also checked that the following did not change unexpectedly:

```text
knowledge_notes
listing_enrichments
listing_detail_snapshots
```

Result:

```text
No RAG/knowledge notes side effects ✅
No listing enrichment side effects ✅
No detail snapshot side effects ✅
```

---

## 8. Cleanup verification

Smoke cleanup removed all temporary PR16 rows.

Inline cleanup check:

```text
PR16_SMOKE_CLEANUP_REMAINING 0
```

Post-cleanup SQL checks were also run for:

```text
listings
listing_analyses
market_research_runs
market_evidence_items
agent_tasks
```

All returned:

```text
0
```

Result:

```text
Cleanup complete ✅
No smoke leftovers ✅
```

---

## 9. Final PR16 production verdict

```text
PR16 production deploy: done ✅
Alembic: unchanged at 0013 ✅
App health: OK ✅
Worker started cleanly ✅
Feature smoke: passed ✅
Evidence-only rent estimate: passed ✅
Manual-primary comparison: passed ✅
Missing purchase price safety: passed ✅
Input hash / rerun idempotency: passed ✅
No alerts/tasks side effects: passed ✅
No RAG/enrichment/snapshot side effects: passed ✅
Cleanup: done ✅
Post-cleanup SQL check: all 0 ✅

Status: CLOSED ✅
```

---

## 10. What PR16 did not do

PR16 intentionally did not implement:

```text
cross-listing evidence reuse
location-level evidence pools
deterministic market evidence matching policy
GIS/geocoding/radius matching
semantic/fuzzy matching
external research inside scoring
ResearchAgent call inside scoring
LLM call inside scoring
automatic evidence ingestion
automatic reanalysis trigger
automatic task creation
alert behavior changes
Google Sheets schema changes
market evidence schema migration
```

These remain future roadmap items.

---

## 11. Next step

The next roadmap implementation step is:

```text
PR17 — Weekly StrategyAgent with system memory RAG
```

But PR17 must remain bounded:

```text
agent proposes, human approves
no automatic filter mutation
no automatic code mutation
no score/verdict mutation
no alert suppression
no autonomous strategy execution
```

A separate future PR should handle controlled cross-listing market evidence reuse, for example:

```text
PR16b / PR25 — deterministic market evidence matching policy
```

That future PR should define exact matching keys, location semantics, asset/deal compatibility, quality thresholds, selected evidence pool fingerprinting, and tests proving irrelevant evidence does not affect `input_hash`.
