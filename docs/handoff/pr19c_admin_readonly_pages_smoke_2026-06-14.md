# PR19c — Admin read-only pages production smoke

Date: 2026-06-14  
Environment: production (`avito-watcher-prod`)  
Repository: `Mitronomik/avito-watcher`  
Branch: `main`  
Production HEAD: `ed0a6b7 Add read-only admin evidence agents outcome pages (#184)`  
Scope: PR19c — read-only Admin UI pages for market evidence, agent tasks and outcome analytics

## 1. Purpose

This handoff records the safe production deploy and smoke test for PR19c.

PR19c added read-only Admin UI pages:

```text
/admin/evidence
/admin/evidence/runs/{run_id}
/admin/agents
/admin/agents/{task_id}
/admin/outcome-analytics
```

The smoke verified that these pages are available in production, require the Admin UI read key, do not expose mutation actions, and do not create database side effects.

## 2. Deployment

The production checkout was already on the production host:

```text
deploy@avito-watcher-prod:~/apps/avito-watcher
```

An attempted nested SSH command failed because the shell was already on the production host:

```text
ssh: Could not resolve hostname avito-beget: Name or service not known
```

This was not a deployment blocker.

Repository state after update:

```text
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
Already up to date.
ed0a6b7 (HEAD -> main, origin/main, origin/HEAD) Add read-only admin evidence agents outcome pages (#184)
```

## 3. Compose, services and Alembic

Production compose config was validated:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null
```

Base services were running and healthy:

```text
postgres: Running / Healthy
redis: Running / Healthy
```

Alembic checks:

```text
0014_human_review_tracking (head)
```

No PR19c migration was added or expected.

## 4. Build and service restart

Built production images:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker
```

Result:

```text
Image deploy-app    Built
Image deploy-worker Built
```

Started app and worker:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Container status:

```text
deploy-app-1        Up / healthy   127.0.0.1:8010->8000/tcp
deploy-postgres-1   Up / healthy
deploy-redis-1      Up / healthy
deploy-worker-1     Up
```

Health check:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

## 5. Admin UI configuration check

The smoke did not print secret values. It only checked whether keys were present.

Observed config:

```text
ADMIN_UI_ENABLED=true
ADMIN_UI_ALLOW_QUERY_API_KEY=false
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
ADMIN_UI_READ_KEY is set
ADMIN_UI_WRITE_KEY is set
ADMIN_UI_TECHNICAL_WRITE_KEY is NOT set
```

Interpretation:

```text
Read-only Admin UI pages are enabled.
Query-string API key access is disabled.
Technical operations are disabled.
No technical write key is configured.
```

This is acceptable for PR19c smoke because PR19c is read-only.

## 6. Database baseline before smoke

Counts before GET requests:

```text
        table_name        | row_count
--------------------------+-----------
 agent_tasks              |         2
 alerts_sent              |      2874
 human_review_actions     |         0
 human_reviews            |         0
 investment_decisions     |         0
 knowledge_notes          |         0
 listing_analyses         |       730
 listing_detail_snapshots |         0
 listing_enrichments      |         0
 listings                 |      1527
 market_evidence_items    |         0
 market_research_runs     |         0
 search_jobs              |         2
```

## 7. Read-only page smoke

Requests used `X-API-Key` with `ADMIN_UI_READ_KEY`.

Results:

```text
GET /admin                                           -> 200
GET /admin/evidence?limit=20                        -> 200
GET /admin/agents?limit=20                          -> 200
GET /admin/outcome-analytics?period_days=30&max_examples=10 -> 200
```

Detail pages:

```text
market_research_runs sample id: none
agent_tasks sample id: 3
GET /admin/agents/3 -> 200
```

`/admin/evidence/runs/{run_id}` was skipped because `market_research_runs` was empty in production at smoke time.

## 8. Mutation route protection

Unsupported POST requests returned 405:

```text
POST /admin/evidence           -> 405
POST /admin/agents             -> 405
POST /admin/outcome-analytics  -> 405
```

This confirms that PR19c did not introduce write endpoints for these pages.

## 9. Database counts after smoke

Counts after GET and unsupported POST requests:

```text
        table_name        | row_count
--------------------------+-----------
 agent_tasks              |         2
 alerts_sent              |      2874
 human_review_actions     |         0
 human_reviews            |         0
 investment_decisions     |         0
 knowledge_notes          |         0
 listing_analyses         |       730
 listing_detail_snapshots |         0
 listing_enrichments      |         0
 listings                 |      1527
 market_evidence_items    |         0
 market_research_runs     |         0
 search_jobs              |         2
```

`diff -u /tmp/pr19c_counts_before.txt /tmp/pr19c_counts_after.txt` produced no output.

Interpretation:

```text
No database side effects were detected.
```

## 10. Logs

App logs filtered for:

```text
traceback|exception|error|warning
```

No matching app log output was reported during smoke.

Worker logs showed normal runtime diagnostics and empty monitor cycles:

```text
searches_processed=0
blocks=0
engine_errors=0
browser_driver_crashes=0
proxy_failures=0
```

No unexpected PR19c-related agent/research/LLM/delivery side effects were detected.

## 11. Operational note

Worker diagnostics reported the legacy LLM summary configuration as enabled:

```text
scoring_enabled=True
llm_provider=openai_compatible
llm_shadow_mode=False
```

This was not a PR19c blocker because the smoke did not process searches and did not create side effects.

However, this should be reviewed separately to confirm whether the production legacy LLM scoring configuration is intentional. The project architecture keeps deterministic analysis as the source of score/verdict, while LLM layers should explain or enrich rather than become the final scorer.

## 12. Smoke verdict

```text
PR19c — Admin read-only evidence, agents and outcome analytics pages
Production deploy: done
Production smoke: closed
Health: OK
Alembic: current at head
Read-only pages: OK
Mutation routes: blocked
DB side effects: none
Logs: clean for PR19c scope
```

Final verdict:

```text
PR19c production smoke closed ✅
```

## 13. Next step

Proceed to the next roadmap item only after this handoff is merged into `main`.

Recommended next roadmap step:

```text
PR19d — Technical operations hardening
```

PR19d should cover technical Admin UI operations separately from the PR19c read-only pages, with a technical key, explicit confirmations, no accidental public access, and audit-safe behavior.
