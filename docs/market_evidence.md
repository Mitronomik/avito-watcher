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
