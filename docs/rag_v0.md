# RAG v0 knowledge notes

PR8 added the local substrate for retrieval-augmented review. PR9 connects that substrate to ReviewCopilot only, while keeping RAG disabled by default and keeping monitoring, scoring, alerts, parsing, and delivery unchanged.

The system rule remains:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Human approves action.
```

## What PR8 adds

- `knowledge_notes`: a local Postgres-backed structured knowledge-note store.
- Strict note types:
  - `rulebook`
  - `false_positive`
  - `domain_note`
- Deterministic lexical retrieval over note title, body, and tags.
- Validation for note type, profile, title, body, tags, metadata, priority, active state, and bounded search limits.

## PR9 ReviewCopilot-only integration

PR9 allows ReviewCopilot to include selected, bounded local notes as context when `LLM_REVIEW_COPILOT_RAG_ENABLED=true`. Notes are context only, may contain untrusted text, and must not override deterministic score or verdict. The LLM output schema remains PR7-compatible; deterministic `rag_context` audit metadata is appended in code only after successful validation. See [ReviewCopilot RAG context](review_copilot_rag.md).

## What RAG v0 intentionally does not add

- No monitor-cycle integration.
- No scoring or verdict impact.
- No alert-delivery impact.
- No parser or browser integration.
- No LLM calls.
- No embedding calls.
- No external research or external HTTP calls.
- No Chroma, pgvector, vector DB, or embedding infrastructure.
- No Postgres full-text search, `to_tsvector`, language dictionaries, or database ranking.
- No automatic note ingestion or false-positive learning.
- No new required environment variables or runtime default changes.

## Knowledge note fields

A note stores:

- `note_type`: one of `rulebook`, `false_positive`, or `domain_note`.
- `profile`: normalized retrieval scope, defaulting to `global`.
- `title`: short human-readable title.
- `body_md`: Markdown body.
- `tags_json`: normalized list of tags.
- `metadata_json`: optional object for future-safe metadata.
- `source` / `source_ref`: optional provenance labels.
- `priority`: integer used for deterministic ordering.
- `is_active`: active notes are searchable by default.

Profile-specific search includes `global` notes so common guidance can be returned alongside profile-specific notes.

## Retrieval behavior

RAG v0 retrieval is deliberately simple and deterministic:

1. Search requires a non-empty query.
2. The query is lowercased and split on whitespace.
3. A note matches if at least one unique query token appears in its title, body, or tags.
4. The lexical score is the number of unique query tokens matched.
5. Results are bounded and ordered by:
   - `priority desc`
   - `lexical_score desc`
   - `updated_at desc`
   - `id desc`

This is a local lexical API only. It does not call LLM providers, embedding providers, browsers, parsers, or external services.

## Safety warning

Do not store secrets, credentials, API keys, webhook URLs, raw HTML dumps, private raw payloads, cookies, sessions, tokens, large listing snapshots, or full external documents in `knowledge_notes`.
