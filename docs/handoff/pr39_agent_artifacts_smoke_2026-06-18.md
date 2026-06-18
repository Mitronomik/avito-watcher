# PR39 production smoke handoff - Agent artifacts / blackboard v0

## PR

PR39 - Agent artifact layer / blackboard v0

Merged PR:

- #243 - Add agent artifact blackboard storage

Production commit:

- 876ad03 Add agent artifact blackboard storage (#243)

Deploy date:

- 2026-06-18

## Deploy summary

Production was updated from:

- 1ba4908 Add agent task orchestration metadata (#241)
- 8af8ab1 Add PR38 production smoke handoff (#242)

to:

- 876ad03 Add agent artifact blackboard storage (#243)

Docker images rebuilt:

- app
- worker

Services restarted:

- app
- worker

Observed services:

- postgres healthy
- redis healthy
- app running
- worker running

## Migration

Migration applied successfully:

```text
0018_agent_task_orch_meta -> 0019_agent_artifacts
```

Alembic current/head after deploy:

```text
0019_agent_artifacts (head)
0019_agent_artifacts (head)
```

Status:

- PASS

## Health smoke

Health endpoint returned HTTP 200.

Observed response body:

```json
{"status":"ok"}
```

Status:

- PASS

## Database schema smoke

### Columns

Observed table:

```text
agent_artifacts
```

Observed columns:

```text
id
artifact_type
schema_version
listing_external_id
listing_analysis_id
search_job_id
context_key
source_task_id
orchestration_run_id
input_hash
content_hash
payload_json
source_refs_json
redaction_status
created_at
```

Observed column policy:

- required core fields are non-null
- optional context/reference fields are nullable
- `payload_json` uses SQLAlchemy JSON / PostgreSQL json
- `source_refs_json` uses SQLAlchemy JSON / PostgreSQL json
- `created_at` has server default `now()`

Status:

- PASS

### Constraints

Observed constraints:

```text
agent_artifacts_pkey
agent_artifacts_source_task_id_fkey
ck_agent_artifacts_artifact_type
ck_agent_artifacts_content_hash_not_empty
ck_agent_artifacts_input_hash_not_empty
ck_agent_artifacts_redaction_status
ck_agent_artifacts_schema_version_not_empty
```

Confirmed:

- artifact type is closed enum
- redaction status is closed enum
- input hash must be non-empty
- content hash must be non-empty
- schema version must be non-empty
- `source_task_id` references `agent_tasks(id)`
- no cascade delete behavior was introduced

Status:

- PASS

### Indexes

Observed indexes:

```text
agent_artifacts_pkey
ix_agent_artifacts_artifact_type
ix_agent_artifacts_content_hash
ix_agent_artifacts_context_key
ix_agent_artifacts_context_latest
ix_agent_artifacts_created_at
ix_agent_artifacts_input_hash
ix_agent_artifacts_listing_analysis_id
ix_agent_artifacts_listing_external_id
ix_agent_artifacts_orchestration_run_id
ix_agent_artifacts_search_job_id
ix_agent_artifacts_source_task_id
```

Status:

- PASS

### Row count

Observed:

```text
agent_artifacts_total: 0
```

Status:

- PASS

This confirms PR39 did not backfill historical agent task outputs into `agent_artifacts`.

## Admin meta smoke

Checked Admin API v1 meta contract with the production read key.

Confirmed:

- `agent_artifact_type` enum is present
- `agent_artifact_redaction_status` enum is present
- `agent_artifacts_read` capability is present and true
- artifact type enum exactly matches PR39 contract
- redaction status enum exactly matches PR39 contract
- no unsafe execution/action/auth/raw-payload metadata markers were present in the meta response

Observed smoke result:

```text
PR39_META_SMOKE_PASS
```

Status:

- PASS

## Agent artifacts read API smoke

Checked:

```text
GET /api/admin/v1/agent-artifacts?limit=10
```

Confirmed:

- endpoint returned Admin API success envelope
- response schema version is `agent-artifact-list-v1`
- `items` is a list
- current production response had `items 0`
- no raw payload/debug/provider/action/auth metadata markers were present in the response

Observed smoke result:

```text
PR39_AGENT_ARTIFACTS_READ_SMOKE_PASS items 0
```

Status:

- PASS

## Query-auth negative smoke

Checked query-param credential access for the agent artifacts read endpoint.

Observed:

```text
HTTP/1.1 403 Forbidden
```

Status:

- PASS

This confirms PR39 did not add query-param auth for the new read endpoint.

## Runner dry-run / no artifact creation smoke

Before runner dry-run:

```text
agent_artifacts_before: 0
agent_tasks_before: 2
```

Runner command returned:

```json
{
  "ok": true,
  "limit": 10,
  "task_type": null,
  "dry_run": true,
  "pending": 0,
  "tasks": []
}
```

After runner dry-run:

```text
agent_artifacts_after: 0
agent_tasks_after: 2
```

Status:

- PASS

This confirms:

- PR39 did not wire `AgentTaskRunner` to create artifacts
- no automatic artifact generation happened
- `agent_tasks` count stayed unchanged
- `agent_artifacts` count stayed unchanged

## No-side-effect check

Observed post-smoke counts:

```text
listings_total: 2384
listing_analyses_total: 730
human_reviews_total: 0
market_evidence_items_total: 0
alerts_sent_total: 4588
agent_tasks_total: 2
agent_artifacts_total: 0
```

Interpretation:

- `agent_artifacts_total` stayed `0`
- `agent_tasks_total` stayed `2`
- `human_reviews_total` stayed `0`
- `market_evidence_items_total` stayed `0`
- PR39 did not create artifacts, tasks, reviews, or market evidence

`listings_total` and `alerts_sent_total` changed while the production worker was running. This is treated as background monitor activity, not a PR39 artifact-layer side effect.

Status:

- PASS for PR39 artifact/read-model side effects

## Logs and sensitive marker grep

Checked recent app and worker logs for errors, stack traces, and common sensitive header/secret markers.

Observed:

```text
no matches
```

Status:

- PASS

## Confirmed PR39 boundaries

Confirmed:

- no AgentOrchestratorService
- no runtime orchestration
- no automatic dependent task creation
- no automatic artifact creation from AgentTaskRunner
- no migration/backfill from `AgentTask.result_json`
- no new agent handlers
- no LLM calls
- no external HTTP calls
- no RAG writes
- no report generation
- no offer generation
- no presentation generation
- no score mutation
- no verdict mutation
- no filters mutation
- no workflow state mutation
- no allowed actions mutation
- no AlertSent creation by PR39 smoke
- read endpoints are GET-only
- artifact table starts empty in production

## Operational notes

Server login banner reported maintenance items:

```text
system restart required
3 zombie processes
root filesystem usage 75.8%
10 updates available
1 standard security update available
```

These are operational maintenance items and not PR39 regressions. Do not address them in this docs-only PR.

## Next roadmap PR

Next after this handoff is merged:

- PR40 - AgentOrchestratorService v0

PR40 should introduce controlled runtime orchestration over the PR37/PR38/PR39 foundations.

PR40 must still preserve:

- deterministic core ownership of score/verdict
- no hidden score/verdict/filter/workflow/action mutation
- no monitor blocking by orchestration
- no uncontrolled agent chaining
- explicit caps, guards, dry-run visibility, and safe failure modes
- artifacts as trace/read-model outputs, not final decision authority
