# AgentOrchestratorService v0 (PR40)

PR40 adds a conservative internal `AgentOrchestratorService` foundation for deterministic workflow planning and explicit root-task enqueueing. It reuses the PR37 agent task/workflow registry, PR38 `AgentTask` orchestration metadata, and the PR39 agent artifact read model.

## Boundaries

The service plans first and writes only through explicit enqueue calls. Dry-run planning and run summaries are read-only. PR40 does not add monitor integration, autonomous scheduling, new agents, LLM/RAG/external calls, score or verdict mutation, alert creation, or artifact creation.

## Workflow blueprint layer

PR37 workflows are metadata-only, so PR40 adds a small blueprint layer in `app/agents/workflow_blueprints.py`. Blueprint keys are existing PR37 workflow ids and node task types are existing PR37 task registry entries. This is not a second registry: labels, permissions, side effects, blocking flags, and handler implementation status continue to come from PR37 contracts.

Initial blueprints are:

- `listing_evidence_pipeline`: `evidence_collector_future -> evidence_normalizer_future`
- `listing_decision_support_pipeline`: `data_gap_agent_future -> owner_call_prep_future -> decision_card_wording_future`
- `report_safety_pipeline`: `claim_guard_future`

Future task types remain unimplemented until later PRs add real handlers.

## Feature flags and caps

PR40 reuses the existing settings:

- `AGENT_ORCHESTRATION_ENABLED` (default `false`)
- `AGENT_ORCHESTRATION_ALLOW_MONITOR_TRIGGER` (default `false`; not used for triggering in PR40)
- `AGENT_ORCHESTRATION_MAX_CHAIN_DEPTH` (default `4`)
- `AGENT_ORCHESTRATION_MAX_TASKS_PER_LISTING` (default `10`)
- `AGENT_ORCHESTRATION_DEFAULT_TIMEOUT_SEC` (default `120`)

When orchestration is disabled, dry-run still works but enqueue returns a disabled result and performs no writes.

## Enqueue behavior and idempotency

PR40 enqueues root ready tasks only when all safety gates pass. Dependent future nodes are not pre-created. New root tasks get `workflow_id`, `orchestration_run_id`, `chain_depth=0`, `dependency_status=ready`, and `orchestration_status=queued`.

The root dedupe key excludes `orchestration_run_id` and includes a stable hash of a safe input envelope. Repeated enqueue requests reuse existing tasks instead of creating unbounded duplicates. A new `orchestration_run_id` is generated only when a new task is created.

## No persistent run row

There is no `orchestration_runs` table in PR40. Run summaries are derived from existing `agent_tasks` and `agent_artifacts` filtered by `orchestration_run_id`.

## Runner safety guard

`AgentTaskRepository.list_pending()` excludes pending tasks whose `dependency_status` is `waiting` or `blocked`. Non-orchestrated tasks and tasks with `NULL`, `not_applicable`, or `ready` dependency status remain eligible. The runner does not create dependent tasks, artifacts, or mirror task status into `orchestration_status`.

## Artifact read-only policy

The orchestrator summarizes artifacts by `orchestration_run_id` using PR39 `list_agent_artifacts()` and `serialize_agent_artifact()` helpers. It exposes only safe artifact identifiers, types, redaction status, source task, run id, and creation time. It does not read or return raw payloads.

## Production smoke plan

After deploy, verify config, build plans for the three PR40 workflow ids, and compare before/after counts for `agent_tasks`, `agent_artifacts`, `alerts_sent`, `listing_analyses`, `human_reviews`, and `market_evidence_items`. Disabled enqueue should return blocked with no DB writes. Confirm Alembic head remains PR39 `agent_artifacts` because PR40 has no migration.

## PR41+ ownership

PR41+ will add actual artifact-producing agents and any later controlled advancement logic. PR40 only supplies the safe planner, validation, root enqueue gate, and read-only summaries.
