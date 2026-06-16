# PR26 — Adjusted comparable model v0 production smoke

Date: 2026-06-16
Environment: `avito-watcher-prod`
Repository: `Mitronomik/avito-watcher`
PR: #218 — `Add deterministic adjusted comparable model v0`

## Status

```text
PR26 — Adjusted comparable model v0 ✅
Merged ✅
Pulled to production ✅
Built app + worker ✅
Restarted app + worker ✅
Health OK ✅
No migration ✅
Alembic unchanged ✅
Admin pages OK ✅
Worker running ✅
UI safety grep clean ✅
Production smoke passed ✅
```

## Production revision

Production was updated from PR25 to PR26.

```text
Before pull:
4c4d4ebde44d6ed11715c5a9d7842d7f40b235e9

After pull:
81e1f65792bf836375fcfeac2e3df0eda69c3728
```

Latest production commit:

```text
81e1f65 Add deterministic adjusted comparable model v0 (#218)
```

Recent production log:

```text
81e1f65 Add deterministic adjusted comparable model v0 (#218)
4c4d4eb Add deterministic comparable selection policy v2 (#216)
cb7c882 Add PR24 production smoke handoff (#215)
b0bebe9 Add deterministic comparable quality scoring (#214)
11bc2f6 Add PR23c production smoke handoff (#213)
fbbb2e1 Add read-only human review queue (#212)
```

Changed files pulled to production:

```text
app/analysis/market_comps.py
app/analysis/provider.py
docs/market_evidence.md
docs/roadmap/avito_watcher_roadmap_v2_after_pr17.md
tests/test_adjusted_comparables.py
tests/test_investment_market_comps.py
```

## Scope reminder

PR26 adds deterministic adjusted comparable model v0.

It is downstream of:

```text
PR25 comparable selection policy v2
PR24 comparable quality scoring v0
```

PR26 does not replace PR24 or PR25.

The intended flow is:

```text
market evidence candidates
-> PR25 selected comps
-> PR24 accepted/quality-approved comps
-> PR26 adjusted comparable model v0
-> adjusted rent_per_m2 median
-> optional comp-derived market rent when strict gates pass
```

Important boundaries:

```text
No migration.
No evidence row mutation.
No backfill.
No LLM/agent/external call.
No parser change.
No alert delivery change.
No admin write action.
No source verification PR27.
No sale/cap-rate evidence PR28.
No scenario/DCF/financing model.
No professional appraisal / valuation claim.
Manual rent remains primary.
```

## Build and restart

Commands run:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Image deploy-worker Built ✅
Image deploy-app Built ✅
deploy-postgres-1 Healthy ✅
deploy-redis-1 Healthy ✅
deploy-worker-1 Started ✅
deploy-app-1 Started ✅
```

## Health check

Command:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```text
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok"}
```

## Alembic status

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

Interpretation:

```text
No new migration was added by PR26.
Production Alembic state remained unchanged.
```

## Runtime log smoke

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=300 app worker \
  | grep -Ei "Traceback|ERROR|Exception|adjusted_rent|adjusted_price|adjusted_median|comp_adjustment_flags|valuation|appraisal|DCF|scenario|financing|semantic|embedding|geocod|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros" || true
```

Result contained only normal metric-field names inside structured runtime stats:

```text
engine_error_count=0
```

No runtime errors were detected.

Interpretation:

```text
Traceback: not detected ✅
ERROR: not detected ✅
Exception: not detected ✅
secret/header leak: not detected ✅
valuation/appraisal/DCF/scenario/financing terms: not detected ✅
semantic/embedding/geocoding terms: not detected ✅
adjusted comp fields in logs: not detected ✅
```

Note: `engine_error_count=0` is a normal parser diagnostics counter, not an error.

## Database baseline

Command:

```sql
select
  count(*) as analyses_total,
  max(id) as last_analysis_id,
  max(created_at) as last_created_at
from listing_analyses;

select
  count(*) as market_evidence_items_total
from market_evidence_items;
```

Result:

```text
analyses_total: 730
last_analysis_id: 730
last_created_at: 2026-06-07 18:52:39.696919

market_evidence_items_total: 0
```

Interpretation:

```text
Old analyses remain readable ✅
No production DB compatibility issue detected ✅
```

Important limitation:

```text
market_evidence_items_total = 0
```

