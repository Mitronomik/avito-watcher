# PR16b — Deterministic market evidence matching policy production smoke

Date: 2026-06-14  
Environment: production server `avito-watcher-prod`  
Repository: `Mitronomik/avito-watcher`  
Runtime branch: `main`  
PR: #169 — `Add deterministic market evidence matching policy`  
Merge commit: `8fb308c77fa39e65bd815e742b2e35e89a22b748`  
Status: CLOSED ✅

---

## 1. Purpose

This handoff records the production deployment and smoke result for PR16b.

PR16b added a deterministic market evidence matching policy layer on top of PR16.

PR16 used only same-listing stored market evidence.

PR16b added controlled, explicitly configured cross-listing reuse through exact `same_location_key` matching.

The production smoke verified that:

```text
same_location_key selects eligible cross-listing evidence
same_listing ignores cross-listing evidence
wrong-location / wrong-asset / wrong-deal / expired / low-confidence / missing-source evidence is excluded
selected evidence affects input_hash deterministically
same input reruns reuse the same analysis row/input_hash
cross-listing evidence is capped conservatively
manual-primary behavior remains safe
no alerts/tasks/evidence mutations happen during analysis
cleanup leaves zero temporary rows
```

---

## 2. Architectural boundary

PR16b is intentionally narrow.

It implements:

```text
deterministic market evidence matching policy
same_listing effective default
opt-in same_location_key policy
exact configured location_key matching only
selected evidence fingerprint in input_hash
facts/questions explaining matching policy
conservative cross-listing guardrails
```

It does not implement:

```text
new scoring formula
adjusted comparable model
comp quality scoring
source reputation scoring
GIS/geocoding/radius matching
fuzzy matching
semantic/vector search
ResearchAgent changes
external research
monitor-cycle research
automatic task creation
automatic evidence ingestion
alert changes
Google Sheets schema changes
```

The core project rule remains:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates market assumptions.
Human approves action.
```

---

## 3. PR16b code status

GitHub PR metadata:

```text
PR #169
Title: Add deterministic market evidence matching policy
State: closed
Merged: true
Merge commit: 8fb308c77fa39e65bd815e742b2e35e89a22b748
Changed files: 8
Additions: 564
Deletions: 16
```

PR summary confirmed:

```text
market_evidence_matching_policy added to AnalysisConfig with None default
same_listing effective default
same_location_key opt-in policy
exact configured location_key selection
matching policy/fingerprint included in input_hash
cross-listing facts/questions added
manual-primary behavior preserved
no schema migration
no LLM / ResearchAgent / external calls
```

Local/CI checks reported before merge:

```text
python3 -m compileall app ✅
python3 -m ruff check app tests ✅
pytest -q tests/test_investment_market_comps.py ✅
pytest -q tests/test_investment_market_comps_hash.py ✅
pytest -q tests/test_investment_analysis.py ✅
pytest -q ✅
git diff --check ✅
alembic heads ✅
```

GitHub CI was green before merge.

---

## 4. Production deployment

Production build completed:

```text
Image deploy-worker Built ✅
Image deploy-app Built ✅
```

Alembic heads:

```text
0013_market_evidence_storage (head)
```

Alembic current:

```text
0013_market_evidence_storage (head)
```

No new migration was introduced by PR16b.

App and worker restarted:

```text
deploy-app-1 Started ✅
deploy-worker-1 Started ✅
```

Health check:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Worker logs after deploy:

```text
PROXY_URLS not set — running without proxies (expected current environment warning)
monitor worker runtime diagnostics printed
avito_parser.end_cycle stats printed
monitor_service.cycle_summary searches_processed=0
monitor cycle completed
```

No production log errors were observed:

```text
Traceback: none ✅
OperationalError: none ✅
UndefinedTable: none ✅
unsupported analysis profile: none ✅
```

---

## 5. Production smoke setup

Smoke prefix:

```text
pr16b-smoke-2026-06-14
```

Temporary target listing:

```text
pr16b-smoke-2026-06-14-target
```

Temporary source listing ids:

```text
pr16b-smoke-2026-06-14-src-1
pr16b-smoke-2026-06-14-src-2
pr16b-smoke-2026-06-14-src-3
```

Configured location key:

```text
pr16b-smoke-location-key
```

Temporary market evidence inserted for smoke only:

```text
3 eligible same_location_key cross-listing comps
1 wrong asset type comp
1 wrong deal type comp
1 expired / too old comp
1 low confidence comp
1 missing source comp
1 wrong location_key comp
```

Smoke intentionally did not run:

```text
ResearchAgent
LLM
external providers
real Avito parser calls
monitor-cycle research
automatic evidence ingestion
```

---

## 6. Production smoke results

Smoke command completed successfully:

```text
PR16B_SMOKE_OK ✅
PR16B_SMOKE_CLEANUP_REMAINING 0 ✅
```

Main same-location-key analysis:

```text
same_location_analysis_id 739
same_location_input_hash e0f0543a7d17d4bc5b86d91ded4392cf40c4ef0b76fe0a5c30c536c87b2ef65d
same_location_rerun_id 739
same_location_rerun_hash e0f0543a7d17d4bc5b86d91ded4392cf40c4ef0b76fe0a5c30c536c87b2ef65d
```

Idempotency result:

```text
same_location_rerun_id == same_location_analysis_id ✅
same_location_rerun_hash == same_location_input_hash ✅
```

Cross-listing selection result:

```text
same_location_monthly_rent 120000.0
same_location_rent_per_m2 2400.0
same_location_usable_comp_count 3
same_location_selected_external_listing_count 3
same_location_distinct_listing_count 3
```

This proves that `same_location_key` selected eligible evidence from other listing ids.

Conservative cross-listing guardrail:

```text
same_location_verdict medium
same_location_score 90.0
same_location_cross_listing_cap True
```

This proves that cross-listing evidence did not produce `strong` before future comparable quality / selection policy upgrades.

Same-listing isolation:

```text
same_listing_analysis_id 740
same_listing_usable_comp_count 0
same_listing_rent_source missing
```

This proves that `same_listing` ignored cross-listing evidence.

Missing location-key scenario:

```text
missing_location_analysis_id 741
```

The smoke asserted that missing `market_evidence_location_key` under `same_location_key` goes to review and does not fallback to broad evidence.

Manual-primary scenario:

```text
manual_primary_analysis_id 742
```

The smoke asserted that manual rent remains primary and missing/wrong cross-listing evidence does not degrade manual-primary calculation by default.

---

## 7. Side-effect checks

Baseline and post-smoke counters:

```text
alerts_before 2822
alerts_after 2822

