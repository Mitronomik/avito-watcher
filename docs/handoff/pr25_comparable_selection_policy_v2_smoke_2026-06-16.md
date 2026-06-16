# PR25 — Comparable selection policy v2: production smoke handoff

Date: 2026-06-16

Status: merged, deployed, production-smoked.

This handoff documents the production deployment and smoke verification for PR25.

## Scope reminder

PR25 adds deterministic comparable selection policy v2.

PR25 is the selection/eligibility layer before PR24 comparable quality scoring.

Correct flow:

```text
candidate market evidence
-> deterministic selection policy v2
-> selected/rejected evidence with stable reasons
-> PR24 comparable quality scoring v0
-> comp-derived estimate / facts / confidence cap
```

PR25 does not implement PR26.

Explicitly out of scope:

- no adjusted comparable model;
- no adjusted rent;
- no adjusted price;
- no adjusted median;
- no area/condition/floor/freshness adjustment values;
- no `comp_adjustment_flags`;
- no DCF;
- no scenario engine;
- no financing/tax layer;
- no semantic fuzzy matching;
- no embeddings matching;
- no geocoding/location taxonomy;
- no city-wide median;
- no progressive widening;
- no city/profile/all-listings fallback;
- no LLM/agent/external calls;
- no evidence row mutation;
- no admin write workflow.

Selection state is per-analysis-run output only and is represented in compact facts/fingerprint, not as persisted selected/rejected state on evidence rows.

## Merged revision

Production was updated from:

```text
b0bebe943648252aa1b281c9b60703752cce6dce
```

to:

```text
4c4d4ebde44d6ed11715c5a9d7842d7f40b235e9
```

Production `git log --oneline -5` after pull:

```text
4c4d4eb (HEAD -> main, origin/main, origin/HEAD) Add deterministic comparable selection policy v2 (#216)
cb7c882 Add PR24 production smoke handoff (#215)
b0bebe9 Add deterministic comparable quality scoring (#214)
11bc2f6 Add PR23c production smoke handoff (#213)
fbbb2e1 Add read-only human review queue (#212)
```

Fast-forward changed files included:

```text
app/analysis/market_comps.py
app/analysis/provider.py
app/analysis/service.py
docs/handoff/pr24_comparable_quality_scoring_smoke_2026-06-16.md
docs/investment_profiles.md
tests/test_comparable_selection_policy.py
tests/test_listing_analysis.py
```

## Deployment commands used

```bash
cd ~/apps/avito-watcher

git status --short
git branch --show-current
git fetch origin main
git rev-parse HEAD
git rev-parse origin/main

git checkout main
git pull --ff-only origin main

git log --oneline -5
```

Then app and worker were rebuilt/restarted:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Image deploy-app Built
Image deploy-worker Built
deploy-redis-1 Healthy
deploy-postgres-1 Healthy
deploy-worker-1 Started
deploy-app-1 Started
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

## Alembic / migration status

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

Conclusion:

```text
No migration was added by PR25.
Alembic remained at 0017_admin_audit_events.
```

## Runtime log smoke

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=250 app worker \
  | grep -Ei "Traceback|ERROR|Exception|adjusted_rent|adjusted_price|adjusted_median|comp_adjustment_flags|city-wide|semantic|embedding|geocod|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros" || true
```

Observed matches:

```text
engine_error_count=0
```

Interpretation:

This was a false positive because `engine_error_count` is a normal diagnostic counter, not a runtime error.

No actual `Traceback`, exception, adjusted-comp field, geocoding/embedding/semantic fallback, authorization header, cookie, API key, technical key, or raw Apps Script macro URL was observed in logs.

Worker completed a monitor cycle:

```text
monitor cycle completed
```

Expected environment warning remained present:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This is not related to PR25.

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
analyses_total | last_analysis_id | last_created_at
730            | 730              | 2026-06-07 18:52:39.696919

market_evidence_items_total
0
```

Interpretation:

- Existing analyses remain readable.
- Production currently has no persisted `market_evidence_items` rows, so this smoke confirms deployment/runtime compatibility but does not exercise real persisted market evidence selection on accumulated evidence rows.
- This is not a PR25 blocker; it is a production data-state fact.

## Admin read key handling

Read key was loaded without printing the secret:

```bash
set +x

ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

printf 'read_key_len=%s\n' "${#ADMIN_READ_KEY}"
```

Result:

```text
read_key_len=64
```

## Admin page smoke

Commands:

```bash
curl -sS -o /tmp/pr25_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

curl -sS -o /tmp/pr25_analyses.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/listing-analyses"

curl -sS -o /tmp/pr25_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Result:

```text
200
200
200
```

Conclusion:

- `/admin/system` rendered.
- `/admin/listing-analyses` rendered.
- `/admin/review-queue` rendered.
- PR25 did not break existing admin read-only pages.

## UI safety smoke

Commands:

```bash
grep -Ei "payload_json|result_json|adjusted_rent|adjusted_price|adjusted_median|comp_adjustment_flags|city-wide|semantic fuzzy|embedding|geocoding|admin_technical_write_key|script\.google\.com/macros/s/" \
  /tmp/pr25_system.html /tmp/pr25_analyses.html /tmp/pr25_review_queue.html || true

grep -Ei "Authorization:|Cookie:|X-API-Key:" \
  /tmp/pr25_system.html /tmp/pr25_analyses.html /tmp/pr25_review_queue.html || true
```

Result:

```text
(no output)
```

Conclusion:

No unsafe raw JSON fields, adjusted comp fields, semantic/geocoding/embedding fallback indicators, technical key fields, raw Apps Script macro URLs, or header leaks were detected in rendered admin pages.

## Service status

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

Result:

```text
deploy-app-1        Up, healthy
deploy-postgres-1   Up, healthy
deploy-redis-1      Up, healthy
deploy-worker-1     Up
```

Worker log tail showed normal startup diagnostics and monitor cycle completion.

## Smoke verdict

```text
PR25 — Comparable selection policy v2 ✅
Merged ✅
Pulled to production ✅
Built app + worker ✅
Restarted app + worker ✅
Health OK ✅
No migration ✅
Alembic unchanged ✅
Old analyses readable ✅
Admin pages render ✅
No adjusted comp fields detected ✅
No hidden selection/fallback terms leaked ✅
No secret leak detected ✅
Worker running ✅
Production smoke passed ✅
```

## Known production data limitation

`market_evidence_items_total = 0` at smoke time.

This means PR25 runtime/deployment safety was validated in production, but real persisted market-evidence selection behavior will become observable only after market evidence rows exist or after a controlled test fixture/backfill workflow is introduced in a future PR.

Do not treat this as a blocker for PR25.

## Next roadmap step

The next formal roadmap step is:

```text
PR26 — Adjusted comparable model
```

PR26 must remain separate from PR25.

Expected PR26 scope:

- deterministic adjusted comparable model v0;
- explicit adjustment factors;
- adjusted rent/median if approved;
- area/condition/floor/freshness adjustments;
- compact adjustment facts;
- no LLM/agent/external decisioning.

PR26 must be planned separately and must not be conflated with PR25 selection policy v2.