This means the production smoke confirmed deployment, compatibility, health, admin rendering, and safety properties, but it did not exercise real adjusted comparable calculations on production market evidence rows because there are currently no `market_evidence_items` rows in production.

This is not a blocker for deploy because PR26 has no migration and does not backfill old analyses.

## Admin read key smoke

Command:

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

No key value was printed.

## Admin pages smoke

Commands:

```bash
curl -sS -o /tmp/pr26_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

curl -sS -o /tmp/pr26_analyses.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/listing-analyses"

curl -sS -o /tmp/pr26_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Result:

```text
/admin/system: 200
/admin/listing-analyses: 200
/admin/review-queue: 200
```

Interpretation:

```text
Admin shell renders ✅
Listing analyses page renders ✅
Human review queue renders ✅
No admin route regression detected ✅
```

## UI safety grep

Commands:

```bash
grep -Ei "payload_json|result_json|comp_adjustment_flags|city-wide|semantic fuzzy|embedding|geocoding|admin_technical_write_key|script\.google\.com/macros/s/" \
  /tmp/pr26_system.html /tmp/pr26_analyses.html /tmp/pr26_review_queue.html || true

grep -Ei "Authorization:|Cookie:|X-API-Key:" \
  /tmp/pr26_system.html /tmp/pr26_analyses.html /tmp/pr26_review_queue.html || true
```

Result:

```text
No matches.
```

Interpretation:

```text
No raw payload_json/result_json in checked admin pages ✅
No comp_adjustment_flags legacy leak ✅
No forbidden city-wide / semantic / embedding / geocoding terms ✅
No admin technical write key leak ✅
No raw App Script URL leak ✅
No Authorization/Cookie/X-API-Key text leak in rendered pages ✅
```

Note: PR26 may legitimately produce `adjusted_comparables` facts for future analyses. The smoke intentionally did not treat `adjusted_rent` or `adjusted_median` as forbidden UI terms because those are now expected PR26 facts when rendered safely and compactly.

## Worker status

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps

docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=160 worker \
  | tail -160
```

Result:

```text
deploy-app-1        Up About a minute (healthy)
deploy-postgres-1   Up 36 hours (healthy)
deploy-redis-1      Up 2 weeks (healthy)
deploy-worker-1     Up About a minute
```

Worker log tail:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
monitor worker runtime diagnostics: {...}
avito_parser.end_cycle stats={...}
monitor_service.cycle_summary searches_processed=0 ...
monitor cycle completed
```

Interpretation:

```text
Worker starts ✅
No restart loop observed ✅
Monitor cycle completes ✅
```

Known existing environment warning:

```text
PROXY_URLS not set — running without proxies
```

This warning predates PR26 and is not caused by adjusted comparable model changes.

## PR26 functional safety notes

PR26 production smoke did not create new analyses and did not backfill old analyses.

Because `market_evidence_items_total = 0`, there were no production comps available to produce new `adjusted_comparables` facts during smoke.

The code-level PR review before merge confirmed the critical PR26 boundaries:

```text
monthly rent / rent_per_m2 basis only;
no unknown period conversion;
adjusted_rent_per_m2 first;
adjusted total rent only if target_area exists;
comp-to-target equivalent direction;
additive deltas with per-dimension and total caps;
freshness affects confidence/review, not rent value;
asking discount only for explicit asking source_type;
unknown source_type does not change rent value;
condition/first-line/floor/access only from structured fields;
manual rent remains primary;
confidence cap does not rewrite broad investment_score/verdict;
model/config versions and constants are fingerprinted;
facts are compact and bounded;
no migration by default and none added.
```

## Final production smoke verdict

```text
PR26 — Adjusted comparable model v0 ✅
Merged ✅
Deployed ✅
No migration ✅
Alembic unchanged ✅
App health OK ✅
Worker running ✅
Old analyses readable ✅
Admin pages render ✅
No raw payload/secret leak detected ✅
No valuation/appraisal/DCF/scenario leakage in logs ✅
Production smoke passed ✅
```

## Follow-up

Next roadmap step should stay separate from PR26.

Candidate next step:

```text
PR27 — Source quality / verification discipline
```

PR27 should not be treated as already implemented by PR26. PR26 only adds deterministic adjustment of selected and quality-approved comparable rent evidence.
