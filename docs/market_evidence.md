# Market evidence storage and SQL-backed market RAG v0

PR15 adds persistent advisory market evidence storage for completed manual `market_research` AgentTasks.

## Scope and boundary

* `market_research_runs` stores normalized research run metadata, source list, confidence, `checked_at`, and `expires_at`.
* `market_evidence_items` stores source-linked reusable evidence items: `comparable_candidate`, `finding`, `assumption_to_verify`, `risk`, and `opportunity`.
* Evidence is advisory and source-linked. `is_reusable=true` means eligible for bounded retrieval context only; it is not accepted market truth and is not safe for scoring by itself.
* `assumption_to_verify` evidence is a hypothesis/question for human validation. It is not an accepted market assumption and must not feed scoring in PR15.
* PR15 does not change scoring, verdicts, alerts, filters, monitor cadence, investment profile formulas, `AnalysisConfig`, input hashes, Google Sheets output, `knowledge_notes`, or reanalysis behavior.
* PR16 may decide how confidence-qualified and verified market evidence is consumed by investment profiles.

## SQL-backed market RAG v0

Market RAG v0 is structured SQL retrieval over `market_evidence_items`. SQL evidence remains the source of truth.

It is not vector/embedding-backed: PR15 adds no pgvector, Chroma, semantic search, Postgres full-text search, geocoding/GIS search, LLM reranking, or external network calls.

The context builder returns a bounded object:

```json
{
  "context_type": "market_research_rag_v0",
  "retrieval_backend": "sql",
  "items": [
    {
      "evidence_item_id": 1,
      "evidence_type": "comparable_candidate",
      "asset_type": "commercial",
      "deal_type": "rent",
      "location_text": "SPb Center",
      "claim": "same district",
      "metrics": {
        "area_m2": 50,
        "rent_rub_per_month": 120000,
        "rent_per_m2_rub": 2400
      },
      "confidence": 0.8,
      "checked_at": "2026-06-14T00:00:00",
      "expires_at": "2026-07-14T00:00:00",
      "source_url": "https://example.com"
    }
  ],
  "limitations": ["SQL-backed advisory market evidence only; not scoring input in PR15."]
}
```

## Manual ingestion

Ingestion is explicit and manual. It only accepts successful `market_research` AgentTasks and validates `result_json` with the PR14 research schema validator before writing evidence.

```bash
python3 -m app.cli ingest-market-evidence --task-id 123
```

The command prints JSON with the run id and created/reused/skipped counts. It does not mutate the AgentTask, create follow-up tasks, call providers, trigger reanalysis, or integrate with the monitor cycle.

## Reuse and expiration

Defaults:

```env
MARKET_EVIDENCE_DEFAULT_TTL_DAYS=30
MARKET_EVIDENCE_MIN_CONFIDENCE_FOR_REUSE=0.5
MARKET_EVIDENCE_MAX_RETRIEVAL_ITEMS=10
```

Expired evidence is not returned by default. Low-confidence evidence is stored for audit but not reused by default. Non-reusable evidence is excluded unless retrieval explicitly asks for it.

Source URLs are stored as originally provided and as normalized URLs. Normalization lowercases scheme/host, strips fragments, removes common tracking parameters, and normalizes trailing slashes.

Evidence content hashes are scoped per run with `(run_id, evidence_type, content_hash)`. PR15 intentionally avoids global dedupe so repeated evidence in different research runs does not lose audit trail.

## Example retrieval

```python
from app.db.session import SessionLocal
from app.services.market_evidence import MarketEvidenceRetriever

with SessionLocal() as db:
    items = MarketEvidenceRetriever(db).retrieve(
        listing_external_id="1234567890",
        asset_type="commercial",
        deal_type="rent",
        evidence_types=["comparable_candidate", "finding"],
        limit=10,
    )
```

Default retrieval excludes expired, low-confidence, and non-reusable items and orders deterministically by reuse eligibility, freshness, confidence, checked time, and id.

## Production smoke without real providers

1. Deploy migration and verify Alembic head/current.
2. Record baseline counts for alerts, agent tasks, listing analyses, knowledge notes, listing enrichments, market research runs, and market evidence items.
3. Create a fake successful `market_research` AgentTask whose `result_json` passes the PR14 schema validator: two sources, one comparable, one finding, one `market_assumptions_to_verify`, confidence above `0.7`.
4. Run `python3 -m app.cli ingest-market-evidence --task-id <id>`.
5. Confirm one run and source-linked evidence items were created, no `evidence_type=source` row exists, normalized URLs and confidence/freshness fields are set, and re-ingestion reuses items.
6. Run the retriever and `MarketResearchRagContextBuilder`; confirm bounded read-only context and no writes to `knowledge_notes`.
7. Confirm no alerts, listing analyses, listing enrichments, search jobs, or follow-up agent tasks changed beyond the temporary smoke task.
8. Clean up temporary smoke rows.

Rollback is the Alembic downgrade for `0013_market_evidence_storage`, which drops `market_evidence_items` and `market_research_runs`.

## PR16 consumption by investment profiles

