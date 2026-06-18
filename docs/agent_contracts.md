# Agent task contracts and governance registry (PR37)

PR37 adds a passive governance layer for the existing one-shot `AgentTask` runtime. It catalogs task types, task classes, safety categories, declared side-effect metadata, capability metadata, output schema metadata, policy metadata, and future workflow skeletons before later orchestration PRs add dependency graphs or runtime orchestration.

Codex checkout note: this environment did not expose local `main` or `origin/main`, so the prerequisite was verified by fallback inspection of the current branch. The current checkout includes the expected PR29-PR36 foundation: Admin API v1, meta/capability/enums/errors contract, listing/review queue read APIs, derived workflow state/allowed actions, Decision Card, Risk Attention, Readiness Checklist, Price Position, and the existing `AgentTaskRunner` plus current handlers. The final reviewer must still verify base branch, PR36 merge status, and CI against `main` in GitHub.

## Boundary

The deterministic application remains the source of truth for score, verdict, risk primitives, workflow state, allowed actions, filters, alerts, reports, and human-approved actions. Agents may collect, normalize, explain, draft, or propose. Deterministic services validate and arbitrate, and humans approve actions.

PR37 does **not** implement:

- `AgentOrchestratorService`;
- dependency graph fields;
- `agent_artifacts`;
- new agents;
- automatic task creation;
- monitor-cycle integration;
- score, verdict, workflow, filter, or action mutation;
- external calls or LLM calls beyond behavior already present in existing handlers;
- a new permission system;
- a parallel redaction system;
- validation, rewrite, wrapping, migration, or retroactive invalidation of historical `agent_tasks.result_json` rows.

## Canonical modules

- Task and workflow contract types: `app/agents/contracts.py`.
- Passive task/workflow registries: `app/agents/registry.py`.
- Canonical redaction helper: `app/api/admin_v1/redaction.py`, function `redact_api_response`.
- Current handler discovery: `app/services/agent_task_runner.py`, function `get_registered_agent_task_handler_names`.

No `app/agents/redaction.py` module is introduced. Agent output redaction reuses and extends the existing Admin API helper.

## Task class enum

PR37 uses only these task classes:

- `data_collection`
- `data_normalization`
- `data_gap_analysis`
- `decision_wording`
- `call_preparation`
- `geocoding`
- `report_composition`
- `offer_composition`
- `claim_guard`
- `portfolio_memory`

Legacy handlers are mapped to the closest class. These mappings do not change runtime behavior.

## Safety categories

PR37 uses only non-autonomous categories:

- `read_only_explanation`
- `read_only_research`
- `read_only_extraction`
- `read_only_normalization`
- `draft_generation`
- `safety_review`
- `governance_proposal`

No category authorizes score mutation, workflow mutation, action execution, alert sending, report export execution, or monitor-cycle blocking.

## Declared side effects

The field is named `declared_side_effects` intentionally. It is descriptive metadata only, not an authorization grant and not a runtime enabler. Runtime behavior remains governed by the existing handler code, feature flags, provider configuration, and explicit endpoints.

Allowed metadata values are:

- `none`
- `write_agent_task_result`
- `write_agent_artifact_future`
- `external_llm_call`
- `external_http_call`
- `rag_read`
- `rag_write_future`
- `admin_display_only`

`write_agent_artifact_future` and `rag_write_future` are future-only metadata and do not execute in PR37.

## Capability metadata

Contracts use `required_capabilities: list[str]` as metadata only. PR37 does not create `AgentPermission`, roles, write endpoints, or a second permission evaluator. The existing Admin API meta/access layer remains the source of truth for permissions, roles, capabilities, labels, and errors.

Suggested strings used by contracts include:

- `agent_task.read`
- `agent_task.run_manual`
- `agent_task.run_research`
- `agent_task.run_extraction`
- `agent_task.run_governance`
- `agent_task.view_redacted_result`

These strings do not grant access by themselves.

## Timeout, retry, dedupe, and redaction policies

Policy objects are metadata only in PR37:

- timeout policy records a default timeout value;
- retry policy records retry intent without adding retry loops;
- dedupe policy documents use of the existing `agent_tasks.dedupe_key` model;
- redaction policy points to the canonical Admin API helper.

Feature flags are safe defaults and not wired into orchestration runtime:

- `AGENT_ORCHESTRATION_ENABLED=false`
- `AGENT_ORCHESTRATION_ALLOW_MONITOR_TRIGGER=false`
- `AGENT_ORCHESTRATION_MAX_CHAIN_DEPTH=4`
- `AGENT_ORCHESTRATION_MAX_TASKS_PER_LISTING=10`
- `AGENT_ORCHESTRATION_DEFAULT_TIMEOUT_SEC=120`

