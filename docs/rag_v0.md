# RAG v0 knowledge notes

PR8 adds only the local substrate for future retrieval-augmented review. It does not connect RAG to agents, monitoring, scoring, alerts, parsing, or delivery.

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

## What PR8 intentionally does not add

- No ReviewCopilot integration yet.
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

PR9 may connect selected, bounded RAG context to ReviewCopilot. That future use must keep RAG contextual only; it must not become a scoring engine or mutate deterministic decisions automatically.

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
