# Weekly StrategyAgent (PR17)

PR17 adds a manual, opt-in `weekly_strategy_agent` AgentTask. It produces an advisory weekly strategy report from deterministic SQL statistics and bounded existing system-memory context when available.

## Purpose

The report helps a human review noisy searches, filter tuning candidates, recurring parser/data-quality issues, weak/review-heavy analysis profiles, market-evidence coverage, useful follow-up agents, and the next human-approved roadmap action.

The model is:

1. Clean data first.
2. Deterministic gates second.
3. Deterministic scoring third.
4. Human-readable LLM explanation fourth.
5. RAG memory fifth.
6. External research sixth.
7. Investment scoring with comps seventh.
8. Agent strategy loop last.

The StrategyAgent proposes; a human approves.

## Manual and default-off behavior

The task is not scheduled and is not created by the monitor cycle. Operators must create an `agent_tasks` row manually with `task_type='weekly_strategy_agent'` and run the existing manual AgentTask runner.

Default settings are safe:

```env
WEEKLY_STRATEGY_AGENT_ENABLED=false
WEEKLY_STRATEGY_AGENT_PROVIDER=off
WEEKLY_STRATEGY_AGENT_MODEL=
WEEKLY_STRATEGY_AGENT_BASE_URL=
WEEKLY_STRATEGY_AGENT_API_KEY=
WEEKLY_STRATEGY_AGENT_TIMEOUT_SEC=60
WEEKLY_STRATEGY_AGENT_MAX_RETRIES=1
WEEKLY_STRATEGY_AGENT_MAX_INPUT_CHARS=16000
WEEKLY_STRATEGY_AGENT_MAX_OUTPUT_CHARS=12000
WEEKLY_STRATEGY_AGENT_PROMPT_VERSION=weekly-strategy-agent-v1
WEEKLY_STRATEGY_AGENT_SCHEMA_VERSION=weekly-strategy-agent-result-v1
```

With defaults, no external call is made. Disabled tasks are skipped with `weekly_strategy_agent_disabled`; provider-off tasks fail closed with `weekly_strategy_agent_provider_disabled`.

## Input payload

Example payload:

```json
{
  "period_days": 7,
  "search_ids": [1, 2, 3],
  "include_system_memory": true,
  "include_market_evidence_stats": true,
  "include_agent_task_stats": true,
  "max_examples_per_section": 10
}
```

`period_days` defaults to `7` and is capped at `30`. `max_examples_per_section` defaults to `10` and is capped at `25`. If `search_ids` is omitted, the collector reviews active searches and still bounds examples.

## Time window and reproducibility

The service resolves one timezone-aware UTC `report_as_of_datetime`. From it the service derives `period_end_at`, `period_start_at`, and `report_as_of_date`. Provider output cannot override these values.

The service stores a compact stats snapshot in the task payload together with `stats_snapshot_hash`. The weekly input hash includes the normalized payload, period window, report date bucket, search scope, `stats_snapshot_hash`, optional `context_hash`, prompt version, and schema version. The same payload next week produces a different hash.

## Data read

The collector reads compact summaries from existing tables such as searches, listings, listing/search matches, analyses, alerts, agent tasks, market research runs, market evidence items, and active knowledge notes when system-memory context is requested.

It does not include secrets, raw credentials, raw HTML, or unbounded descriptions.

## Output schema

The provider controls analytical fields only: confidence, executive summary, health status, findings, recommendations, suggested next PR, and limitations. The service wraps that output with schema/prompt versions, period window, generated time, provider/model, `stats_snapshot_hash`, optional `context_hash`, used context refs, guardrails, and mutation-scope metadata.

`human_approval_required` is always true. `side_effects_performed` is always false. `allowed_mutation_scope` is always `agent_tasks_only`.

## Manual run

Create a pending task by the existing supported path, then run:

```bash
python -m app.cli run-agent-tasks --task-type weekly_strategy_agent --limit 1
```

Inspect `agent_tasks.result_json` for the advisory report or fail-closed error metadata.

## What PR17 must not mutate

PR17 must not create or mutate listings, listing analyses, search jobs, filters, alerts, market research runs, market evidence items, knowledge notes, listing enrichments, or detail snapshots. The only expected mutation is the target `agent_tasks` row status/result/error metadata through the existing AgentTask lifecycle.

## Interpreting recommendations

Recommendations are advisory and should be treated as candidate human actions. They may identify an area, priority, rationale, expected impact, and a suggested human action. They do not change filters, code, searches, scores, verdicts, alerts, market evidence, or RAG memory automatically.

## PR18+ boundary and non-goals

PR17 does not implement human outcome tracking. PR17 does not implement admin UI. PR17 does not implement alert retry dashboard. PR17 does not implement health dashboard. PR17 does not implement backup/restore. PR17 does not implement access control. PR17 does not implement comp quality scoring. PR17 does not implement automatic filter/code changes. PR17 does not implement scheduling.
