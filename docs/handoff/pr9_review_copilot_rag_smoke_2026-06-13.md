# PR 9 — ReviewCopilot RAG production smoke

PR 9 — ReviewCopilot with RAG context is merged, deployed, and smoked in production.

## Production checks

- Alembic current remains `0010_knowledge_notes (head)`.
- App starts successfully.
- `/health` returns `{"status":"ok"}`.
- `LLM_REVIEW_COPILOT_ENABLED=false` in `.env`.
- `LLM_REVIEW_COPILOT_RAG_ENABLED` is not set in `.env`, so RAG remains disabled by default.
- Worker starts normally.
- Worker cycle completes normally.
- No automatic ReviewCopilot AgentTasks were created.
- No automatic knowledge-note ingestion happened.

## Controlled RAG smoke

A temporary `knowledge_notes` row was created for smoke testing:

- note id: `2`
- note_type: `domain_note`
- profile: `commercial_rent`
- source: `manual_smoke`
- source_ref: `pr9_smoke_2026-06-13`

A manual ReviewCopilot task was created:

- task id: `3`
- task_type: `review_copilot`
- listing_external_id: `8147836490`
- listing_analysis_id: `730`
- search_job_id: `2`
- context_key: `search:2`

The task was executed with:

- `LLM_REVIEW_COPILOT_ENABLED=true`
- `LLM_REVIEW_COPILOT_RAG_ENABLED=true`
- `LLM_REVIEW_COPILOT_RAG_LIMIT=5`
- `LLM_REVIEW_COPILOT_RAG_MAX_CHARS=4000`
- `LLM_REVIEW_COPILOT_RAG_QUERY_MAX_CHARS=1000`

Result:

- processed: `1`
- succeeded: `1`
- failed: `0`
- task status: `success`
- `rag_context.enabled=true`
- `rag_context.matched_count=1`
- `rag_context.included_count=1`
- `rag_context.notes[0].id=2`
- `rag_context.notes[0].source_ref=pr9_smoke_2026-06-13`

## Verified no side effects

Controlled smoke baseline:

- alerts_sent baseline id: `2774`
- alerts after baseline: `0`

Analysis remained unchanged:

- listing_analysis_id: `730`
- listing_external_id: `8147836490`
- status: `success`
- score: `50`
- verdict: `review`

Agent tasks after smoke:

- `review_copilot / success / 2`

The temporary smoke note was deleted:

- remaining smoke notes: `0`
- knowledge_notes_count: `0`

## Conclusion

PR 9 production smoke is closed.

ReviewCopilot RAG is confirmed as safe/manual/shadow-mode only:

- RAG is disabled by default;
- ReviewCopilot remains disabled by default;
- RAG is used only during explicit manual ReviewCopilot task execution;
- `rag_context` is persisted for audit;
- no monitor-cycle integration;
- no scoring integration;
- no alert integration;
- no automatic AgentTask creation;
- no automatic note ingestion;
- no embeddings/vector DB/full-text search/external calls.

## Next roadmap step

PR 10 — listing detail enrichment.
