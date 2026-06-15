# PR24 — Comparable quality scoring production smoke

Date: 2026-06-16
Repository: `Mitronomik/avito-watcher`
Environment: `avito-watcher-prod`

## Status

```text
PR24 — Comparable quality scoring ✅
Merged ✅
Deployed ✅
No migration ✅
Alembic unchanged ✅
App health OK ✅
Worker running ✅
Admin pages render ✅
Old analyses readable ✅
No adjusted-comp fields detected as a real issue ✅
No raw secret leak detected ✅
Logs operationally clean ✅
Production smoke passed ✅
```

## Scope reminder

PR24 adds deterministic comparable quality scoring for already selected market evidence.

It is part of the main roadmap and starts the comparable-quality / market-data-discipline layer.

PR24 is not:

- PR25 selection policy v2;
- PR26 adjusted comparable model;
- adjusted rent / adjusted price / adjusted median;
- scoring formula rewrite;
- human outcome calibration;
- agent strategy loop;
- external research integration;
- admin workflow change.

Comparable quality can cap market-evidence confidence and add review/facts reasons, but it must not broadly rewrite `investment_score`, `verdict`, or broad score-cap behavior.

## Merged revision

Production was updated from:

```text
fbbb2e1 Add read-only human review queue (#212)
```

to:

```text
b0bebe9 Add deterministic comparable quality scoring (#214)
```

The pull also included the previously merged PR23c handoff:

```text
11bc2f6 Add PR23c production smoke handoff (#213)
```

Recent production history after pull:

```text
b0bebe9 (HEAD -> main, origin/main, origin/HEAD) Add deterministic comparable quality scoring (#214)
11bc2f6 Add PR23c production smoke handoff (#213)
fbbb2e1 Add read-only human review queue (#212)
cc5f974 Add PR23b production smoke handoff
f7cd120 Harden admin key-based access control (#210)
```

Changed files pulled to production:

```text
app/analysis/market_comps.py
app/analysis/provider.py
docs/handoff/pr23c_human_review_queue_smoke_2026-06-15.md
docs/investment_profiles.md
tests/test_comparable_quality.py
tests/test_investment_market_comps.py
tests/test_investment_market_comps_hash.py
```

No Alembic migration file was added.

## Build and restart

Commands run:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Image deploy-app Built ✅
Image deploy-worker Built ✅
Container deploy-redis-1 Healthy ✅
Container deploy-postgres-1 Healthy ✅
Container deploy-worker-1 Started ✅
Container deploy-app-1 Started ✅
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

Conclusion:

```text
No migration ✅
Single Alembic head ✅
DB revision unchanged ✅
```

## Startup logs

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=200 app worker \
  | grep -Ei "Traceback|ERROR|Exception|adjusted_rent|adjusted_price|adjusted_median|comp_adjustment_flags|datetime.utcnow|datetime.now|date.today|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros" || true
```

Result:

Only expected operational worker lines were present:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
monitor worker runtime diagnostics: {...}
avito_parser.end_cycle stats={...}
monitor_service.cycle_summary ...
monitor cycle completed
```

No traceback, exception, adjusted-comp field, technical key, header secret, or raw Apps Script macros URL was found.

`PROXY_URLS not set` is an existing environment warning and is not related to PR24.

## Existing analyses compatibility

Command:

```sql
select
  count(*) as analyses_total,
  max(id) as last_analysis_id,
  max(created_at) as last_created_at
from listing_analyses;
```

Result:

```text
analyses_total: 730
last_analysis_id: 730
last_created_at: 2026-06-07 18:52:39.696919
```

Conclusion:

```text
Old listing_analyses readable ✅
Old facts remain compatible ✅
No backfill required ✅
```

## Admin read key loading

The read key was loaded from `.env` without printing the secret value.

Result:

```text
read_key_len=64
```

## Admin pages smoke

Commands:

```bash
curl -sS -o /tmp/pr24_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

curl -sS -o /tmp/pr24_analyses.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/listing-analyses"

curl -sS -o /tmp/pr24_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Result:

```text
/admin/system: 200 ✅
/admin/listing-analyses: 200 ✅
/admin/review-queue: 200 ✅
```

## UI safety smoke

Command:

```bash
grep -Ei "payload_json|result_json|adjusted_rent|adjusted_price|adjusted_median|comp_adjustment_flags|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros" \
  /tmp/pr24_system.html /tmp/pr24_analyses.html /tmp/pr24_review_queue.html || true
```

Observed output:

The grep returned a large chunk from `/tmp/pr24_system.html`, but this was reviewed as a false positive.

Reasons:

- `Cookie` matched safe explanatory UI text: metadata, request bodies, headers, cookies, API keys are not shown;
- `error`-like terms appear in normal operational counters such as `engine_error_count` / `last_error`;
- Apps Script URLs are still redacted as `https://script.google.com/.../exec`, not raw `/macros/s/...` URLs;
- no `payload_json`, `result_json`, `adjusted_rent`, `adjusted_price`, `adjusted_median`, `comp_adjustment_flags`, technical write key, or raw secret value was identified as a real leak.

Recommended tighter future smoke:

```bash
grep -Ei "payload_json|result_json|adjusted_rent|adjusted_price|adjusted_median|comp_adjustment_flags|admin_technical_write_key|script\.google\.com/macros/s/" \
  /tmp/pr24_system.html /tmp/pr24_analyses.html /tmp/pr24_review_queue.html || true

grep -Ei "Authorization:|Cookie:|X-API-Key:" \
  /tmp/pr24_system.html /tmp/pr24_analyses.html /tmp/pr24_review_queue.html || true
```

## Runtime services

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

Result:

```text
deploy-app-1        Up, healthy ✅
deploy-worker-1     Up ✅
deploy-postgres-1   Up, healthy ✅
deploy-redis-1      Up, healthy ✅
```

Worker logs ended with:

```text
monitor cycle completed
```

## Final conclusion

PR24 is deployed and production-smoked successfully.

The comparable-quality layer is now present in code and docs, while production operational checks show:

- no migration;
- unchanged Alembic head;
- healthy app;
- running worker;
- admin pages render;
- existing analyses remain readable;
- no real raw JSON / adjusted-comp / secret leak observed;
- logs are operationally clean.

## Next roadmap step

After PR24, the next formal roadmap item is PR25:

```text
PR25 — Comparable selection policy v2
```

PR25 should remain separate from PR24 and should focus on selection/reuse policy rather than adjusted comp math. Adjusted comparable model remains PR26.
