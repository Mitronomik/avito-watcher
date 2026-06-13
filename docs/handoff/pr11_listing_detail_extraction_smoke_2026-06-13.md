# PR 11 — LLM structured extraction over persisted detail snapshots production smoke

PR 11 is merged, deployed, and production-smoked.

## Migration

* Alembic head before upgrade: `0012_listing_enrichments`.
* Alembic current before upgrade: `0011_listing_detail_snapshots`.
* Alembic upgrade applied successfully:

  * `0011_listing_detail_snapshots -> 0012_listing_enrichments`
* Alembic current after upgrade: `0012_listing_enrichments (head)`.

## Table verification

New table `listing_enrichments` exists.

Verified key fields:

* `listing_external_id`
* `listing_id`
* `enrichment_type`
* `source_type`
* `source_id`
* `status`
* `validation_status`
* `model`
* `provider`
* `prompt_version`
* `schema_version`
* `extraction_profile`
* `input_hash`
* `source_content_hash`
* `output_hash`
* `structured_facts_json`
* `field_confidence_json`
* `evidence_json`
* `missing_fields_json`
* `uncertain_fields_json`
* `contradictions_json`
* `warnings_json`
* `confidence`
* `error_type`
* `error_message`
* `started_at`
* `finished_at`
* `created_at`
* `updated_at`

Verified indexes:

* `ix_listing_enrichments_enrichment_type`
* `ix_listing_enrichments_extraction_profile`
* `ix_listing_enrichments_input_hash`
* `ix_listing_enrichments_listing_external_id`
* `ix_listing_enrichments_listing_id`
* `ix_listing_enrichments_output_hash`
* `ix_listing_enrichments_prompt_version`
* `ix_listing_enrichments_schema_version`
* `ix_listing_enrichments_source_content_hash`
* `ix_listing_enrichments_source_id`
* `ix_listing_enrichments_source_type`
* `ix_listing_enrichments_status`
* `ix_listing_enrichments_validation_status`
* `listing_enrichments_pkey`
* `uq_listing_enrichments_success_identity`

## App health

App health check returned:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

The earlier transient connection reset immediately after app restart was resolved by the next health check.

## Baseline before smoke

Before PR11 smoke:

* `alerts_sent_baseline_id = 2796`
* existing AgentTasks:

  * `review_copilot / success = 2`
* `knowledge_notes_count = 0`
* `detail_snapshots_count = 0`
* `listing_enrichments_count = 0`

## Controlled smoke setup

A temporary `listing_detail_snapshot` was created from static HTML through `ListingDetailEnrichmentService.persist_from_html(...)`.

No live fetch was performed.

Created smoke snapshot:

* `SNAPSHOT_ID = 2`
* `listing_external_id = pr11-smoke-2026-06-13`

Created explicit manual `listing_detail_extraction` AgentTasks only.

No automatic task creation was observed.

## Dry-run smoke

Command:

```text
python -m app.cli run-agent-tasks --task-type listing_detail_extraction --limit 10 --dry-run
```

Result:

* `ok = true`
* `dry_run = true`
* `pending = 2`
* tasks remained `pending`
* no provider call was made
* no task status/result mutation
* no `listing_enrichments` row was created

Verified:

* `smoke_enrichments = 0`

## Default disabled smoke

With default production config:

* `LLM_LISTING_DETAIL_EXTRACTION_ENABLED=false`

Manual task result:

* task id `4`
* status: `skipped`
* result error type: `listing_detail_extraction_disabled`
* message: `Listing detail extraction is disabled`

Verified:

* provider was not called
* no `listing_enrichments` row was created
* `smoke_enrichments = 0`

## Provider-off fail-closed smoke

Executed with:

```text
LLM_LISTING_DETAIL_EXTRACTION_ENABLED=true
LLM_PROVIDER=off
```

Manual task result:

* task id `5`
* status: `failed`
* error type: `listing_detail_extraction_provider_disabled`
* error message: `LLM provider is disabled for listing detail extraction`