tasks_before 2
tasks_after 2

analyses_before 730
analyses_after_before_cleanup 734

runs_before 0
runs_after_before_cleanup 1

items_before 0
items_after_before_cleanup 9
```

Expected temporary deltas:

```text
listing_analyses +4 ✅
market_research_runs +1 ✅
market_evidence_items +9 ✅
```

No unexpected side effects:

```text
alerts_sent unchanged ✅
agent_tasks unchanged ✅
knowledge_notes unchanged ✅
listing_enrichments unchanged ✅
listing_detail_snapshots unchanged ✅
```

The analysis path did not create alerts, agent tasks, knowledge notes, enrichments, or detail snapshots.

Temporary market evidence rows were created only by the smoke setup and removed by cleanup.

---

## 8. Cleanup verification

Smoke cleanup result:

```text
PR16B_SMOKE_CLEANUP_REMAINING 0 ✅
```

Post-cleanup SQL check confirmed all temporary rows were removed:

```text
pr16b_smoke_listings = 0
pr16b_smoke_analyses = 0
pr16b_smoke_runs = 0
pr16b_smoke_items = 0
pr16b_smoke_tasks = 0
```

Cleanup status:

```text
listings cleanup ✅
listing_analyses cleanup ✅
market_research_runs cleanup ✅
market_evidence_items cleanup ✅
agent_tasks cleanup ✅
```

---

## 9. What PR16b proves

PR16b production smoke proves:

```text
same_location_key cross-listing reuse works when explicitly configured ✅
same_listing remains isolated and ignores cross-listing evidence ✅
wrong candidates are excluded ✅
matching policy affects input_hash ✅
same input rerun is idempotent ✅
cross-listing evidence is capped conservatively ✅
manual-primary behavior remains safe ✅
no LLM / ResearchAgent / external calls are involved ✅
no unexpected side effects ✅
cleanup is clean ✅
```

---

## 10. What PR16b does not prove

PR16b does not prove:

```text
comparable quality scoring
adjusted comp rent
source reputation scoring
area-band quality model
sale/cap-rate evidence
DCF/scenario modeling
professional valuation/appraisal suitability
```

Those are future roadmap items.

Important boundary:

```text
PR16 = use stored same-listing market comps in investment analysis.
PR16b = allow explicit exact same_location_key cross-listing evidence pools.
PR24/PR25+ = comparable quality, richer selection policy and adjusted comps.
```

---

## 11. Operational notes

PR16b is opt-in.

Default behavior remains effectively:

```text
market_evidence_matching_policy = same_listing
```

Cross-listing reuse requires explicit config:

```json
{
  "use_market_evidence": true,
  "market_evidence_matching_policy": "same_location_key",
  "market_evidence_location_key": "..."
}
```

No global setting was enabled in production.

Existing searches should not change unless their analysis config explicitly enables market evidence and the matching policy.

---

## 12. Final verdict

```text
PR16b production deploy: passed ✅
PR16b feature smoke: passed ✅
Cross-listing same_location_key policy: passed ✅
Same-listing isolation: passed ✅
Input hash/idempotency: passed ✅
Manual-primary safety: passed ✅
No side effects: passed ✅
Cleanup: passed ✅
Post-cleanup SQL check: all 0 ✅

Status: CLOSED ✅
```

PR16b can be considered fully closed.

Next roadmap step may proceed only after this handoff is merged as docs-only.