Future PR40 owns runtime enforcement.

## Output schema metadata and legacy compatibility

Every registered task type has schema-like `output_schema` metadata. The generic safe result envelope documents:

- `schema_version`
- `agent_name`
- `agent_contract_version`
- `task_type`
- `input_hash`
- `source_refs`
- `limitations`
- `confidence`
- `result_kind`
- `recommendations_or_proposals_only`

This metadata describes current/future expected shapes only. PR37 does not rewrite, wrap, migrate, validate-on-read, or invalidate old `result_json` rows. Existing handlers may continue returning legacy shapes, and legacy-compatible contracts are marked accordingly.

Allowed future `source_refs` keys are listed in `app/agents/contracts.py`. Forbidden source refs include raw provider payloads, raw HTML, raw `facts_json`, raw `result_json`, raw payloads, webhook URLs, request headers, cookies, authorization values, API keys, tokens, secrets, debug URLs, and absolute execution URLs.

Future hashing policy: stable JSON serialization should omit generated timestamps, auth values, request URLs, and secrets. PR37 does not add unused hashing infrastructure.

## Current implemented task type mapping

`implemented=true` is based only on actual handlers discoverable from the current codebase through `get_registered_agent_task_handler_names()`. PR37 does not create fake no-op handlers.

Current mappings when handlers are present:

| task type | task class | safety category | notes |
| --- | --- | --- | --- |
| `review_copilot` | `decision_wording` | `read_only_explanation` | legacy-compatible review explanation task |
| `listing_detail_extraction` | `data_collection` | `read_only_extraction` | legacy-compatible extraction task |
| `market_research` | `data_collection` | `read_only_research` | legacy-compatible research candidate task |
| `weekly_strategy_agent` | `portfolio_memory` | `governance_proposal` | legacy-compatible strategy summary task if handler exists |
| `data_quality_agent` | `data_gap_analysis` | `safety_review` | legacy-compatible data-quality review, not PR43 DataGapAgent |

Future roadmap placeholders are registered with `implemented=false` and `handler_required=false` when no handler exists.

## Legacy `data_quality_agent` mapping

`data_quality_agent` is a legacy data-quality review task. It investigates suspicious data quality, parser mismatch, and bad listing data.

It is **not** the future PR43 DataGapAgent. It must not produce `data_gap_report` artifacts in PR37, and it must not be treated as the owner of PR43 data-gap workflows. Its contract includes:

- `legacy_compatibility=true`
- `legacy_semantic_label="data_quality_review"`
- limitation `legacy_data_quality_review_not_pr43_data_gap_agent`
- limitation `must_not_produce_data_gap_report_artifact`

Future DataGapAgent work belongs to PR43.

## Workflow registry skeleton

PR37 registers metadata-only future workflows:

- `listing_evidence_pipeline`
- `listing_decision_support_pipeline`
- `report_safety_pipeline`

All are `implemented=false`. They cannot run, create tasks, create orchestration runs, write artifacts, or add dependency graph fields. PR38 owns dependency graph fields, PR39 owns artifacts, and PR40 owns `AgentOrchestratorService`.

## Unknown and missing handler behavior

The runner remains the existing one-shot runner. PR37 adds only a minimal diagnostic distinction for missing handlers:

- unknown task type: skipped with `unknown_agent_task_type` in `result_json`;
- registered task type without handler: skipped with `agent_handler_not_registered` in `result_json`.

Known handler success, skipped, failed, exception, and dry-run behavior remains compatible. Handler exceptions remain failed and are not converted to success.

## Redaction policy

The canonical helper is `app.api.admin_v1.redaction.redact_api_response`. It recursively redacts secret-like keys and obvious credential-like strings, including auth headers, cookies, webhook fields, SMTP credentials, Telegram bot tokens, OpenAI API keys, and database URLs. It must not over-redact safe limitation codes such as:

- `not_investment_advice`
- `readiness_is_not_action_authorization`
- `recommendation_scope_internal_workflow`
- `not_certified_appraisal`
- `not_valuation_report`

## Future PR boundaries

- PR38 owns AgentTask orchestration metadata fields and DB migration.
- PR39 owns `agent_artifacts` / blackboard layer.
- PR40 owns `AgentOrchestratorService`.
- PR41+ own new agent implementations.
- PR43 owns the future DataGapAgent and `data_gap_report` artifacts.
- PR46 owns actual claim checking over reports, offers, and presentations.
- Report/export PRs own export execution and export permissions.
