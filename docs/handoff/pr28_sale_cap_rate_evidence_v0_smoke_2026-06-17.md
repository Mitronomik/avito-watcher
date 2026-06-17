# PR28 — Sale and cap-rate evidence read model v0 production smoke

Date: 2026-06-17  
Environment: `avito-watcher-prod`  
Repository: `Mitronomik/avito-watcher`  
Branch: `main`  
Deployed commit: `c7596b7260d37f9f4aa402d504a4ad0d20267c79`  
Commit subject: `Add deterministic sale and cap-rate evidence read model (#222)`

## 1. Purpose

This document records the production smoke for PR28.

PR28 adds a deterministic read model for sale / asking-price / explicit cap-rate evidence.

PR28 is intentionally scoped as a read model and evidence facts layer.

It is **not**:

- a certified appraisal system;
- a professional valuation report;
- an automated investment decision maker;
- a DCF model;
- a financing model;
- a tax model;
- a scenario engine;
- an external verification workflow;
- an LLM/agent/RAG workflow.

Core expected boundary:

```text
PR24 — comparable quality
PR25 — comparable selection
PR26 — adjusted rent comps
PR27 — source quality discipline
PR28 — sale / cap-rate evidence read model
```

PR28 must not directly rewrite:

- deterministic score;
- deterministic verdict;
- manual rent;
- adjusted rent values;
- evidence rows;
- source type;
- verification status.

PR28 may add facts, review reasons, fingerprints, and confidence caps for sale/cap-rate evidence.

## 2. Deploy summary

Production was updated from PR27 to PR28.

Before pull:

```text
HEAD:        48c523e830694e028ea035094661e3e4e52516c8
origin/main: c7596b7260d37f9f4aa402d504a4ad0d20267c79
```

Pull result:

```text
Updating 48c523e..c7596b7
Fast-forward
```

Files changed on production pull:

```text
app/analysis/market_comps.py                                       | 350 +++++++++++++++++++++++++++++++++++++
app/analysis/provider.py                                           |  27 +++
docs/handoff/pr27_source_quality_discipline_v0_smoke_2026-06-16.md | 502 +++++++++++++++++++++++++++++++++++++++++++++++++++++
docs/investment_profiles.md                                        |   8 +
tests/test_sale_cap_rate_evidence.py                               | 194 +++++++++++++++++++++
```

Top of production log after pull:

```text
c7596b7 (HEAD -> main, origin/main, origin/HEAD) Add deterministic sale and cap-rate evidence read model (#222)
72eeb51 Add PR27 production smoke handoff (#221)
48c523e Add deterministic source quality discipline v0 (#220)
b6ad8e5 Add PR26 production smoke handoff (#219)
0729cd7 Add PR25 production smoke handoff (#217)
81e1f65 Add deterministic adjusted comparable model v0 (#218)
4c4d4eb Add deterministic comparable selection policy v2 (#216)
cb7c882 Add PR24 production smoke handoff (#215)
```

## 3. Build and restart

Commands run:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker
```

Result:

```text
Image deploy-worker Built
Image deploy-app    Built
Container deploy-redis-1    Healthy
Container deploy-postgres-1 Healthy
Container deploy-app-1      Started
Container deploy-worker-1   Started
```

Assessment:

```text
Docker compose config OK
app image build OK
worker image build OK
app restarted OK
worker restarted OK
postgres healthy
redis healthy
```

## 4. Health check

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

Assessment:

```text
Health OK
```

## 5. Alembic / migration status

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

Assessment:

```text
No unexpected migration
Alembic current is at head
Alembic heads reports one head
```

PR28 did not require a migration for production.

## 6. Runtime log safety grep

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=450 app worker \
  | grep -Ei "Traceback|ERROR|Exception|sale_evidence|cap_rate|cap_rate_pct|gross_yield|sale_price|price_per_m2|DCF|scenario|financing|tax|valuation|appraisal|professional valuation|semantic|embedding|geocod|Authorization|Cookie|X-API-Key|admin_technical_write_key|script\.google\.com/macros" || true
```

Matches:

```text
worker-1 | 2026-06-17 02:14:55,867 INFO app.parsers.avito_parser avito_parser.end_cycle stats={..., 'engine_error_count': 0, ...}
worker-1 | 2026-06-17 02:14:55,868 INFO app.services.monitor_service monitor_service.cycle_summary searches_processed=0 ... engine_errors=0 ...
```

Assessment:

```text
No Traceback
No ERROR
No Exception
No sale_evidence leakage in runtime logs
No cap_rate / gross_yield leakage in runtime logs
No DCF / scenario / financing / tax leakage
No valuation / appraisal / professional valuation leakage
No semantic / embedding / geocoding leakage
No Authorization / Cookie / X-API-Key leakage
No admin technical key leakage
No Apps Script URL leakage
```

`engine_error_count=0` and `engine_errors=0` are normal runtime stats fields, not errors.

## 7. DB baseline

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
 analyses_total | last_analysis_id |      last_created_at       
----------------+------------------+----------------------------
            730 |              730 | 2026-06-07 18:52:39.696919
(1 row)

 market_evidence_items_total 
-----------------------------
                           0
