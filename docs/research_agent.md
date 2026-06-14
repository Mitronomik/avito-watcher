# ResearchAgent manual market research shadow mode (PR14)

PR14 adds a manual `market_research` AgentTask for advisory external research. It is disabled by default and the provider defaults to `off`, so mandatory production behavior performs no external calls.

## Scope and boundaries

ResearchAgent is manual only: operators create an `agent_tasks` row explicitly with `task_type='market_research'`. It is not called by the monitor cycle, investment profiles, deterministic scoring, alerts, filters, Google Sheets, ReviewCopilot, DataQualityAgent, RAG retrieval, embeddings, or vector DB code.

The task stores validated advisory output only in `agent_tasks.result_json`. It does not create or mutate `listing_enrichments`, `knowledge_notes`, `listing_detail_snapshots`, `market_research_runs`, `market_evidence_items`, market RAG records, scores, verdicts, alerts, filters, search jobs, investment metrics, or worker cadence. It does not trigger reanalysis.

PR14 performs deterministic query planning, uses a source-backed provider abstraction when explicitly enabled, and extracts comparable candidates into bounded `result_json` only. Generic LLM output without sources is not verified market research. Source-less factual claims are treated as invalid/unverifiable and must be put in limitations or human-review questions rather than successful findings/comps.

PR15 is expected to handle reusable market evidence storage and market RAG. PR16 is expected to handle investment profiles v1 with comps. PR14 does neither.

## Configuration

Defaults are safe:

```env
RESEARCH_AGENT_ENABLED=false
RESEARCH_AGENT_PROVIDER=off
RESEARCH_AGENT_MODEL=
RESEARCH_AGENT_BASE_URL=
RESEARCH_AGENT_API_KEY=
RESEARCH_AGENT_TIMEOUT_SEC=60
RESEARCH_AGENT_MAX_RETRIES=1
RESEARCH_AGENT_MAX_QUERIES=3
RESEARCH_AGENT_MAX_INPUT_CHARS=12000
RESEARCH_AGENT_MAX_OUTPUT_CHARS=12000
RESEARCH_AGENT_PROMPT_VERSION=research-agent-v1
RESEARCH_AGENT_SCHEMA_VERSION=research-agent-result-v1
```

Disable the feature by leaving `RESEARCH_AGENT_ENABLED=false` or setting `RESEARCH_AGENT_PROVIDER=off`. With the feature disabled, a manual task is skipped with `research_agent_disabled`. With provider `off`, it fails closed with `research_agent_provider_disabled`.

## Manual task payload

Minimum payload:

```json
{
  "listing_external_id": "1234567890"
}
```

Optional fields:

```json
{
  "listing_external_id": "1234567890",
  "listing_analysis_id": 123,
  "research_profile": "commercial_rent_location",
  "research_questions": [
    "Проверить окружение и спрос на аренду",
    "Проверить возможные факторы риска по локации"
  ],
  "max_queries": 3
}
```

Supported profiles are `default`, `commercial_rent_location`, `commercial_sale_investment`, and `flat_sale_investment`. Unknown profiles fail closed.

## Output

Validated output is wrapped in `agent_tasks.result_json` with task metadata, provider/model, input/output hashes, query plan, and the strict `research-agent-result-v1` result. The result includes summary, findings, comparable candidates, risks, opportunities, assumptions to verify, human-review questions, sources, limitations, confidence, and review recommendation.

Comparable candidates are advisory only. They do not feed deterministic scoring and do not create `market_evidence_items`.

## Safe smoke commands

Mandatory smoke should avoid real external research:

```bash
python3 -m compileall app
alembic heads
```

Create a manual `market_research` task for an existing harmless listing while `RESEARCH_AGENT_ENABLED=false`, run the AgentTask runner, and confirm the task is `skipped` with `research_agent_disabled`. Then run a one-off process with:

```env
RESEARCH_AGENT_ENABLED=true
RESEARCH_AGENT_PROVIDER=off
```

Create/run one manual task and confirm it fails closed with `research_agent_provider_disabled`. Confirm no alerts, `listing_enrichments`, `knowledge_notes`, `market_research_runs`, `market_evidence_items`, market RAG records, listing-detail snapshot mutations, score mutations, or verdict mutations were created. Optional fake-client smoke may exercise success without network. Optional real-provider smoke must be separated from mandatory smoke, use one manual task, and verify no side effects.
