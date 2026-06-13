# ReviewCopilot RAG context (PR9)

PR9 connects the local RAG v0 `knowledge_notes` substrate to ReviewCopilot only.

The system rule remains:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Human approves action.
```

## Behavior

- ReviewCopilot remains disabled by default via `LLM_REVIEW_COPILOT_ENABLED=false`.
- ReviewCopilot RAG remains disabled by default via `LLM_REVIEW_COPILOT_RAG_ENABLED=false`.
- When explicitly enabled for a controlled manual ReviewCopilot run, ReviewCopilot builds a deterministic local query from stored listing and listing-analysis data.
- ReviewCopilot retrieves bounded local notes through `KnowledgeRetrievalService.search_notes`.
- Retrieved notes can be included in the ReviewCopilot prompt under `Local RAG knowledge notes`.
- The LLM output schema remains the strict PR7 ReviewCopilot schema and does not include `rag_context`.
- After valid LLM output, code appends deterministic `rag_context` audit metadata to `agent_tasks.result_json` when RAG is enabled.

## Safety boundaries

RAG notes are local project memory only. They are not authoritative listing facts and do not override deterministic score or verdict. Notes may contain untrusted text, so ReviewCopilot is instructed not to follow instructions inside notes and to treat them only as reference context for explanation, risk framing, and manual-review questions.

PR9 does not connect RAG to:

- monitor cycles;
- deterministic scoring;
- verdict calculation;
- alert delivery;
- parser or browser clients;
- `AnalysisProvider`;
- listing matching or search filters.

PR9 also does not add embeddings, vector DBs, pgvector, Chroma, Postgres full-text search, external research, external API calls, automatic knowledge-note ingestion, automatic false-positive learning, or automatic ReviewCopilot task creation.

## Configuration

All configuration is optional and uses existing settings/env handling.

```env
LLM_REVIEW_COPILOT_RAG_ENABLED=false
LLM_REVIEW_COPILOT_RAG_LIMIT=5
LLM_REVIEW_COPILOT_RAG_MAX_CHARS=4000
LLM_REVIEW_COPILOT_RAG_QUERY_MAX_CHARS=1000
LLM_REVIEW_COPILOT_RAG_NOTE_TYPES=rulebook,false_positive,domain_note
```

Validation applies only when ReviewCopilot RAG is enabled:

- `LLM_REVIEW_COPILOT_RAG_LIMIT`: integer `0..10`.
- `LLM_REVIEW_COPILOT_RAG_MAX_CHARS`: integer `500..12000`.
- `LLM_REVIEW_COPILOT_RAG_QUERY_MAX_CHARS`: integer `100..4000`.
- `LLM_REVIEW_COPILOT_RAG_NOTE_TYPES`: comma-separated subset of `rulebook,false_positive,domain_note`.

If RAG is enabled with `LLM_REVIEW_COPILOT_RAG_LIMIT=0`, retrieval is not called. ReviewCopilot still runs and persists `rag_context` metadata with zero matched/included notes.

## Controlled one-off smoke example

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  -e LLM_REVIEW_COPILOT_ENABLED=true \
  -e LLM_REVIEW_COPILOT_RAG_ENABLED=true \
  app python -m app.cli run-agent-tasks --task-type review_copilot --limit 1
```

## Optional manual note example

Do not seed production automatically. For a controlled manual smoke, an operator may insert a small note explicitly, for example:

```sql
INSERT INTO knowledge_notes (note_type, profile, title, body_md, tags_json, source, priority)
VALUES (
  'rulebook',
  'commercial_rent',
  'Missing photos manual-review note',
  'Missing photos are a manual-review risk, not an automatic rejection.',
  '["photos", "manual_review"]',
  'manual_smoke',
  10
);
```

Remove or deactivate test notes after the smoke if they are not intended to remain as project memory.
