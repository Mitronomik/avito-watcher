# Agent artifacts / blackboard v0 (PR39)

PR39 adds an internal, append-only blackboard for future agent outputs. It stores typed, traceable intermediate artifacts for deterministic services and future Admin API display. It does not introduce runtime orchestration, new agents, scoring changes, alerts, monitor integration, RAG writes, LLM calls, report generation, offer generation, or presentation generation.

## Table

`agent_artifacts` contains:

- `id` primary key.
- `artifact_type` closed enum.
- `schema_version` non-empty artifact schema version.
- Context refs: `listing_external_id`, `listing_analysis_id`, `search_job_id`, `context_key`.
- PR38 refs: `source_task_id` nullable FK to `agent_tasks.id`, and `orchestration_run_id`.
- `input_hash` explicit non-empty hash of caller-provided production context.
- `content_hash` non-empty deterministic hash of `artifact_type`, `schema_version`, and canonical payload.
- `payload_json` safe JSON object envelope.
- `source_refs_json` safe PR37-compatible refs.
- `redaction_status` closed enum.
- `created_at` creation timestamp.

`listing_analysis_id` and `search_job_id` are nullable indexed context integers, not FKs. `listing_external_id` is an indexed string reference. `source_task_id` has no cascade delete because artifacts are trace records.

## Artifact types

Exact PR39 values are: `evidence_candidates`, `normalized_evidence`, `data_gap_report`, `call_questions`, `decision_wording`, `claim_review`, `report_draft`, `offer_draft`, `presentation_outline`, `geo_context`, and `portfolio_memory_finding`.

PR39 only defines these types. Future PR41+ agents own producing evidence artifacts; PR43 owns `data_gap_report`; later report/export PRs own report, offer, and presentation drafts; ClaimGuard owns `claim_review`; geocoding/map PRs own `geo_context`.

## Redaction statuses

Exact values are:

- `not_required` — only safe structured data is present.
- `redacted` — unsafe/raw/private parts were removed.
- `blocked` — payload exists but must not be displayed.
- `unknown` — display conservatively.

Creation requires an explicit redaction status. Read DTOs hide payloads for `blocked` and `unknown`.

## Hashing policy

`input_hash` is accepted from the caller and represents the artifact-producing input context. The service also exposes a helper to hash a safe caller-provided input envelope. It is not inferred from `payload_json`.

`content_hash` is deterministic from `artifact_type`, `schema_version`, and canonicalized `payload_json` using sorted compact JSON. It excludes `created_at` and other row metadata.

## Payload envelope and safety

PR39 accepts only a conservative top-level envelope:

```json
{
  "schema_version": "agent-artifact-schema-v1",
  "artifact_type": "evidence_candidates",
  "result_kind": "artifact_payload",
  "summary": null,
  "items": [],
  "limitations": [],
  "confidence": null,
  "notes": [],
  "metadata": {}
}
```

Unknown top-level keys are rejected. Unsafe key paths such as score/verdict mutation keys, alert keys, appraisal/advice keys, raw provider payload, debug HTML, headers, cookies, authorization, API keys, tokens, secrets, passwords, and webhook URLs are rejected by key/path checks. Safe limitation codes such as `not_investment_advice` and `guaranteed_yield_claim_blocked` are allowed.

## Source refs

`source_refs_json` accepts a list or object using the PR37 source-ref vocabulary where possible: listing refs, analysis refs, search job refs, agent task refs, human review refs, market evidence ids, deterministic input hashes, knowledge note ids, and future artifact/task refs.

PR39 additionally permits only bounded artifact-specific keys such as `source_kind`, `source_ref_id`, `source_hash`, `url_hash`, `source_checked_at`, `source_expires_at`, `source_confidence`, and `note`. Raw URLs, headers, cookies, tokens, provider payloads, debug HTML, and free-form `table`/`field` refs are rejected.

## Preview and read model

The read DTO version is `agent-artifact-v1`. Payload previews are bounded to 5 items, 300 characters per string, and about 2000 total characters. Serialization composes artifact preview rules with the canonical Admin API helper `app.api.admin_v1.redaction.redact_api_response`; no second redaction layer is introduced.

Admin read endpoints are read-only:

- `GET /api/admin/v1/agent-artifacts`
- `GET /api/admin/v1/agent-artifacts/{artifact_id}`

They use existing Admin API v1 read auth and response envelopes. They do not expose raw `payload_json`, execution endpoints, HTTP methods, auth params, raw result JSON, provider payloads, debug HTML, headers, cookies, tokens, or secrets.

## Append-only behavior

`create_agent_artifact(...)` inserts a new row by default. `find_duplicate_agent_artifact(...)` checks duplicate identity by `artifact_type`, `content_hash`, `listing_external_id`, `context_key`, and `source_task_id`. It never deletes, updates, or overwrites existing rows. `get_latest_agent_artifact(...)` orders by `created_at desc, id desc`.

## Production smoke plan

After deploy, run migrations and verify one Alembic head. Check `agent_artifacts` columns, constraints, indexes, FK, and row count through SQL. Smoke `/api/admin/v1/meta` for artifact enums and labels, then read `/api/admin/v1/agent-artifacts?limit=10` with the Admin read key. Prefer read-only production smoke and do not insert production artifacts unless using a rollback transaction. Compare before/after counts for agent artifacts, agent tasks, alerts, listing analyses, human reviews, and market evidence items. Inspect app/worker logs for errors and secret/header leaks.

## Ownership boundaries

PR40 owns runtime orchestration and artifact handoff between tasks. PR41+ own actual artifact-producing agents. PR39 does not alter deterministic scoring, verdicts, filters, workflow actions, monitoring, alerting, `AgentTaskRunner`, `AgentTask.status`, `AgentTask.result_json`, orchestration status, or dependency status.
