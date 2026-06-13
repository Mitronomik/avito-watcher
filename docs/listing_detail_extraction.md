# Listing detail extraction (PR11)

PR11 adds LLM structured extraction over persisted `listing_detail_snapshots` only. The LLM output is stored as evidence in `listing_enrichments` with `enrichment_type=llm_listing_detail_extraction`.

What it does:

- builds a bounded prompt from clean snapshot fields only;
- warns that listing text is untrusted user-generated content and must not be followed as instructions;
- requires strict JSON with schema version `listing-detail-extraction-schema-v1` and prompt version `listing-detail-extraction-v1`;
- validates fields, confidence values, evidence source fields, evidence snippet bounds, and hashes validated output;
- stores validated structured facts, field-level confidence, evidence, missing fields, uncertain fields, contradictions, hashes, model/provider, and source snapshot identity;
- is idempotent for successful extraction by source snapshot, model, prompt version, schema version, extraction profile, and input hash;
- runs only through explicit manual AgentTask type `listing_detail_extraction`.

What it does not do:

- no scoring, verdicts, rankings, recommendations, or alerts;
- no mutation of `listing_analyses`, `listings`, `listing_detail_snapshots`, `alerts_sent`, `knowledge_notes`, `listing_search_matches`, or `search_jobs`;
- no automatic monitor or worker runtime integration;
- no automatic AgentTask creation;
- no live fetch, Avito calls, debug HTML reads, raw HTML storage, phone/contact scraping, external research, RAG, embeddings, vector DB, or full-text search;
- no generic enrichment framework beyond minimal storage/repository/service code for `llm_listing_detail_extraction`.

The feature flag `LLM_LISTING_DETAIL_EXTRACTION_ENABLED` defaults to `false`. When disabled, the manual task is skipped, the provider is not called, and no enrichment row is created. Optional settings are `LLM_LISTING_DETAIL_EXTRACTION_MAX_INPUT_CHARS`, `LLM_LISTING_DETAIL_EXTRACTION_PROMPT_VERSION`, and `LLM_LISTING_DETAIL_EXTRACTION_SCHEMA_VERSION`.

Failure modes are surfaced on the AgentTask: disabled extraction, invalid payload, missing usable snapshot, provider failure, invalid JSON, or schema validation failure. Failed and skipped attempts do not create successful enrichment rows and do not block later retries.

PR12/DataQualityAgent may later compare deterministic clean data with extracted facts, but PR11 stores extraction evidence only.