(1 row)
```

Assessment:

```text
listing_analyses count unchanged from previous smoke baseline
last_analysis_id unchanged
market_evidence_items_total = 0
```

Important limitation:

```text
Production smoke did not exercise real PR28 sale/cap-rate evidence facts over persisted market_evidence_items because production currently has 0 market_evidence_items.
```

The smoke confirms:

- production deploy compatibility;
- app startup;
- worker startup;
- admin rendering;
- migration safety;
- log/UI safety;
- backward compatibility with existing analyses.

It does not confirm real sale/cap-rate evidence calculations over production evidence rows.

## 8. Admin read key check

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

Assessment:

```text
Admin read key present
Shell tracing disabled before reading key
No key value printed
```

## 9. Admin page smoke

Commands:

```bash
curl -sS -o /tmp/pr28_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

curl -sS -o /tmp/pr28_analyses.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/listing-analyses"

curl -sS -o /tmp/pr28_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Result:

```text
200
200
200
```

Assessment:

```text
/admin/system renders OK
/admin/listing-analyses renders OK
/admin/review-queue renders OK
```

## 10. UI safety grep

Command:

```bash
grep -Ei "payload_json|result_json|raw evidence_json|sale_evidence_model_version|cap_rate_evidence_model_version|admin_technical_write_key|Authorization:|Cookie:|X-API-Key:|script\.google\.com/macros/s/" \
  /tmp/pr28_system.html /tmp/pr28_analyses.html /tmp/pr28_review_queue.html || true
```

Result:

```text
<no output>
```

Assessment:

```text
No raw payload_json leak detected
No raw result_json leak detected
No raw evidence_json leak detected
No sale/cap-rate model version exposed on generic admin pages
No admin technical key leak detected
No Authorization header leak detected
No Cookie leak detected
No X-API-Key leak detected
No Apps Script URL leak detected
```

## 11. Container status

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml ps
```

Result:

```text
NAME                IMAGE           COMMAND                  SERVICE    STATUS                        PORTS
deploy-app-1        deploy-app      "uvicorn app.main:ap…"   app        Up About a minute (healthy)   127.0.0.1:8010->8000/tcp
deploy-postgres-1   postgres:16     "docker-entrypoint.s…"   postgres   Up 46 hours (healthy)         5432/tcp
deploy-redis-1      redis:7         "docker-entrypoint.s…"   redis      Up 2 weeks (healthy)          6379/tcp
deploy-worker-1     deploy-worker   "python -m app.worke…"   worker     Up About a minute
```

Assessment:

```text
app running and healthy
postgres running and healthy
redis running and healthy
worker running
```

## 12. Worker logs

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=220 worker \
  | tail -220
```

Observed:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
monitor worker runtime diagnostics: {... 'proxy_urls_set': False, ...}
avito_parser.end_cycle stats={..., 'engine_error_count': 0, ...}
monitor_service.cycle_summary searches_processed=0 ... engine_errors=0 ...
monitor cycle completed
```

Assessment:

```text
Worker starts
Worker completes monitor cycles
No Traceback observed
No engine errors observed
No parser crash observed
No sale/cap-rate runtime leakage observed
```

`PROXY_URLS not set` is an existing environment warning and is not related to PR28.

## 13. Functional limitation of this production smoke

Production currently contains no persisted `market_evidence_items`:

```text
market_evidence_items_total = 0
```

Therefore PR28's sale/cap-rate evidence read model was not exercised on real persisted production evidence.

This is acceptable for this smoke because PR28 has focused tests and production smoke validates:

- deploy safety;
- runtime import/startup safety;
- old data compatibility;
- no unexpected migrations;
- admin rendering compatibility;
- no forbidden log/UI leakage;
- worker cycle stability.

Future PRs or a manual seeded smoke should exercise PR28 over actual stored market evidence when production evidence exists.

## 14. Security and safety notes

Smoke checked that generic admin pages do not leak:

- raw payload JSON;
- raw result JSON;
- raw evidence JSON;
- admin technical write key;
- authorization headers;
- cookies;
- API key header;
- Google Apps Script webhook URL.

Smoke checked that logs do not expose:

- sale/cap-rate evidence facts;
- DCF/scenario/financing/tax terms;
- professional valuation/appraisal terms;
- semantic/embedding/geocoding terms;
- credentials or notifier URLs.

PR28 remains within deterministic read-model boundaries.

## 15. Server maintenance note

On SSH login the server reported:

```text
System restart required
19 zombie processes
13 updates can be applied immediately
4 standard security updates
```

This is not a blocker for PR28 smoke.

Recommendation:

```text
Schedule a maintenance reboot after the handoff PR is merged and the current milestone is fully documented.
```

## 16. Final verdict

```text
PR28 — Sale and cap-rate evidence read model v0 ✅
Merged ✅
Pulled to production ✅
Built app + worker ✅
Restarted app + worker ✅
Health OK ✅
No unexpected migration ✅
Alembic unchanged ✅
Admin pages OK ✅
Worker running ✅
UI safety grep clean ✅
Log safety grep clean ✅
Production smoke passed ✅
```

## 17. Next step

Create docs-only handoff PR:

```text
docs/handoff/pr28_sale_cap_rate_evidence_v0_smoke_2026-06-17.md
```

After that PR is merged, proceed to the next roadmap block:

```text
PR29 — Admin API v1 foundation
```

Rationale:

```text
After PR28 the evidence core is strong, but the frontend/API layer is not yet decision-ready.
PR29 should create a stable JSON API foundation for future operator UI and decision-card endpoints.
```
