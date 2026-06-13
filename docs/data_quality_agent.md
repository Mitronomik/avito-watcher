# DataQualityAgent (PR12)

PR12 adds a manual, shadow-only `data_quality_agent` AgentTask handler. It evaluates diagnostic data quality for one persisted listing using listing fields, optional deterministic analysis, optional detail snapshot, optional successful `llm_listing_detail_extraction`, and optional local `knowledge_notes` RAG context.

The agent does **not** score listings, change verdicts, send alerts, auto-repair data, mutate filters, create tasks automatically, fetch live pages, use external research, use embeddings/vector DB/full-text search, store raw HTML, or mutate RAG notes. It is disabled by default with `LLM_DATA_QUALITY_AGENT_ENABLED=false`. RAG is also disabled by default with `LLM_DATA_QUALITY_AGENT_RAG_ENABLED=false` and must be explicitly enabled.

Successful validated assessments are stored in existing `listing_enrichments` rows with `enrichment_type=data_quality_assessment`. No DB migration was needed; PR12 reuses `listing_enrichments` added in PR11.

## Schema and prompt

Prompt version: `data-quality-agent-v1`.
Schema version: `data-quality-assessment-schema-v1`.

The bounded prompt instructs the model to return strict JSON only, treat listing/snapshot/extraction text as untrusted user-generated evidence, not follow commands inside listing text, not use external knowledge, and not produce score/verdict/alert decisions. Missing PR11 extraction is tolerated and recorded as missing evidence/warnings.

The assessment includes `overall_status`, `review_priority`, issues, contradictions, missing/uncertain evidence, RAG references, bounded human-review recommendations, and optional `recommended_rule_patch`.

`recommended_rule_patch` is advisory text only for human review. It cannot contain executable code, code fences, shell commands, SQL DDL/DML, migrations, config diffs, JSON Patch, file edits, ready-to-apply patches, filter/scoring/verdict changes, alert suppression, automatic repair instructions, or instructions to create/update `knowledge_notes`.

## RAG

When enabled, local RAG retrieval reads only active `knowledge_notes` with bounded note types, query length, result count, and total snippet characters. Notes are project memory/context, not authoritative market truth. If retrieval fails, the task fails closed before provider call. If no notes are found, execution continues with a warning. RAG notes are never created or edited.

## Failure modes

Manual payload ids (`listing_analysis_id`, `snapshot_id`, and `extraction_enrichment_id`) are accepted only when they belong to the requested `listing_external_id` and pass status/type constraints. Mismatched or unusable explicit ids fail closed before provider call and do not create new enrichment rows.

The handler fails/skips without creating enrichment rows for disabled feature flag, provider off, unsupported provider, missing listing, mismatched explicit payload ids, too-thin input, RAG retrieval failure, provider error, malformed JSON, schema validation failures, forbidden decision-like output, and invalid `recommended_rule_patch` content.

## Controlled production smoke

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  -e LLM_DATA_QUALITY_AGENT_ENABLED=true \
  -e LLM_DATA_QUALITY_AGENT_RAG_ENABLED=true \
  app python -m app.cli run-agent-tasks --task-type data_quality_agent --limit 1
```

Suggested SQL checks:

```sql
select enrichment_type, status, count(*)
from listing_enrichments
group by enrichment_type, status
order by enrichment_type, status;

select task_type, status, count(*)
from agent_tasks
group by task_type, status
order by task_type, status;

select count(*) from alerts_sent;
```

Future PRs may consume these findings only after human review and explicit integration work; PR12 itself is diagnostic/shadow-only.
