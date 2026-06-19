# PR41 - EvidenceCollectorAgent v0 production smoke handoff

Date: 2026-06-19

## Summary

PR41 delivered the first controlled artifact-producing agent in the avito-watcher pipeline: `EvidenceCollectorAgent v0` for task type `evidence_collector_future`.

This handoff covers:

- PR #247 - `Add EvidenceCollectorAgent v0`
- PR #248 - `Align evidence collector artifact result_kind` hotfix
- Production deploy and smoke evidence for the PR41 functionality after the hotfix

Final status: PASS for the PR41 artifact-critical smoke path.

## Production commits

Production branch was updated to:

```text
b7641cb Align evidence collector artifact result_kind (#248)
6051712 Add EvidenceCollectorAgent v0 (#247)
a2cec88 Add PR40 production smoke handoff (#246)
c68af4c Add AgentOrchestratorService v0 (#245)
2913e16 Add PR39 production smoke handoff (#244)
876ad03 Add agent artifact blackboard storage (#243)
```

## Build and migration

Production build completed for both images:

```text
Image deploy-app    Built
Image deploy-worker Built
```

Alembic remained unchanged, as expected for PR41:

```text
0019_agent_artifacts (head)
0019_agent_artifacts (head)
```

No database migration was introduced by PR41 or the PR41 result-kind hotfix.

## Runtime deployment

`app` and `worker` were restarted from the updated images.

An immediate health curl after restart returned a transient connection reset while the app container had just restarted. Subsequent in-container smoke execution against the app runtime succeeded. No runtime error, traceback, or exception was found in the final narrowed log grep.

## Smoke checks performed

### 1. PR41 plan smoke

Before the hotfix, the orchestrator plan correctly showed:

```text
workflow_id: listing_evidence_pipeline
valid: True
reason: ok
planning_supported: True
enqueue_supported: True
nodes:
  evidence_collector -> evidence_collector_future, implemented=True, can_enqueue=True
  evidence_normalizer -> evidence_normalizer_future, implemented=False, can_enqueue=False, blocked_reason=handler_unimplemented
edges:
  evidence_collector -> evidence_normalizer
```

This confirms PR41 implements only the root EvidenceCollector task and does not implement or auto-create EvidenceNormalizer tasks.

### 2. Disabled enqueue smoke

With orchestration disabled by settings, enqueue returned:

```text
ok=False
blocked_reason=orchestration_disabled
enqueued_task_ids=()
existing_task_ids=()
orchestration_run_id=None
```

This confirms PR41 does not accidentally enable orchestration at runtime.

### 3. Enabled root enqueue rollback smoke

With orchestration enabled in-memory for a rollback-only smoke, enqueue created exactly one root task:

```text
task_type=evidence_collector_future
workflow_id=listing_evidence_pipeline
chain_depth=0
dependency_status=ready
orchestration_status=queued
```

No `evidence_normalizer_future` downstream task was created.

The transaction was rolled back.

### 4. Registry smoke

Registry contract after PR41:

```text
task_type=evidence_collector_future
implemented=True
handler_name=evidence_collector_future
declared_side_effects=(write_agent_task_result, write_agent_artifact_future)
limitations=(
  internal_evidence_candidates_only,
  no_external_http,
  no_llm,
  no_rag_write,
  not_normalized_evidence,
  not_market_evidence_item
)
```

The registry smoke verified:

```text
WRITE_AGENT_TASK_RESULT present
WRITE_AGENT_ARTIFACT_FUTURE present
EXTERNAL_HTTP_CALL absent
EXTERNAL_LLM_CALL absent
```

### 5. Artifact creation rollback smoke after PR #248

After the PR #248 hotfix, the EvidenceCollector handler successfully processed a rollback-only task:

```text
processed=1
succeeded=1
skipped=0
failed=0
task_type=evidence_collector_future
status=success
artifact_type=evidence_candidates
schema_version=evidence-candidates-v0
candidate_count=1
source_refs_count=1
```

The created artifact payload included:

