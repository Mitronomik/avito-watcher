# Listing detail enrichment snapshots (PR10)

PR10 adds deterministic listing detail enrichment snapshots as a clean-data substrate for later structured extraction.
It stores bounded public facts parsed from provided/static/existing HTML or text-like HTML inputs.

## What PR10 does

- Adds `listing_detail_snapshots` as append-friendly evidence storage.
- Adds source provenance with `source_kind` (`fixture`, `existing_parser_payload`, `manual`, `cached_html`).
- Keeps `fetch_status` separate from `parse_status`.
- Computes deterministic `content_hash` from normalized extracted fields only.
- Persists snapshots idempotently by `(listing_external_id, content_hash)`.
- Returns bounded diagnostics through `DetailEnrichmentResult`.

## Non-goals and safety boundaries

PR10 does not perform live fetching, does not add CLI commands, and does not connect to monitor runtime or worker cadence.
It does not add LLM extraction, ReviewCopilot changes, RAG changes, external research, embeddings, vector DB, Postgres full-text search, scoring/verdict changes, alert changes, search matching changes, or browser bypass behavior.
It does not store raw HTML and does not scrape phone numbers or hidden contact data. Contact-like values that appear inside persisted free-text fields are conservatively redacted as `[redacted_contact]`.

## Status semantics

`fetch_status` describes input acquisition. In PR10 static/service calls normally use `not_applicable` because no network fetch is performed.
Allowed values are service-level enum-like strings such as `success`, `failed`, `skipped`, and `not_applicable`. PR10 intentionally stores these as bounded enum-like strings without DB check constraints; current service/parser paths emit only expected values, and DB-level constraints can be added later if the project standardizes status enums across enrichment tables.

`parse_status` describes deterministic extraction from the provided input: `success`, `partial`, `failed`, or `skipped`.
A provided page can have `fetch_status=not_applicable` and `parse_status=success`.

## Content hash semantics

`content_hash` excludes timestamps, errors, request metadata, volatile URL query parameters, and raw HTML.
It includes normalized extracted content such as title, description, address, metro, price, area, publication label/date, seller type, category, attributes, facts, and photos count with stable JSON key ordering.

## Schema overview

The snapshot table stores listing references (`listing_id`, `listing_external_id`, `listing_url`), source provenance (`source_url`, `canonical_url`, `source_host`, `source_kind`), statuses and parser metadata, bounded public fields, bounded JSON attributes/facts, diagnostics, and timestamps.
`listing_external_id` is the required audit and idempotency key for PR10. `listing_id` is intentionally nullable and has no foreign-key requirement, so orphan snapshots by external id are allowed in this substrate phase rather than introducing an inconsistent FK pattern; `listing_id` can be filled later if the project standardizes internal FK usage for enrichment tables.

## Follow-up

PR11 will add LLM structured extraction using these persisted detail snapshots as bounded evidence context.