PR16 consumes SQL-backed market evidence for investment scoring only when explicitly enabled with `use_market_evidence=true`. It does not create or mutate market research runs or evidence items, does not create agent tasks, and does not call external research, LLMs, `ResearchAgent`, embeddings, vector DB, GIS/geocoding, radius, fuzzy, or semantic matching during scoring.

Cross-listing evidence reuse is not implemented. Selection always starts from evidence for the target `listing_external_id`; `market_evidence_location_key` only narrows within that same listing evidence set. Broad city-wide and location-level reuse are future scope.

Eligible rent comps are reusable `comparable_candidate` items for the same listing, matching the investment profile asset type (`commercial` or `flat`), with `deal_type=rent`, source URL, rent metric, confidence above the configured threshold, not expired, and checked within the configured max age. The selected evidence fingerprint is included in the analysis `input_hash`, so selected evidence changes churn the hash while unrelated non-selected evidence does not.

## PR16b matching policy bridge

PR16 used same-listing market evidence only. PR16b adds a narrow deterministic matching policy layer and does not replace PR24 comparable quality scoring or PR25 comparable selection policy v2.

Supported policies:

* `same_listing` — effective default when `use_market_evidence=true` and `market_evidence_matching_policy` is unset. Selection is limited to the target `listing_external_id`; an optional `market_evidence_location_key` only narrows that same-listing set.
* `same_location_key` — opt-in cross-listing reuse. It requires explicit `market_evidence_location_key` and can select stored rent comps from other `listing_external_id` values only when their `location_key` exactly equals the configured key.

No fuzzy matching, semantic matching, embeddings, vector search, GIS/geocoding, radius search, inferred district/address matching, or broad city-wide evidence is used. Scoring still reads existing SQL-backed `market_evidence_items` only; it does not call an LLM, `ResearchAgent`, live external research, or mutate market evidence.

The matching policy and selected evidence fingerprint are part of `input_hash`. Fingerprinted item fields include id, listing id, content hash, confidence, checked/expires UTC date bucket, normalized source URL, asset/deal type, location key, and rent metrics. This keeps replay behavior deterministic: policy changes or selected evidence changes churn the hash, while irrelevant non-selected evidence does not.

Because PR16b has no full comparable quality scoring, same-location-key cross-listing evidence cannot produce a strong verdict when used as the rent source. Facts record `matching_policy`, `cross_listing_reuse_enabled`, `comp_quality_scoring_used=false`, selected listing ids, same/external listing counts, distinct source/listing counts, excluded counts, and whether a cross-listing verdict cap was applied. Manual rent remains primary; weak or missing cross-listing evidence does not degrade manual-primary calculations.

## PR26 adjusted comparable model v0

PR26 adds deterministic adjusted comparable model v0 after PR25 selection policy v2 and PR24 comparable quality scoring v0. It does not replace either layer: only PR25-selected and PR24-accepted comps can enter adjusted median calculations, and rejected/unusable comps stay excluded.

The model lives in `app.analysis.market_comps.adjust_comparable_rents`. It is pure and deterministic: callers pass `as_of`; the helper performs no DB, external API, LLM, agent, parser, alert, or current-time calls. The model and config versions are `ADJUSTED_COMPARABLE_MODEL_VERSION = "v0"` and `ADJUSTED_COMPARABLE_CONFIG_VERSION = "v0"`; adjustment-relevant fields and versions are included in the market-evidence fingerprint so input hashes churn when adjusted-comparable inputs change.

Rent basis is strictly monthly rent and monthly rent-per-m2. PR26 adjusts rent-per-m2 first: derive raw rent-per-m2 from `rent_per_m2_rub`, or from monthly total rent plus comp area; sum additive percentage deltas; cap each dimension and total delta; compute adjusted rent-per-m2; then derive adjusted total rent from target area when target area exists. It does not calculate adjusted medians from raw total rents of differently sized comps.

Explicit v0 dimensions and constants are area mismatch, condition/capex, first-line, floor/access, asking-to-effective discount, and freshness confidence penalty. Adjustment directions convert comp evidence to target-equivalent rent: comp advantage versus target adjusts down; target advantage versus comp adjusts up; same signal is unchanged; unknown structured signals add flags/review reasons without value adjustment. Freshness affects confidence/review only, not rent value. Asking-to-effective discount applies only to explicit asking evidence; confirmed/effective evidence receives no discount; unknown source type is flagged and not discounted.

Facts include compact `adjusted_comparables` with raw comp rent, raw rent-per-m2, adjusted rent-per-m2, adjusted total rent when target area exists, adjustment reasons/flags, raw and adjusted medians, confidence/confidence cap, whether the adjusted median was used, and review reasons. Item facts are capped and contain no raw payloads, secrets, notifier URLs, or delivery URLs. Old analyses without `adjusted_comparables` remain compatible.

Manual rent/manual assumptions remain primary. Adjusted median can be used as the comp-derived rent estimate only when manual rent is absent, evidence is sufficient, target area exists, and quality/confidence gates are satisfied. The adjusted median is internal investment screening and decision-support only: it is not a certified appraisal, valuation opinion, automated investment advice, source verification (PR27), sale/cap-rate evidence (PR28), scenario engine, DCF, financing/tax model, hidden ML, or professional appraisal claim.