```json
{
  "schema_version": "evidence-candidates-v0",
  "artifact_type": "evidence_candidates",
  "result_kind": "evidence_candidates",
  "summary": "Collected internal evidence candidates for listing.",
  "items": [
    {
      "candidate_id": "listing_snapshot:8097895214",
      "evidence_kind": "listing_snapshot",
      "source": "internal",
      "observed_value": {
        "price": 84900.0,
        "area_m2": 52.0,
        "price_per_m2": 1632.69
      },
      "source_refs": [
        {
          "agent_task_id": 22,
          "listing_external_id": "8097895214"
        }
      ]
    }
  ],
  "limitations": [
    "internal_evidence_candidates_only",
    "not_normalized_evidence",
    "not_market_evidence_item",
    "no_external_http",
    "no_llm",
    "no_rag_write"
  ],
  "confidence": 0.5,
  "notes": [],
  "metadata": {
    "collector_version": "evidence-collector-v0",
    "listing_external_id": "8097895214",
    "candidate_count": 1,
    "missing_data": []
  }
}
```

The key PR41 production-smoke acceptance condition is now met:

```text
payload.result_kind == evidence_candidates
registry.output_schema.recommended_envelope.result_kind == payload.result_kind
```

### 6. Idempotency smoke

Calling the handler again on the same successful task returned the same artifact id and did not create a duplicate artifact:

```text
repeat handler result: success
artifact_id unchanged
artifact count unchanged before rollback
```

### 7. Rollback / no persisted side effects

After rollback, final production counts were:

```text
agent_tasks_after             2
agent_artifacts_after         0
alerts_sent_after             4780
listing_analyses_after        730
human_reviews_after           0
market_evidence_items_after   0
```

`agent_tasks` and `agent_artifacts` remained unchanged after rollback.

`alerts_sent` increased from the earlier value during worker runtime, but this is unrelated to the PR41 rollback-only artifact smoke. PR41 did not create alerts, did not write `AlertSent`, and did not mutate scoring or reviews.

### 8. Log safety grep

Final narrowed log grep returned no output for:

```text
ERROR
CRITICAL
Traceback
Exception
Authorization:
Cookie:
X-API-Key:
Bearer <token>
OPENAI_API_KEY
DATABASE_URL
POSTGRES_PASSWORD
WEBHOOK_URL
SMTP_PASSWORD
TELEGRAM_BOT_TOKEN
raw_payload_json
provider_payload
```

No sensitive marker or runtime exception was found in the final grep.

## Scope boundaries confirmed

PR41 and PR #248 did not change:

```text
score
verdict
workflow_state
allowed_actions
filters
scoring formulas
monitor trigger
alerts_sent writes
human_reviews writes
market_evidence_items writes
listing_analyses writes
orchestrator blueprint semantics
EvidenceNormalizerAgent implementation
external HTTP calls
LLM calls
RAG writes
browser/parser calls
```

## Known notes

1. PR41 is an internal-only evidence candidate collector. It produces candidate artifacts, not normalized evidence.
2. `evidence_normalizer_future` remains unimplemented.
3. `listing_evidence_pipeline` is still not fully implemented end-to-end until the normalizer step is added in a later PR.
4. The PR #248 hotfix intentionally made the artifact validator accept both legacy `result_kind="artifact_payload"` and artifact-specific `result_kind=artifact_type` to preserve backward compatibility while allowing PR41 typed artifacts.
5. The immediate post-restart `/health` curl after PR #248 returned a transient connection reset; subsequent app-runtime execution and log grep were clean. If a strict health sample is required for the deployment record, run one final `curl -i http://127.0.0.1:8010/health` and append the 200 response to this handoff.

## Final decision

PR41 + PR #248 production smoke is accepted as PASS for the agent/artifact contract:

```text
EvidenceCollectorAgent handler registered: yes
evidence_collector_future implemented: yes
external HTTP / LLM side effects: absent
root-only orchestration behavior: verified
artifact creation: verified in rollback transaction
artifact result_kind: evidence_candidates
artifact duplicate prevention: verified
persistent task/artifact side effects after rollback: none
logs: clean
```
