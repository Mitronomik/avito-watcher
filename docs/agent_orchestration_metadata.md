# Agent orchestration metadata (PR38)

PR38 extends `AgentTask` with nullable metadata for future orchestration and dependency graph reasoning. It is a backward-compatible schema foundation only: existing one-shot `AgentTaskRunner` behavior remains unchanged.

## New AgentTask fields

All fields are nullable at the database level so existing rows remain valid and no historical rows are backfilled with fake orchestration data. Plain non-orchestrated tasks keep these fields `NULL` unless a caller explicitly supplies orchestration metadata.

- `orchestration_run_id` — optional future orchestration run correlation key.
- `workflow_id` — optional workflow identifier from the PR37 workflow registry.
- `parent_task_id` — optional self-reference to an upstream parent task.
- `depends_on_task_id` — optional self-reference to a task whose completion may matter to future orchestration.
- `chain_depth` — optional non-negative depth marker. Non-orchestrated rows keep `NULL`; helper-level effective reads may treat it as `0`.
- `blocking` — optional future blocking marker. Non-orchestrated rows keep `NULL`; helper-level effective reads may treat it as `False`.
- `dependency_status` — optional dependency status marker. Non-orchestrated rows keep `NULL`; helper-level effective reads may treat it as `not_applicable`.
- `orchestration_status` — optional orchestration status marker. Non-orchestrated rows keep `NULL`; helper-level effective reads may treat it as `not_applicable`.

## Dependency statuses

- `not_applicable` — task is not part of orchestration or dependency graph.
- `waiting` — task is waiting for dependency completion.
- `ready` — dependency is satisfied, but PR38 does not auto-run it.
- `blocked` — dependency or upstream task prevents safe downstream processing.

## Orchestration statuses

- `not_applicable` — task is not part of orchestration.
- `queued` — task is part of a future orchestration run but not running yet.
- `running` — future orchestration status marker only.
- `completed` — future orchestration status marker only.
- `failed` — future orchestration status marker only.
- `skipped` — future orchestration status marker only.
- `blocked` — future orchestration status marker only.

## Validation helper

`validate_agent_task_orchestration_metadata(...)` provides deterministic checks for status enums, non-negative chain depth, max chain depth, known workflow IDs, and direct self-references. Effective value helpers can interpret `NULL` as `0`, `False`, or `not_applicable` for read-only reasoning. These helpers never write effective values back to the database. It returns safe validation metadata instead of raising during normal use.

Example:

```python
validation = validate_agent_task_orchestration_metadata(
    workflow_id="listing_evidence_pipeline",
    chain_depth=1,
    dependency_status="ready",
    orchestration_status="queued",
)
assert validation.valid
```

Unknown workflow IDs return `valid=False` with reason `unknown_workflow_id`. The runner does not call this helper to gate execution in PR38.

## Metadata-only boundary

PR38 does not enforce dependencies, inspect `depends_on_task_id`, create child tasks, mirror `task.status` into `orchestration_status`, or transition orchestration statuses. Pending one-shot tasks continue to run exactly as before.

## Non-goals

- No `AgentOrchestratorService`.
- No automatic dependent task creation.
- No `agent_artifacts` or blackboard layer.
- No new agents.
- No score, verdict, filter, workflow action, or alert mutation.
- No monitor integration.
- No LLM calls, HTTP calls, report generation, or financial scenario engine.

## Next PR ownership

- PR39 owns the artifact/blackboard layer.
- PR40 owns runtime orchestration.
