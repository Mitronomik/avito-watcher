# PR 12 — DataQualityAgent with RAG production smoke

PR12 is merged, deployed, and production-smoked.

## Scope

PR12 added a manual, diagnostic-only `data_quality_agent` AgentTask handler.

The agent is shadow-only:

* does not change deterministic scoring;
* does not change verdicts;
* does not send alerts;
* does not mutate listings;
* does not mutate `listing_analyses`;
* does not mutate `listing_detail_snapshots`;
* does not mutate `knowledge_notes`;
* does not create automatic tasks;
* does not connect to monitor runtime;
* does not use external research.

Successful validated assessments are stored in `listing_enrichments` with:

```text
enrichment_type = data_quality_assessment
```

No DB migration was added in PR12. It reuses `listing_enrichments` from PR11.

## Deployment

App and worker images were rebuilt successfully.

Containers started successfully:

* `deploy-postgres-1` healthy;
* `deploy-redis-1` healthy;
* `deploy-app-1` started;
* `deploy-worker-1` started.

## Alembic

Checked heads and current revision.

Result:

```text
0012_listing_enrichments (head)
```

This is expected. PR12 did not add a migration.

## App health

Health check returned:

```text
HTTP/1.1 200 OK
```

## Baseline before smoke

Baseline:

```text
alerts_sent_baseline_id = 2804
```

Initial AgentTasks:

```text
review_copilot / success = 2
```

Initial enrichments:

```text
listing_enrichments = 0 rows
```

Initial RAG notes:

```text
knowledge_notes_count = 0
```

## Controlled smoke data

Created temporary smoke listing:

```text
listing_external_id = pr12-smoke-2026-06-13
listing_id = 1494
title = PR12 smoke помещение 42 м²
```

Created three manual AgentTasks:

```text
id 9  — pr12-smoke:disabled
id 10 — pr12-smoke:provider-off
id 11 — pr12-smoke:unsupported-provider
```

## Dry-run smoke

Command:

```text
python -m app.cli run-agent-tasks --task-type data_quality_agent --limit 10 --dry-run
```

Result:

```text
ok = true
dry_run = true
pending = 3
```

Verified:

* all three tasks remained `pending`;
* no provider call was made;
* no task status mutation;
* no `listing_enrichments` row was created.

Result:

```text
smoke_enrichments = 0
```

## Default disabled smoke

Ran one `data_quality_agent` task with default production settings.

Expected default:

```text
LLM_DATA_QUALITY_AGENT_ENABLED=false
```

Result:

```text
task id = 9
status = skipped
error_type = data_quality_agent_disabled
message = DataQualityAgent is disabled
```

Verified:

* provider was not called;
* no `listing_enrichments` row was created.

## Provider-off fail-closed smoke

Ran one task with:

```text
LLM_DATA_QUALITY_AGENT_ENABLED=true
LLM_PROVIDER=off
```

Result:

```text
task id = 10
status = failed
error_type = data_quality_agent_provider_disabled
message = LLM provider is disabled
```

Verified:

* provider-off fails closed;
* no fallback provider was used;
* no `listing_enrichments` row was created.

## Unsupported-provider fail-closed smoke

Ran one task with:

```text
LLM_DATA_QUALITY_AGENT_ENABLED=true
LLM_PROVIDER=ollama
```

Result:

```text
task id = 11
status = failed
error_type = data_quality_agent_provider_unsupported
message = Unsupported LLM provider: ollama
```

Verified:

* unsupported provider fails closed;
* no silent fallback;
* no `listing_enrichments` row was created.

## No side effects

Checked after mandatory smoke.

Alerts:

```text
alerts after baseline 2804 = 0 rows
```

AgentTasks:

```text
data_quality_agent / skipped = 1
data_quality_agent / failed = 2
review_copilot / success = 2
```

Enrichments:

```text
listing_enrichments = 0 rows
```

RAG notes:

```text
knowledge_notes_count = 0
```

Smoke enrichments:

```text
smoke_enrichments = 0
```

Confirmed:

* no alerts were created;
* no data quality assessment row was created during mandatory fail-closed smoke;
* no RAG notes were mutated;
* no scoring/verdict side effects were observed.

## Worker runtime smoke

Worker logs were checked after deploy.

Observed:

* worker started normally;
* runtime diagnostics printed normally;
* repeated `monitor cycle completed`;
* repeated `monitor_service.cycle_summary`;
* no traceback;
* no browser driver crash;
* no engine errors;
* no blocks;
* no proxy failures;
* no automatic `data_quality_agent` task creation;
* no monitor integration for DataQualityAgent.

Worker runtime remains stable.

## Cleanup

Deleted smoke rows.

Deleted AgentTasks:

```text
id 9  — data_quality_agent — skipped — pr12-smoke:disabled
id 10 — data_quality_agent — failed  — pr12-smoke:provider-off
id 11 — data_quality_agent — failed  — pr12-smoke:unsupported-provider
```

Deleted smoke listing:

```text
id = 1494
external_id = pr12-smoke-2026-06-13
title = PR12 smoke помещение 42 м²
```

Post-cleanup checks:

```text
remaining_smoke_enrichments = 0
remaining_smoke_tasks = 0
remaining_smoke_listing = 0
```

## Conclusion

PR12 mandatory production smoke is closed.

Confirmed:

* no new migration required;
* Alembic remains at `0012_listing_enrichments (head)`;
* app health is OK;
* worker runtime is OK;
* manual dry-run is safe;
* DataQualityAgent is disabled by default;
* provider `off` fails closed;
* unsupported provider fails closed;
* failed/skipped tasks do not create enrichment rows;
* no alerts were created;
* no RAG notes were mutated;
* no scoring/verdict side effects were observed;
* smoke data was cleaned up.

Not confirmed in this smoke:

* successful real-provider `data_quality_assessment` row creation.

This is not a blocker for PR12 mandatory safety smoke. The mandatory smoke intentionally validated safety, fail-closed behavior, manual-only execution, no side effects, and cleanup.

Optional future check:

* run a controlled successful `data_quality_agent` assessment with a safe provider configuration and verify one valid `data_quality_assessment` row is created idempotently.

## Optional successful DataQualityAgent persistence smoke

After mandatory fail-closed smoke, an optional successful persistence smoke was executed with a local fake in-memory DataQualityAgent client.

No code changes were made.

Smoke listing:

```text
listing_external_id = pr12-success-smoke-2026-06-13
```

Result:

```text
FIRST_ENRICHMENT_ID = 1
SECOND_ENRICHMENT_ID = 1
CREATED_FIRST = True
CREATED_SECOND = False
CLIENT_CALLS = 1
ENRICHMENT_TYPE = data_quality_assessment
STATUS = success
VALIDATION_STATUS = valid
OVERALL_STATUS = needs_review
```

Verified persisted row:

```text
enrichment_type = data_quality_assessment
status = success
validation_status = valid
model = fake-data-quality-smoke
provider = openai_compatible
prompt_version = data-quality-agent-v1
schema_version = data-quality-assessment-schema-v1
extraction_profile = commercial_rent
confidence = 0.7
```

This confirms:

* successful `data_quality_assessment` row creation;
* validated schema persistence;
* idempotent reuse of the same enrichment row;
* no duplicate enrichment on repeated same input;
* provider/client called only once.

Cleanup:

```text
deleted listing_enrichment id = 1
deleted smoke listing id = 1495
```

Conclusion:

```text
PR12 successful persistence and idempotency path confirmed.
```


Next roadmap step:

```text
PR13 — investment profiles v0 with manual assumptions
```
