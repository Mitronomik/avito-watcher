# PR 7 — ReviewCopilot production smoke

PR 7 — ReviewCopilot shadow mode is merged, deployed, and functionally smoked in production.

## Production checks

- `alembic current` remains `0009_alert_sent_created_at (head)`.
- App starts successfully.
- `/health` returns `200 OK`.
- `run-agent-tasks --task-type review_copilot --dry-run` works.
- No automatic `review_copilot` AgentTasks are created.
- Worker cycle completes normally.
- No alert spike after PR7 deploy.
- `alerts_sent` did not change during ReviewCopilot controlled run.
- `listing_analyses.score` and `listing_analyses.verdict` were not mutated.

## Controlled functional test

- AgentTask id: `2`
- task_type: `review_copilot`
- listing_external_id: `8147836490`
- listing_analysis_id: `730`
- search_job_id: `2`
- context_key: `search:2`
- result: `success`
- `result_json` was written successfully.
- `recommended_next_action`: `call_owner`
- `prompt_version`: `review-copilot-v1`

## Verified no side effects

- No alerts were created.
- Analysis remained unchanged:
  - score: `50`
  - verdict: `review`

## Conclusion

PR 7 production functional smoke is closed.

ReviewCopilot is confirmed as shadow-mode only:

- no monitor-cycle integration;
- no automatic task creation;
- no score/verdict mutation;
- no alert side effects;
- output only in `agent_tasks.result_json`.

## Data-quality observation

ReviewCopilot summary said “area is missing” even though the title contains `40 м2`.

This is acceptable for PR7 because ReviewCopilot uses stored structured data and should not infer facts from title as authoritative data.

This should be handled later in PR11 structured extraction / data enrichment, not in PR8.

## Next roadmap step

PR 8 — RAG v0: rulebook + false positives + domain notes.
