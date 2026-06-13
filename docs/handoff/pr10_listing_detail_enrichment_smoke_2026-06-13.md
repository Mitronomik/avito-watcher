# PR 10 — Listing detail enrichment snapshots production smoke

PR 10 — Listing detail enrichment snapshots is merged, deployed, and production-smoked.

## Production checks

- Alembic head after deploy: `0011_listing_detail_snapshots`.
- App starts successfully.
- `/health` returns `{"status":"ok"}`.
- New table `listing_detail_snapshots` exists.
- Required fields exist:
  - `listing_external_id`
  - `source_kind`
  - `fetch_status`
  - `parse_status`
  - `content_hash`
  - `description_text`
  - `raw_text_excerpt`
- Unique constraint exists for idempotency by `listing_external_id + content_hash`.
- `listing_id` is nullable by design.
- `listing_external_id` is required by design.
- `fetch_status` and `parse_status` are string enum-like v0 fields by design.

## Controlled service smoke

A temporary snapshot was created through `ListingDetailEnrichmentService.persist_from_html(...)` using provided static HTML only.

No live fetch was performed.

Expected result:

- `status=created` on first run.
- `status=existing` on repeated same-content run.
- `fetch_status=not_applicable`.
- `parse_status=success`.
- `source_kind=fixture`.
- `listing_external_id=pr10-smoke-2026-06-13`.
- `description_text` redacts contact-like values.
- `raw_text_excerpt` redacts contact-like values.
- `seller_name` does not persist raw contact-like values.
- canonical URL excludes volatile query params.
- repeated same content does not create duplicate snapshot.

## Verified no side effects

- No alerts were created by PR10 smoke.
- No AgentTasks were created by PR10 smoke.
- `knowledge_notes` was not mutated.
- Monitor/worker runtime was not connected to detail enrichment.
- No LLM calls were involved.
- No ReviewCopilot/RAG changes were involved.
- No scoring/verdict changes were involved.
- No live fetching/CLI/background job was introduced.

The temporary PR10 smoke snapshot was deleted after verification.

## Conclusion

PR10 production smoke is closed.

Next roadmap step:

PR11 — LLM structured extraction over persisted detail snapshots.
