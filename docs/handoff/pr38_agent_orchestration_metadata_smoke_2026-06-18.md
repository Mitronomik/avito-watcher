# PR38 production smoke handoff - Agent orchestration metadata

## PR

PR38 - Agent orchestration metadata and dependency graph

Merged PR:

- #241 - Add agent task orchestration metadata

Production commit:

- 1ba4908 Add agent task orchestration metadata (#241)

Deploy date:

- 2026-06-18

## Deploy summary

Production was updated from:

- c96eef5 Add agent task/workflow contracts, registry, redaction improvements, and runner diagnostics (#239)
- 86db5f9 Add PR37 production smoke handoff (#240)

to:

- 1ba4908 Add agent task orchestration metadata (#241)

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
0017_admin_audit_events -> 0018_agent_task_orch_meta
```

Alembic current/head after deploy:

```text
0018_agent_task_orch_meta (head)
0018_agent_task_orch_meta (head)
```

Status:

- PASS

## Health smoke

Command:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```json
{"status":"ok"}
```

Status:

- PASS

## Database schema smoke

### Columns

PR38 added these nullable columns to `agent_tasks`:

```text
orchestration_run_id
workflow_id
parent_task_id
depends_on_task_id
chain_depth
blocking
dependency_status
orchestration_status
```

Observed:

```text
8 columns present
all is_nullable = YES
all column_default = NULL / empty
```

Status:

- PASS

### Constraints

Observed constraints:

```text
ck_agent_tasks_chain_depth_non_negative
ck_agent_tasks_dependency_not_self
ck_agent_tasks_dependency_status
ck_agent_tasks_orchestration_status
ck_agent_tasks_parent_not_self
fk_agent_tasks_depends_on_task_id
fk_agent_tasks_parent_task_id
```

Constraint definitions confirmed:

- `chain_depth IS NULL OR chain_depth >= 0`
- `depends_on_task_id IS NULL OR depends_on_task_id <> id`
- `parent_task_id IS NULL OR parent_task_id <> id`
- dependency status is nullable or one of the closed PR38 values
- orchestration status is nullable or one of the closed PR38 values
- self-referential FK for `parent_task_id`
- self-referential FK for `depends_on_task_id`

Status:

- PASS

### Indexes

Observed indexes:

```text
ix_agent_tasks_dependency_status
ix_agent_tasks_depends_on_task_id
ix_agent_tasks_orchestration_run_id
ix_agent_tasks_orchestration_status
ix_agent_tasks_parent_task_id
ix_agent_tasks_workflow_id
```

Status:

- PASS

## Strict NULL convention smoke

Observed production `agent_tasks` orchestration metadata:

```text
agent_tasks_total: 2
orchestration_run_id_not_null: 0
workflow_id_not_null: 0
parent_task_id_not_null: 0
depends_on_task_id_not_null: 0
chain_depth_not_null: 0
blocking_not_null: 0
dependency_status_not_null: 0
orchestration_status_not_null: 0
```

Status:

- PASS

This confirms PR38 did not backfill historical tasks and did not persist fake orchestration metadata for non-orchestrated tasks.

## Helper smoke

Checked helper-level effective values on a task-like object with null metadata.

Observed:

```text
effective_chain_depth_null_task 0
effective_blocking_null_task False
effective_dependency_status_null_task not_applicable
effective_orchestration_status_null_task not_applicable
valid_known_empty AgentTaskOrchestrationMetadataValidation(valid=True, reason='ok')
invalid_unknown_workflow AgentTaskOrchestrationMetadataValidation(valid=False, reason='unknown_workflow_id')
```

Status:

- PASS

This confirms effective values are helper-level read-only interpretation and unknown workflow IDs return safe validation metadata rather than throwing.

## Admin meta enum smoke

Checked `/api/admin/v1/meta`.

Observed:

```text
enums_type dict
has_dependency_status True
has_orchestration_status True
META_ENUM_SMOKE_PASS
```

Confirmed enum ids:

```text
agent_task_dependency_status
agent_task_orchestration_status
```

Forbidden execution/auth/raw metadata markers were absent.

Status:

- PASS

## Runner dry-run smoke

Command:

```bash
python3 -m app.cli run-agent-tasks --limit 10 --dry-run
```

Observed:

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

Status:

- PASS

This confirms PR38 did not introduce dependency enforcement, automatic dependent task creation, or orchestration runtime behavior.

## No-side-effect check

Observed counts after smoke:

```text
listings_total: 2375
listing_analyses_total: 730
human_reviews_total: 0
market_evidence_items_total: 0
alerts_sent_total: 4570
agent_tasks_total: 2

agent_tasks by status:
success: 2
```

Interpretation:

- `agent_tasks_total` stayed `2`
- all PR38 orchestration metadata on existing tasks stayed `NULL`
- runner dry-run had `pending=0`
- no PR38 orchestration side effects were observed

`listings_total` and `alerts_sent_total` changed while the production worker was running. This is treated as background monitor activity, not a PR38 orchestration effect.

Status:

- PASS for PR38 orchestration/no-agent-task side effects

## Logs and secret grep

Recent app and worker logs were checked for error, traceback, auth header, token, and known secret-name leakage markers.

Observed:

```text
no matches
```

Status:

- PASS

## Confirmed PR38 boundaries

Confirmed:

- no AgentOrchestratorService
- no runtime orchestration
- no automatic dependent task creation
- no dependency enforcement in AgentTaskRunner
- no agent_artifacts table
- no blackboard layer
- no new agents
- no monitor integration
- no score mutation
- no verdict mutation
- no filters_json mutation
- no workflow_state mutation
- no allowed_actions mutation
- no AlertSent creation by PR38 smoke
- no new agent-task-specific permission/capability namespace
- no execution endpoint metadata
- no HTTP method metadata
- no absolute URL metadata
- no auth param metadata
- no raw result_json metadata

## Operational notes

The server had previously reported:

- system restart required
- zombie processes present
- root filesystem usage around 75%

These are operational maintenance items and not PR38 regressions. Do not address them in this docs-only PR.

## Next roadmap PR

Next:

- PR39 - Agent artifacts / blackboard read model

PR39 should introduce persisted agent artifacts / blackboard storage for agent outputs and evidence handoff.

PR39 must still avoid:

- score/verdict mutation
- automatic workflow/action mutation
- monitor blocking
- hidden orchestration side effects
- report/offer/presentation generation
- runtime AgentOrchestratorService behavior, unless explicitly scoped later

Runtime orchestration remains owned by PR40.