Verified:

* no silent provider fallback
* no default client execution
* no `listing_enrichments` row was created
* `smoke_enrichments = 0`

## Unsupported-provider fail-closed smoke

Executed with:

```text
LLM_LISTING_DETAIL_EXTRACTION_ENABLED=true
LLM_PROVIDER=ollama
```

Manual task result:

* task id `6`
* status: `failed`
* error type: `listing_detail_extraction_provider_unsupported`
* error message: `Unsupported LLM provider for listing detail extraction: ollama`

Verified:

* unsupported provider fails closed
* no silent fallback
* no `listing_enrichments` row was created
* `smoke_enrichments = 0`

## Optional real LLM success smoke

A real enabled extraction smoke was attempted.

Manual task result:

* task id `7`
* status: `failed`
* error type: `listing_detail_extraction_schema_validation_failed`
* error message: `Invalid field confidence`

Verified:

* no invalid enrichment row was created
* `listing_enrichments` remained empty for the smoke listing

Conclusion:

* Real-provider success path was not confirmed in this smoke.
* Strict validation and fail-closed behavior were confirmed.
* This is not a blocker for PR11 substrate safety because invalid model output did not persist.

A second real LLM task was created but not executed before cleanup:

* task id `8`
* status before cleanup: `pending`

## No side effects

After mandatory safety smoke:

* no alerts were created after baseline `2796`;
* `knowledge_notes_count = 0`;
* `smoke_detail_snapshots = 1` before cleanup;
* `smoke_enrichments = 0`;
* no scoring changes were observed;
* no verdict changes were observed;
* no RAG usage was observed;
* no ReviewCopilot changes were observed;
* no live fetch was performed by PR11 extraction smoke;
* no automatic extraction tasks were created by monitor.

## Worker runtime smoke

Worker logs were checked after PR11 deploy.

Observed:

* worker started normally;
* runtime diagnostics printed normally;
* repeated `monitor cycle completed`;
* repeated `monitor_service.cycle_summary`;
* no traceback;
* no browser driver crashes;
* no engine errors;
* no proxy failures;
* no PR11 automatic extraction runtime activity;
* no monitor integration for `listing_detail_extraction`.

Worker runtime remains stable.

## Cleanup

Temporary smoke data was deleted from:

* `listing_enrichments`
* `agent_tasks`
* `listing_detail_snapshots`

Cleanup result:

* deleted smoke AgentTasks:

  * id `4`, status `skipped`, context `pr11-smoke:disabled`
  * id `5`, status `failed`, context `pr11-smoke:provider-off`
  * id `6`, status `failed`, context `pr11-smoke:unsupported-provider`
  * id `7`, status `failed`, context `pr11-smoke:real-llm-success`
  * id `8`, status `pending`, context `pr11-smoke:real-llm-success`
* deleted smoke snapshot:

  * id `2`
  * listing_external_id `pr11-smoke-2026-06-13`
  * title `PR11 smoke помещение 42 м²`

Post-cleanup checks:

* `remaining_smoke_enrichments = 0`
* `remaining_smoke_tasks = 0`
* `remaining_smoke_snapshots = 0`

## Conclusion

PR11 production smoke is closed for the required safety scope.

Confirmed:

* migration applied;
* table and indexes exist;
* app health is OK;
* worker runtime is OK;
* manual AgentTask dry-run is safe;
* default disabled mode is safe;
* provider `off` fails closed;
* unsupported provider fails closed;
* failed/skipped attempts do not create enrichment rows;
* invalid real LLM output fails closed;
* no alerts/scoring/verdict/RAG side effects;
* smoke data cleaned up.

Not confirmed:

* successful real-provider extraction path in production.

Follow-up note:

* Real LLM success can be revisited later by improving prompt/schema tolerance or provider output normalization.
* This should not be mixed into PR11 after merge unless it becomes a blocker for the next roadmap step.

Next roadmap step:

```text
PR12 — DataQualityAgent with RAG
```
