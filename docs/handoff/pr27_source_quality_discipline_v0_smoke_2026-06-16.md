# PR27 — Source quality / verification discipline v0: production smoke handoff

Date: 2026-06-16

Status: merged, deployed, production-smoked.

This handoff documents the production deployment and smoke verification for PR27.

## Scope reminder

PR27 adds deterministic source quality / verification discipline v0.

PR27 is a read-only source reliability assessment layer for market evidence comparables.

It is not a source verification workflow and does not perform external verification.

It does not replace PR24, PR25, or PR26.

Current comparable stack after PR27:

```text
market evidence candidates
-> PR25 comparable selection policy v2
-> PR24 comparable quality scoring v0
-> PR27 source quality / verification discipline v0
-> PR26 adjusted comparable model v0
-> market evidence facts / market-derived rent estimate
```

PR27 answers:

```text
How reliable and traceable is the source behind this comparable evidence?
```

PR27 does not answer:

```text
What is the correct market rent?
What is the investment verdict?
What is the appraised value?
```

## Architectural boundary

PR27 preserves the project architecture:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates assumptions.
Human approves action.
```

PR27 is deterministic.

PR27 must not call:

- LLM;
- agents;
- RAG;
- external APIs;
- browser/parser;
- geocoding;
- embeddings;
- semantic search;
- web search;
- admin write actions.

PR27 must not mutate existing evidence rows.

PR27 must not persist `source_quality` to `market_evidence_items`.

PR27 must not update `source_type` or `verification_status`.

PR27 must not backfill old analyses.

PR27 must not change adjusted rent values.

PR27 must not directly rewrite:

- investment_score;
- verdict;
- broad score cap;
- deterministic profile formula;
- manual rent;
- adjusted_rent_per_m2;
- adjusted_median_rent_per_m2.

Source quality can only:

```text
add facts
add review reasons
cap/lower market evidence confidence
```

## Merged revision

Production was updated from:

```text
81e1f65792bf836375fcfeac2e3df0eda69c3728
```

to:

```text
48c523e830694e028ea035094661e3e4e52516c8
```

Production HEAD after pull:

```text
48c523e Add deterministic source quality discipline v0 (#220)
b6ad8e5 Add PR26 production smoke handoff (#219)
0729cd7 Add PR25 production smoke handoff (#217)
81e1f65 Add deterministic adjusted comparable model v0 (#218)
4c4d4eb Add deterministic comparable selection policy v2 (#216)
cb7c882 Add PR24 production smoke handoff (#215)
```

Files pulled as part of the fast-forward included:

```text
app/analysis/market_comps.py
app/analysis/provider.py
docs/handoff/pr25_comparable_selection_policy_v2_smoke_2026-06-16.md
docs/handoff/pr26_adjusted_comparable_model_v0_smoke_2026-06-16.md
docs/market_evidence.md
tests/test_source_quality.py
```

No Alembic migration file was added.

## Deploy commands executed

Production path:

```bash
cd ~/apps/avito-watcher
```

Pull commands:

```bash
git status --short
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main
git pull --ff-only origin main
git log --oneline -6
```

Build and restart:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Build completed successfully.

Containers restarted successfully:

```text
Container deploy-redis-1    Healthy
Container deploy-postgres-1 Healthy
Container deploy-worker-1   Started
Container deploy-app-1      Started
```

## Health check

Command:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Verdict:

```text
App health OK.
```

## Alembic check

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec app \
  sh -lc 'alembic current'

docker compose --env-file .env -f deploy/docker-compose.prod.yml exec app \
  sh -lc 'alembic heads'
```

Result:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

Verdict:

```text
No migration required.
Alembic unchanged.
Single head observed in production.
```

## Runtime log safety grep

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=350 app worker \
  | grep -Ei "Traceback|ERROR|Exception|source_quality|verification_status|source_type|verified|human_verified|adjusted_rent|adjusted_median|valuation|appraisal|DCF|scenario|financing|semantic|embedding|geocod|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros" || true
```

Matches:

```text
worker-1 | ... avito_parser.end_cycle stats={... 'engine_error_count': 0, ...}
worker-1 | ... monitor_service.cycle_summary searches_processed=0 ... engine_errors=0 ...
```

Interpretation:

```text
The grep only matched normal statistics fields such as engine_error_count=0 / engine_errors=0.
No actual traceback, exception, or runtime error was found.
```

No runtime log evidence of:

- `source_quality` facts being dumped to logs;
- `verification_status` dumps;
- `source_type` dumps;
- `verified` / `human_verified` dumps;
- `adjusted_rent` / `adjusted_median` dumps;
- valuation/appraisal/DCF/scenario/financing claims;
- semantic/embedding/geocoding behavior;
- auth headers or secrets;
- admin technical write key;
- Apps Script delivery URL.

Verdict:

```text
Runtime log safety grep passed.
```

## Database baseline

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
select
  count(*) as analyses_total,
  max(id) as last_analysis_id,
  max(created_at) as last_created_at
from listing_analyses;

select
  count(*) as market_evidence_items_total
from market_evidence_items;
SQL
```

Result:

```text
analyses_total   | 730
last_analysis_id | 730
last_created_at  | 2026-06-07 18:52:39.696919

market_evidence_items_total | 0
```

Interpretation:

```text
Existing analyses remain present.
No new analysis was created during this smoke.
No persisted market evidence items exist in production at this point.
```

Important limitation:

```text
Because market_evidence_items_total = 0, this smoke verifies deployment, startup, admin rendering, compatibility, and safety.
It does not verify real source_quality computation over persisted production market evidence items.
```

This is an expected limitation, not a blocker for PR27 deployment.

## Admin read-key check

Read key extraction was performed with shell tracing disabled:

```bash
set +x
```

Read key length:

```text
read_key_len=64
```

No secret value was printed.

## Admin page smoke

Commands:

```bash
curl -sS -o /tmp/pr27_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

curl -sS -o /tmp/pr27_analyses.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/listing-analyses"

curl -sS -o /tmp/pr27_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Results:

```text
/admin/system           200
/admin/listing-analyses 200
/admin/review-queue     200
```

Verdict:

```text
Admin pages render successfully after PR27 deploy.
```

## Admin/UI safety grep

Command:

```bash
grep -Ei "payload_json|result_json|source_quality_model_version|raw evidence_json|admin_technical_write_key|Authorization:|Cookie:|X-API-Key:|script\.google\.com/macros/s/" \
  /tmp/pr27_system.html /tmp/pr27_analyses.html /tmp/pr27_review_queue.html || true
```

Result:

```text
No matches.
```

Verdict:

```text
No raw payload/result JSON exposure found.
No source_quality_model_version leak in admin list pages.
No raw evidence_json leak found.
No auth header / cookie / API key leak found.
No admin technical write key leak found.
No Apps Script URL leak found.
```

## Service status

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

Result:

```text
NAME                SERVICE    STATUS

deploy-app-1        app        Up About a minute (healthy)
deploy-postgres-1   postgres   Up 37 hours (healthy)
deploy-redis-1      redis      Up 2 weeks (healthy)
deploy-worker-1     worker     Up About a minute
```

Verdict:

```text
App is healthy.
Postgres is healthy.
Redis is healthy.
Worker is running.
```

## Worker log tail

Worker log tail showed:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
monitor worker runtime diagnostics: {...}
avito_parser.end_cycle stats={... engine_error_count: 0 ...}
monitor_service.cycle_summary searches_processed=0 ... engine_errors=0 ...
monitor cycle completed
```

Interpretation:

```text
PROXY_URLS not set is an existing environment warning and is unrelated to PR27.
engine_error_count=0 and engine_errors=0 are normal metric fields.
Worker cycles completed successfully.
searches_processed=0 is current production search scheduling/config behavior and not a PR27 failure.
```

## PR27-specific smoke conclusion

Confirmed:

```text
PR27 code deployed.
No migration required.
Alembic unchanged.
App starts and health endpoint returns 200.
Worker starts and completes cycles.
Admin pages render.
No runtime traceback/error/exception found.
No evidence mutation path observed during smoke.
No source_quality/source_type/verification_status facts dumped to logs.
No valuation/appraisal/DCF/scenario/financing leakage found.
No semantic/embedding/geocoding behavior observed.
No raw payload/secret/header/App Script URL leak found in admin pages.
```

Not confirmed in production due to missing persisted evidence:

```text
Real source_quality computation over persisted production market_evidence_items.
```

Reason:

```text
market_evidence_items_total = 0
```

This limitation should be revisited when production has persisted market evidence items.

## Final verdict

```text
PR27 — Source quality / verification discipline v0 ✅
Merged ✅
Deployed ✅
No migration ✅
Alembic unchanged ✅
App health OK ✅
Worker running ✅
Admin pages render ✅
No raw payload/secret leak detected ✅
No external verification / valuation / DCF / scenario leakage in logs ✅
Production smoke passed ✅
```

## Next roadmap step

After PR27, the comparable evidence stack is now:

```text
PR24 comparable quality scoring
PR25 comparable selection policy
PR26 adjusted comparable model
PR27 source quality / verification discipline
```

Next likely functional step:

```text
PR28 — Sale / cap-rate evidence read model v0
```

PR28 should remain a read model / deterministic evidence layer and must not become DCF/scenario/financing or professional appraisal.
