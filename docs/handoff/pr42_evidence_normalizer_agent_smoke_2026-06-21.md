# PR42 — EvidenceNormalizerAgent v0 production smoke handoff

Date: 2026-06-21

## Scope

This handoff documents the production deployment and smoke verification for PR42:

- PR #250 — `EvidenceNormalizerAgent v0`
- Merge commit: `82fd0b1 Add EvidenceNormalizer agent to convert evidence_candidates into normalized_evidence (#250)`

PR42 implements the `evidence_normalizer_future` agent task handler. It normalizes PR41 `evidence_candidates` artifacts into PR39 `agent_artifacts` using:

```text
artifact_type = normalized_evidence
schema_version = normalized-evidence-candidates-v0
result_kind = normalized_evidence
metadata.normalization_kind = normalized_evidence_candidates
```

The PR is intentionally artifact-to-artifact only. It does not write market evidence, scoring, verdicts, workflow state, allowed actions, alerts, human reviews, or listing analyses.

## Deployment summary

Production was updated to commit:

```text
82fd0b1 Add EvidenceNormalizer agent to convert evidence_candidates into normalized_evidence (#250)
b597539 Add PR41 production smoke handoff (#249)
b7641cb Align evidence collector artifact result_kind (#248)
6051712 Add EvidenceCollectorAgent v0 (#247)
a2cec88 Add PR40 production smoke handoff (#246)
c68af4c Add AgentOrchestratorService v0 (#245)
2913e16 Add PR39 production smoke handoff (#244)
876ad03 Add agent artifact blackboard storage (#243)
```

Changed files deployed with PR42 included:

```text
app/agents/evidence_normalizer_agent.py
app/agents/registry.py
app/services/agent_task_runner.py
docs/evidence_normalizer_agent.md
docs/handoff/pr41_evidence_collector_agent_smoke_2026-06-19.md
tests/test_agent_orchestrator_service.py
tests/test_evidence_collector_agent.py
tests/test_evidence_normalizer_agent.py
```

Docker build succeeded for both app and worker images.

Alembic remained at the existing PR39 artifact head:

```text
0019_agent_artifacts (head)
0019_agent_artifacts (head)
```

No migration was introduced by PR42.

Application health after restart:

```http
HTTP/1.1 200 OK
{"status":"ok"}
```

## Registry smoke

Registry smoke was rerun with the corrected access path for `result_kind` via `output_schema["recommended_envelope"]`.

Observed output:

```text
collector implemented: True
collector result_kind: evidence_candidates
normalizer implemented: True
normalizer task_class: data_normalization
normalizer safety_category: read_only_normalization
normalizer result_kind: normalized_evidence
normalizer side_effects: ['write_agent_task_result', 'write_agent_artifact_future']
PR42_REGISTRY_SMOKE_PASS
```

Validated:

- `evidence_collector_future` remains implemented.
- `evidence_normalizer_future` is implemented.
- Normalizer task class is `data_normalization`.
- Normalizer safety category is `read_only_normalization`.
- Normalizer result kind is `normalized_evidence`.
- Declared side effects are exactly:
  - `WRITE_AGENT_TASK_RESULT`
  - `WRITE_AGENT_ARTIFACT_FUTURE`
- No external HTTP or LLM side effects are declared.

A first registry smoke attempt incorrectly accessed `normalizer.result_kind` as a direct attribute and failed with `AttributeError`. This was a smoke-script mistake, not a PR42 runtime issue.

## Rollback smoke

The rollback smoke created temporary source and normalizer tasks inside a transaction, created a rollback-only PR41-style `evidence_candidates` source artifact, ran `evidence_normalizer_future`, checked the produced `normalized_evidence` artifact, verified idempotency, checked the cross-listing ownership guard, and rolled back.

Observed setup:

```text
before_tasks: 2
before_artifacts: 0
listing_a: 1588822130
listing_b: 1632324480
```

Normalizer runner result:

```text
processed: 1
succeeded: 1
failed: 0
task_type: evidence_normalizer_future
status: success
```

Result JSON included:

```json
{
  "ok": true,
  "status": "success",
  "artifact_type": "normalized_evidence",
  "schema_version": "normalized-evidence-candidates-v0",
  "result_kind": "normalized_evidence",
  "normalization_kind": "normalized_evidence_candidates",
  "normalized_count": 1,
  "source_candidate_count": 1
}
```

Output payload included:

