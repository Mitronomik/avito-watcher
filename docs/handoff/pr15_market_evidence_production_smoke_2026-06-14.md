# PR15 — Market evidence storage production smoke

Date: 2026-06-14  
Environment: `avito-watcher-prod`  
Branch deployed: `main`  
Merge commit: `c9306867dc9d010df5a37ee251da48b2959a89b2`  
PR: #164 — Add market evidence storage and SQL retrieval

## Scope

This handoff is intentionally standalone and documents only the PR15 production deploy and smoke verification.
It does not depend on PR14 handoff documents and does not define PR16 scope.

PR15 added the market evidence storage layer and SQL-backed market RAG context:

- Alembic migration `0013_market_evidence_storage`.
- New tables:
  - `market_research_runs`
  - `market_evidence_items`
- Ingestion of validated successful `market_research` `AgentTask.result_json` into reusable market evidence rows.
- Idempotent ingestion by scoped run/type/content hash rules.
- Bounded SQL retrieval via `MarketEvidenceRetriever`.
- SQL-backed advisory context via `MarketResearchRagContextBuilder`.

Architectural boundary preserved:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates market assumptions.
Human approves action.
```

PR15 must not change deterministic scoring, monitor decisions, alerts, enrichment snapshots, detail snapshots, or knowledge notes.

## Production deploy summary

Production repository was updated to merged `main`:

```text
c930686 (HEAD -> main, origin/main, origin/HEAD) Add market evidence storage and SQL retrieval (#164)
```

Changed files included the PR15 migration, market evidence models/repository/service, docs, and tests.

Important production safety rule followed:

```text
Do not run `docker compose down -v` on production.
```

The deploy sequence used:

```bash
git checkout main
git pull --ff-only origin main

docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d postgres redis

docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker
```

## PostgreSQL migration smoke

Alembic head after rebuild:

```text
0013_market_evidence_storage (head)
```

Before migration, production DB was at:

```text
0012_listing_enrichments
```

Migration command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic upgrade head
```

Observed migration log:

```text
Running upgrade 0012_listing_enrichments -> 0013_market_evidence_storage, market evidence storage
```

After migration:

```text
0013_market_evidence_storage (head)
```

Status:

```text
PostgreSQL migration smoke: PASS
```

## App and worker smoke

Services restarted:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Health check:

```bash
curl -i http://127.0.0.1:8010/health
```

Observed result:

```text
HTTP/1.1 200 OK
```

Worker log after restart did not show `Traceback`, `UndefinedTable`, or `OperationalError`.

Observed worker diagnostics still included:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning pre-existed PR15 and is not related to market evidence storage.

Worker cycle summary after deploy:

```text
searches_processed=0
monitor cycle completed
```

This is not a PR15 failure. It only means the immediate worker cycle had no searches to process.

## Table existence check

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select
  to_regclass('\''public.market_research_runs'\'') as market_research_runs,
  to_regclass('\''public.market_evidence_items'\'') as market_evidence_items;
"'
```

Observed result:

```text
 market_research_runs | market_evidence_items 
----------------------+-----------------------
 market_research_runs | market_evidence_items
(1 row)
```

Status:

```text
New PR15 tables exist: PASS
```

## Initial post-deploy counts

Immediately after deploy, before feature smoke:

```text
market_research_runs_count = 0
market_evidence_items_count = 0
```

This is expected. PR15 does not create evidence automatically during deploy.
Market evidence appears only after explicit ingestion from a successful `market_research` task.

## Feature smoke

A synthetic production smoke script was executed inside the app container.

The script created a synthetic listing and a successful `market_research` `AgentTask`, then called:

- `MarketEvidenceService.ingest_agent_task(task.id)` twice.
- `MarketEvidenceRetriever.retrieve(...)`.
- `MarketResearchRagContextBuilder.build_context(...)`.

The fake research result had to use a valid PR14/PR15 finding topic.
The valid topic used in the final smoke was:

```text
price_context
```

A previous attempt with `rent_context` failed validation with:

```text
ResearchAgentError: Invalid finding
MarketEvidenceError: Invalid finding
```

That was expected because `rent_context` is not an allowed `finding.topic`.
It is not a PR15 storage bug.

Observed successful first ingestion:

```text
first_ingest {
  'run_id': 1,
  'agent_task_id': 16,
  'listing_external_id': 'pr15-smoke-2026-06-14-listing',
  'created_run': True,
  'created_items': 4,
  'reused_items': 0,
  'skipped_items': 0,
  'non_reusable_items': 0,
  'confidence': 0.8,
  'checked_at': '2026-06-14T08:00:00',
  'expires_at': '2026-07-14T08:00:00'
}
```

Observed successful second ingestion:

```text
second_ingest {
  'run_id': 1,
  'agent_task_id': 16,
  'listing_external_id': 'pr15-smoke-2026-06-14-listing',
  'created_run': False,
  'created_items': 0,
  'reused_items': 4,
  'skipped_items': 0,
  'non_reusable_items': 0,
  'confidence': 0.8,
  'checked_at': '2026-06-14T08:00:00',
  'expires_at': '2026-07-14T08:00:00'
}
```

Observed success marker:

```text
PR15_SMOKE_OK
```

Feature smoke counters before cleanup:

```text
task_id 16
run_id 1
created_items 4
reused_items_second_ingest 4
runs_before 0
runs_after_before_cleanup 1
items_before 0
items_after_before_cleanup 4
```

Status:

```text
Market evidence ingestion: PASS
Idempotent re-ingestion: PASS
Market evidence retriever: PASS
SQL-backed market RAG context builder: PASS
```

## Side-effect checks

The feature smoke explicitly checked that unrelated production tables did not change.

Observed counts:

```text
alerts_before 2806
alerts_after 2806
alerts_max_before 2806
alerts_max_after 2806

tasks_before 2
tasks_after_before_cleanup 3

analyses_before 730
analyses_after 730

enrichments_before 0
enrichments_after 0

notes_before 0
notes_after 0

snapshots_before 0
snapshots_after 0
```

Interpretation:

- `agent_tasks` increased by 1 only because the smoke intentionally created one synthetic task.
- `market_research_runs` increased by 1 only during the smoke.
- `market_evidence_items` increased by 4 only during the smoke.
- Alerts did not change.
- Listing analyses did not change.
- Listing enrichments did not change.
- Knowledge notes did not change.
- Listing detail snapshots did not change.

Status:

```text
No unexpected side effects: PASS
```

## Cleanup verification

The smoke script ran cleanup in `finally` and removed synthetic rows from:

- `market_evidence_items`
- `market_research_runs`
- `agent_tasks`
- `listings`

Observed cleanup marker:

```text
PR15_SMOKE_CLEANUP_REMAINING 0
```

Final manual post-cleanup SQL check:

```text
market_research_runs_count = 0
market_evidence_items_count = 0
smoke_leftovers = 0
```

Status:

```text
Cleanup: PASS
```

## Final verdict

```text
PR15 production deploy: done
PostgreSQL migration smoke: passed
App health: passed
Worker restart: passed
New tables exist: passed
Feature smoke: passed
Ingestion idempotency: passed
Retriever: passed
SQL market RAG context builder: passed
No unexpected side effects: passed
Cleanup: passed

Status: CLOSED
```

## Notes for future PRs

This handoff does not authorize PR16 behavior by itself.

Future PRs may use PR15 storage as an advisory evidence source, but must preserve the established boundary unless explicitly changed by roadmap:

```text
Clean data first.
Deterministic gates second.
Deterministic scoring third.
Human-readable LLM explanation fourth.
RAG memory fifth.
External research sixth.
Investment scoring with comps seventh.
Agent strategy loop last.
```

Specifically:

- Do not let market evidence mutate deterministic score/verdict directly in PR15 semantics.
- Do not let market evidence trigger alerts automatically without a separate scoped PR.
- Do not make monitor cycle depend on external research.
- Keep human approval for strategy/action changes.
