# EvidenceCollectorAgent v0

EvidenceCollectorAgent v0 implements the `evidence_collector_future` task type as the first controlled artifact-producing root for `listing_evidence_pipeline`.

## Purpose and scope

The agent collects safe internal evidence candidates around one listing. It is a candidate/read-model producer only: it does not create deterministic truth, normalized evidence, market evidence rows, score inputs, verdict inputs, or blocking decision logic.

## Inputs and allowed data sources

Allowed internal data sources are:

- `Listing` by `AgentTask.listing_external_id`.
- `ListingAnalysis` by `AgentTask.listing_analysis_id` when present and matching the listing.
- Safe task metadata such as `context_key`, `search_job_id`, and orchestration ids.

It does not call external HTTP services, LLM providers, RAG stores, browsers, parsers, Avito pages, or market evidence/comps read models.

## Artifact output

Successful runs create or reuse exactly one append-only PR39 `AgentArtifact`:

- `artifact_type`: `evidence_candidates`
- `schema_version`: `evidence-candidates-v0`
- `redaction_status`: `not_required`

The payload uses the PR39 safe envelope only:

```json
{
  "schema_version": "evidence-candidates-v0",
  "artifact_type": "evidence_candidates",
  "result_kind": "artifact_payload",
  "summary": "Collected internal evidence candidates for listing.",
  "items": [],
  "limitations": [],
  "confidence": 0.0,
  "notes": [],
  "metadata": {
    "collector_version": "evidence-collector-v0",
    "listing_external_id": "...",
    "listing_analysis_id": null,
    "search_job_id": null,
    "context_key": null,
    "candidate_count": 0,
    "missing_data": []
  }
}
```

Candidates are stored in `items`, capped at five, and use deterministic ids such as `listing_snapshot:<listing_external_id>` and `listing_analysis:<listing_analysis_id>`.

## Source refs

Artifact-level and candidate-level source refs are structured dict/list refs, never string references. Minimum refs include `agent_task_id` and `listing_external_id`; `listing_analysis_id` and `search_job_id` are included only when present.

## Idempotency and skip policy

A task that already has a successful `result_json.artifact_id` for `evidence_candidates` returns that safe summary and creates no new artifact. New successful runs compute PR39 input/content hashes and check `find_duplicate_agent_artifact` before creating an artifact.

Skip policy:

- Missing `task.listing_external_id`: skipped, no artifact.
- Missing listing row: skipped, no artifact.
- Existing listing with insufficient evidence: success with an empty `evidence_candidates` artifact and stable missing-data/limitation codes.

## Safety boundaries

EvidenceCollectorAgent writes only `AgentArtifact` evidence candidates through PR39 helpers and a small safe `AgentTask.result_json` summary. It does not mutate score, verdict, filters, workflow state, allowed actions, alerts, human reviews, market evidence items, or listing analyses. It has no monitor trigger, no dependent task auto-creation, and no external calls by default.

## Pipeline participation

In `listing_evidence_pipeline`, the root `evidence_collector` node is now enqueueable when orchestration is enabled. The downstream `evidence_normalizer_future` node remains unimplemented and is not enqueued automatically; the workflow contract remains not fully implemented until the end-to-end pipeline exists.

## Production smoke checklist

After merge/deploy:

1. Verify git HEAD is the PR41 merge commit.
2. Build app/worker images.
3. Confirm `alembic current` equals heads.
4. Confirm health endpoint is OK.
5. Confirm meta shows `evidence_collector_future` implemented.
6. Confirm the orchestrator plan shows `evidence_collector` implemented and `evidence_normalizer` unimplemented.
7. Confirm disabled enqueue creates no task.
8. Confirm runner dry-run still works.
9. Confirm no monitor integration.
10. Grep logs/config output for secrets/debug payload markers.

Prefer dry-run/no-side-effect checks. If enabled enqueue or artifact creation is tested in production, run it only inside an explicit transaction with rollback or with synthetic rows that are cleaned up in the same smoke procedure. Never leave smoke `AgentTask` or `AgentArtifact` rows in production and do not create production artifacts from real listings unless explicitly reviewed.
