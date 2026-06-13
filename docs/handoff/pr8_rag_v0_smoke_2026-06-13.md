# PR 8 — RAG v0 production smoke

PR 8 — RAG v0 substrate is merged, deployed, and smoked in production.

## Production checks

- Alembic current reached `0010_knowledge_notes (head)`.
- `knowledge_notes` table exists.
- DB-level defaults for `knowledge_notes` were verified with a rollback SQL insert.
- `knowledge_notes` remained empty after rollback smoke.
- App starts successfully.
- App container is healthy.
- `/health` returns `{"status":"ok"}`.
- Worker starts normally.
- Worker cycle completes normally.
- No automatic knowledge-note ingestion happened.
- No automatic ReviewCopilot AgentTasks were created.
- No alerts were created after smoke baseline.
- Existing ReviewCopilot controlled task remained the only AgentTask.
- No scoring/verdict mutation was observed.

## Verified production values

- alerts_sent baseline id: `2766`
- alerts after baseline: `0`
- agent_tasks:
  - `review_copilot / success / 1`
- knowledge_notes_count: `0`

## Conclusion

PR 8 production smoke is closed.

RAG v0 is confirmed as substrate only:

- local `knowledge_notes` store;
- DB-level defaults are active;
- deterministic lexical retrieval service exists;
- no ReviewCopilot integration yet;
- no monitor-cycle integration;
- no scoring integration;
- no alert side effects;
- no embeddings;
- no vector DB;
- no Postgres full-text search;
- no external calls.

## Next roadmap step

PR 9 — ReviewCopilot with RAG context.
