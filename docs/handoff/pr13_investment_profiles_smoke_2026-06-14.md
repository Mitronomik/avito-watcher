# PR13 - Investment profiles v0 production smoke

Date: 2026-06-14  
Environment: production  
Repository: Mitronomik/avito-watcher  
Branch: main  
Commit: 6b501b7  

## Scope

PR13 added deterministic investment analysis profiles with manual assumptions:

- `commercial_sale_investment`
- `flat_sale_investment`

The PR is deterministic-only and does not use:

- LLM
- RAG
- external research
- comps
- agents
- live detail fetch
- alert behavior changes
- DB migration

## Deployment

Production checkout was corrected from the temporary docs branch back to `main`.

Confirmed:

```text
branch: main
HEAD: 6b501b7
origin/main: 6b501b7
Images rebuilt and services restarted:

app: built and started
worker: built and started

Alembic remained unchanged:

0012_listing_enrichments (head)

Health check:

HTTP/1.1 200 OK
Baseline

Before smoke:

alerts_sent_baseline_id = 2806
listing_analyses_baseline_id = 730
agent_tasks:
  review_copilot / success = 2
listing_enrichments = 0
knowledge_notes_count = 0
Provider registration check

Confirmed both investment providers are registered:

commercial_sale_investment InvestmentAnalysisProvider deterministic commercial-sale-investment-rules-v0
flat_sale_investment InvestmentAnalysisProvider deterministic flat-sale-investment-rules-v0
Worker logs

Worker after deploy:

monitor cycle completed
searches_processed=0
browser_driver_crashes=0
engine_errors=0
proxy_failures=0

No traceback was observed.

PROXY_URLS not set warning is expected for the current production environment and is not related to PR13.

Smoke scenarios

The smoke script created temporary listings/searches/matches for:

commercial_sale_investment with explicit investment_purchase_price
same commercial input to verify dedupe
changed commercial rent assumption to verify input hash invalidation
flat_sale_investment with explicit investment_purchase_price
missing investment_purchase_price to verify listing.price is not used silently
explicit listing-price fallback with:
investment_allow_listing_price_as_purchase_price = true
investment_price_basis = listing_price_as_purchase_price
Smoke result
PR13_SMOKE_OK
commercial_analysis_id 731
commercial_changed_analysis_id 732
flat_analysis_id 733
missing_price_analysis_id 734
fallback_analysis_id 735
smoke_analysis_count 5
smoke_alerts 0
alerts_before 2806
alerts_after 2806
tasks_before 2
tasks_after 2
enrichments_before 0
enrichments_after 0
notes_before 0
notes_after 0
snapshots_before 0
snapshots_after 0
PR13_SMOKE_CLEANUP_REMAINING 0
Verified behavior

Confirmed:

commercial_sale_investment creates deterministic analysis
flat_sale_investment creates deterministic analysis
same input dedupes
changed manual assumption creates a new analysis row
missing investment_purchase_price forces review and does not use listing.price
explicit fallback uses listing.price only with the required allow flag and price basis
fallback adds human-confirmation risk
no alerts created
no agent tasks created
no listing enrichments created
no knowledge notes created
no detail snapshots changed
smoke data cleanup completed
Post-smoke cleanup verification
remaining_pr13_smoke_listings = 0
remaining_pr13_smoke_searches = 0
remaining_pr13_smoke_matches = 0
remaining_pr13_smoke_analyses = 0
smoke_alerts = 0
alerts_after_baseline = 0
analyses_after_baseline = 0
agent_tasks:
  review_copilot / success = 2
listing_enrichments = 0
knowledge_notes_count = 0
Verdict

PR13 production smoke closed successfully.
PR13 - Investment profiles v0 with manual assumptions ✅
Production smoke closed ✅
Cleanup done ✅
No side effects detected ✅