```json
{
  "schema_version": "normalized-evidence-candidates-v0",
  "artifact_type": "normalized_evidence",
  "result_kind": "normalized_evidence",
  "metadata": {
    "normalizer_version": "evidence-normalizer-v0",
    "normalization_kind": "normalized_evidence_candidates",
    "listing_external_id": "1588822130",
    "source_artifact_type": "evidence_candidates",
    "source_artifact_schema_version": "evidence-candidates-v0",
    "source_result_kind": "evidence_candidates",
    "source_candidate_count": 1,
    "normalized_count": 1,
    "candidate_count": 1
  }
}
```

The normalized item included deterministic numeric-only normalization:

```json
{
  "normalized_candidate_id": "normalized:4:listing_snapshot:1588822130",
  "source_candidate_id": "listing_snapshot:1588822130",
  "evidence_kind": "listing_snapshot",
  "source": "internal",
  "normalization_status": "normalized",
  "normalized_values": {
    "price_rub": 84900,
    "area_m2": 52,
    "price_per_m2_rub": 1632.69
  }
}
```

Source refs used PR39-safe keys:

```json
{
  "source_kind": "agent_artifact",
  "source_ref_id": "4",
  "note": "source_evidence_candidates_artifact",
  "listing_external_id": "1588822130",
  "agent_task_id": 23
}
```

Validated:

- No source ref key used `artifact_id`, `source_artifact_id`, or `source_task_id`.
- `source_artifact_id` stayed in metadata/result JSON, not source refs.
- Provenance points to the source artifact and source task where available.
- No forbidden sensitive markers were found in serialized output payload.

## Idempotency smoke

The direct handler rerun returned the same artifact id and did not create a duplicate artifact:

```text
repeat handler result: AgentTaskHandlerResult(status='success', ... artifact_id: 5 ...)
```

Validated:

- Re-running the same successful normalizer task returns the existing artifact.
- No duplicate `normalized_evidence` artifact is created.

## Ownership guard smoke

A second normalizer task was created for `listing_b`, while pointing to the source artifact for `listing_a`.

Observed result:

```text
mismatch handler result: AgentTaskHandlerResult(
  status='skipped',
  result_json={
    'ok': True,
    'status': 'skipped',
    'reason': 'source_artifact_listing_mismatch',
    'artifact_type': 'normalized_evidence',
    'normalization_kind': 'normalized_evidence_candidates'
  }
)
```

Validated:

- Cross-listing source artifact normalization is blocked.
- The mismatch path returns `skipped`.
- No artifact is created for the mismatch path.

## Rollback counts

After rollback:

```text
after_tasks: 2
after_artifacts: 0
PR42_NORMALIZER_ROLLBACK_SMOKE_PASS
```

Final persistent counts:

```text
agent_tasks_after: 2
agent_artifacts_after: 0
listing_analyses_after: 730
human_reviews_after: 0
market_evidence_items_after: 0
alerts_sent_after: 4894
```

The `alerts_sent` count may continue to grow due to the live monitor/worker. This is not caused by the PR42 rollback smoke. The PR42-relevant persisted tables remained unchanged for agent tasks/artifacts/listing analyses/human reviews/market evidence.

## Logs

A grep over recent app/worker logs returned one runtime diagnostics line containing the configuration key `scrape_debug_dump_html` with value `False`.

This is a known false positive from the smoke grep pattern. It is not a dumped debug HTML payload, not a secret, and not a traceback.

No suspicious error, critical traceback, raw payload, provider payload, token, cookie, authorization header, or source-artifact mismatch failure was observed.

## Safety boundaries verified

PR42 production smoke verified that the normalizer:

- Reads a PR41 `evidence_candidates` artifact.
- Creates a PR39 `normalized_evidence` artifact only inside rollback smoke.
- Uses deterministic numeric-only normalization.
- Computes `price_per_m2_rub` from price/area when needed.
- Preserves source provenance through PR39-safe source refs.
- Enforces source artifact listing ownership.
- Is idempotent.
- Does not write market evidence.
- Does not mutate scoring, verdicts, workflow state, allowed actions, listing analyses, human reviews, or alerts.
- Does not call external HTTP.
- Does not call LLM.
- Does not write RAG/system memory.

## Known caveats

- PR42 does not auto-enqueue the normalizer after the collector. This is intentional and preserves PR40 root-task-only enqueue behavior.
- `build_plan` can show the normalizer as implemented, while still keeping non-root enqueue disabled during initial enqueue.
- `normalized_evidence` is intentionally used as the existing artifact type. The specific semantic subtype is recorded in `metadata.normalization_kind = normalized_evidence_candidates`.

## Result

PR42 production deployment and smoke verification passed.

```text
PR42 — EvidenceNormalizerAgent v0 ✅ production-smoked
```
