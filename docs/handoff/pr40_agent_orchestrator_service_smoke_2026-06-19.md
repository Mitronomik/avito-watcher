# PR40 production smoke handoff - AgentOrchestratorService v0

## PR

PR40 - AgentOrchestratorService v0

Merged PR:

- #245 - Add AgentOrchestratorService v0

Production commit:

- c68af4c Add AgentOrchestratorService v0 (#245)

Deploy date:

- 2026-06-19

## Deploy summary

Production was updated from:

- 876ad03 Add agent artifact blackboard storage (#243)
- 2913e16 Add PR39 production smoke handoff (#244)

to:

- c68af4c Add AgentOrchestratorService v0 (#245)

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

Status:

- PASS

## Migration status

PR40 does not add a database migration.

Alembic current/head after deploy:

```text
0019_agent_artifacts (head)
0019_agent_artifacts (head)
```

`alembic upgrade head` completed as a no-op.

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

## Admin meta smoke

Checked Admin API v1 meta contract with the production read key.

Confirmed:

- `orchestration_planning_supported` is present and `true`
- `orchestration_enqueue_enabled` is present and `false`
- `orchestration_monitor_trigger_enabled` is present and `false`
- existing PR39 `agent_artifacts_read` capability remains present and `true`
- no execution/action/auth/raw-payload metadata markers were present in the meta response

Observed smoke result:

```text
PR40_META_SMOKE_PASS
```

Status:

- PASS

## AgentOrchestratorService plan smoke

Checked deterministic planning for all PR40 blueprint workflow ids:

- `listing_evidence_pipeline`
- `listing_decision_support_pipeline`
- `report_safety_pipeline`

Observed:

```text
listing_evidence_pipeline {'valid': True, 'reason': 'ok', 'planning_supported': True, 'enqueue_supported': False, 'nodes': [('evidence_collector', 'evidence_collector_future', False, False, 'handler_unimplemented'), ('evidence_normalizer', 'evidence_normalizer_future', False, False, 'handler_unimplemented')], 'edges': [('evidence_collector', 'evidence_normalizer')]}
listing_decision_support_pipeline {'valid': True, 'reason': 'ok', 'planning_supported': True, 'enqueue_supported': False, 'nodes': [('data_gap', 'data_gap_agent_future', False, False, 'handler_unimplemented'), ('owner_call_prep', 'owner_call_prep_future', False, False, 'handler_unimplemented'), ('decision_card_wording', 'decision_card_wording_future', False, False, 'handler_unimplemented')], 'edges': [('data_gap', 'owner_call_prep'), ('owner_call_prep', 'decision_card_wording')]}
report_safety_pipeline {'valid': True, 'reason': 'ok', 'planning_supported': True, 'enqueue_supported': False, 'nodes': [('claim_guard', 'claim_guard_future', False, False, 'handler_unimplemented')], 'edges': []}
```

Observed smoke result:

```text
PR40_PLAN_SMOKE_PASS
```

Confirmed:

- all three workflow plans are valid
- all three are planning-supported
- enqueue is not supported because all v0 nodes are future-only/unimplemented
- no fake implemented handlers were introduced
- dependent nodes were not enqueued or pre-created

Status:

- PASS

## Disabled enqueue smoke

Before disabled enqueue smoke:

```text
agent_tasks_before: 2
agent_artifacts_before: 0
alerts_sent_before: 4610
listing_analyses_before: 730
human_reviews_before: 0
market_evidence_items_before: 0
```

Attempted explicit enqueue for:

```text
workflow_id=listing_evidence_pipeline
listing_external_id=smoke-pr40-listing
context_key=smoke-pr40
dry_run=False
```

Observed result:

```text
AgentOrchestrationEnqueueResult(
  ok=False,
  dry_run=False,
  workflow_id='listing_evidence_pipeline',
  orchestration_run_id=None,
  enqueued_task_ids=(),
  existing_task_ids=(),
  blocked_reason='orchestration_disabled'
)
```

Observed smoke result:

```text
PR40_DISABLED_ENQUEUE_SMOKE_PASS
```

After disabled enqueue smoke:

```text
agent_tasks_after: 2
agent_artifacts_after: 0
alerts_sent_after: 4610
listing_analyses_after: 730
human_reviews_after: 0
market_evidence_items_after: 0
```

Confirmed:

- disabled enqueue produced no DB writes
- no `AgentTask` rows were created
- no `AgentArtifact` rows were created
- no alerts were sent
- no listing analyses, human reviews, or market evidence were created

Status:

- PASS

## Future-only enabled enqueue smoke

Temporarily enabled orchestration only inside the Python process:

```text
settings.agent_orchestration_enabled = True
```

Then attempted enqueue for future-only workflow:

```text
workflow_id=listing_evidence_pipeline
listing_external_id=smoke-pr40-enabled-future-only
context_key=smoke-pr40
dry_run=False
```

Observed result:

```text
AgentOrchestrationEnqueueResult(
  ok=True,
  dry_run=False,
  workflow_id='listing_evidence_pipeline',
  orchestration_run_id=None,
  enqueued_task_ids=(),
  existing_task_ids=(),
  blocked_reason='no_implemented_root_nodes'
)
```

Observed smoke result:

```text
PR40_ENABLED_FUTURE_ONLY_ZERO_TASKS_SMOKE_PASS
```

Confirmed:

- future-only workflows may enqueue zero tasks
- no fake handlers were introduced
- no persistent run row was created
- no `orchestration_run_id` was generated for zero-task enqueue
- no root task was inserted
- transaction was rolled back

Status:

- PASS

## Pending dependency guard smoke

Checked `AgentTaskRepository.list_pending(limit=50)`.

Observed:

```text
{'pending_returned': 0, 'waiting_or_blocked_returned': []}
```

Observed smoke result:

```text
PR40_PENDING_GUARD_SMOKE_PASS
```

Confirmed:

- no pending `waiting` task was returned
- no pending `blocked` task was returned
- repository-level guard is active

Status:

- PASS

## Runner dry-run compatibility

Ran agent task runner dry-run:

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

Confirmed:

- runner still works after PR40
- runner dry-run found no pending tasks
- runner did not create artifacts
- runner did not auto-create dependent tasks

Status:

- PASS

## Final no-side-effect check

Observed final counts:

```text
listings_total: 2395
listing_analyses_total: 730
human_reviews_total: 0
market_evidence_items_total: 0
alerts_sent_total: 4610
agent_tasks_total: 2
agent_artifacts_total: 0
```

Confirmed PR40 side-effect boundaries:

- `agent_tasks_total` stayed `2`
- `agent_artifacts_total` stayed `0`
- `alerts_sent_total` stayed `4610`
- `listing_analyses_total` stayed `730`
- `human_reviews_total` stayed `0`
- `market_evidence_items_total` stayed `0`

Note:

- `listings_total` was observed only in the final no-side-effect check and was not captured in the before-block. This handoff therefore does not claim a before/after listings comparison for PR40. The orchestrator smoke itself did not create tasks, artifacts, alerts, reviews, market evidence, or analyses.

Status:

- PASS for PR40 orchestrator/read-model side effects

## Logs and sensitive marker grep

Checked recent app and worker logs for errors, stack traces, and common sensitive header/secret markers.

Observed:

```text
no matches
```

Status:

- PASS

## Confirmed PR40 boundaries

Confirmed:

- no database migration
- no `agent_orchestration_runs` table
- no persistent orchestration run row
- no monitor trigger
- no new agent handlers
- no artifact writes
- no dependent task auto-creation
- no workflow advancement loop
- no score mutation
- no verdict mutation
- no filters mutation
- no workflow state mutation
- no allowed actions mutation
- no `AlertSent` creation by PR40 smoke
- no LLM calls
- no external HTTP calls
- no RAG writes
- no report generation
- no offer generation
- no presentation generation
- future-only workflows enqueue zero tasks
- Admin meta exposes orchestration planning/enqueue/monitor-trigger capability flags without execution/auth/raw payload metadata

## Operational notes

Server login banner reported maintenance items:

```text
system restart required
18 zombie processes
root filesystem usage 76.1%
19 updates available
10 standard security updates available
ESM Apps not enabled
```

These are operational maintenance items and not PR40 regressions. Do not address them in this docs-only PR.

## Next roadmap PR

Next after this handoff is merged:

- PR41 - first artifact-producing agent implementation, likely EvidenceCollectorAgent / evidence collection task handler, unless roadmap sequencing is adjusted.

PR41 must still preserve:

- deterministic core ownership of score/verdict
- no hidden score/verdict/filter/workflow/action mutation
- no monitor blocking by agents or orchestration
- explicit feature flags and safe defaults
- artifacts as trace/read-model outputs, not final decision authority
- no direct AlertSent writes from agents
