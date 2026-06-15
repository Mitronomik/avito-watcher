# PR19d — Admin technical operations hardening production smoke

Date: 2026-06-15

Status: **closed / passed**

## Scope

This handoff records the safe production deploy and smoke verification for:

```text
PR19d — Technical operations hardening
PR #186 — Harden admin technical operations
```

PR19d hardened dangerous Admin UI technical operations:

- create search
- edit search
- activate search
- deactivate search
- reset baseline
- run once

The intended safety model after PR19d:

```text
Read-only/operator Admin UI remains available with ADMIN_UI_READ_KEY.
Human review writes remain separate from technical operations.
Dangerous technical operations require ADMIN_UI_TECHNICAL_OPS_ENABLED=true.
Dangerous technical POSTs require a dedicated ADMIN_UI_TECHNICAL_WRITE_KEY.
Dangerous technical POSTs require explicit confirm_action.
Query-string keys remain disabled by default.
Run-once output is redacted before rendering.
```

## Production revision

Production repository was updated from the PR19c production head to PR19d:

```text
Before: ed0a6b7 Add read-only admin evidence agents outcome pages (#184)
After:  b44346f Harden admin technical operations (#186)
```

Production `main` after deploy:

```text
b44346f (HEAD -> main, origin/main, origin/HEAD) Harden admin technical operations (#186)
```

The fast-forward pull also included the previously merged PR19c smoke handoff docs file:

```text
docs/handoff/pr19c_admin_readonly_pages_smoke_2026-06-14.md
```

## Deploy commands executed

The production deploy followed the standard safe deploy flow:

```bash
cd ~/apps/avito-watcher

git status
git switch main
git pull --ff-only origin main
git log -1 --oneline

docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d postgres redis

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic heads

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml ps

curl -i http://127.0.0.1:8010/health
```

## Deploy result

Alembic head/current:

```text
0014_human_review_tracking (head)
```

No new migration was expected or added by PR19d.

Container state after deploy:

```text
deploy-app-1        Up / healthy
deploy-postgres-1   Up / healthy
deploy-redis-1      Up / healthy
deploy-worker-1     Up
```

Initial health check:

```http
HTTP/1.1 200 OK

{"status":"ok"}
```

## Initial Admin UI env state

The initial smoke started with technical operations disabled:

```text
ADMIN_UI_ENABLED=true
ADMIN_UI_ALLOW_QUERY_API_KEY=false
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
ADMIN_UI_READ_KEY is set
ADMIN_UI_WRITE_KEY is set
ADMIN_UI_TECHNICAL_WRITE_KEY is NOT set
```

This matches the desired default posture:

```text
Read-only Admin UI allowed.
Query-string keys disabled.
Technical operations disabled.
No technical write key required while technical operations are disabled.
```

## Disabled-mode smoke

With `ADMIN_UI_TECHNICAL_OPS_ENABLED=false`, the following checks were executed with `ADMIN_UI_READ_KEY`:

```bash
ADMIN_READ_KEY="$(grep '^ADMIN_UI_READ_KEY=' .env | cut -d= -f2-)"

curl -sS -o /tmp/pr19d_admin.html -w "admin root: %{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  http://127.0.0.1:8010/admin

curl -sS -o /tmp/pr19d_technical_disabled.html -w "technical page: %{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  http://127.0.0.1:8010/admin/technical

curl -sS -o /tmp/pr19d_new_search_disabled.html -w "new search disabled: %{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  http://127.0.0.1:8010/admin/searches/new

curl -sS -o /tmp/pr19d_post_disabled.txt -w "post disabled: %{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  http://127.0.0.1:8010/admin/searches/1/deactivate
```

Observed result:

```text
admin root: 200
technical page: 200
new search disabled: 403
post disabled: 403
```

Interpretation:

```text
Read-only Admin UI remained reachable.
The technical page was reachable as a status/explanation page.
Dangerous technical creation/editing routes were blocked.
Dangerous technical POST routes were blocked.
```

## DB snapshot before controlled technical smoke

Counts before the controlled technical mutation smoke:

```text
        table_name        | row_count
--------------------------+-----------
 agent_tasks              |         2
 alerts_sent              |      2910
 human_review_actions     |         0
 human_reviews            |         0
 investment_decisions     |         0
 knowledge_notes          |         0
 listing_analyses         |       730
 listing_detail_snapshots |         0
 listing_enrichments      |         0
 listings                 |      1545
 market_evidence_items    |         0
 market_research_runs     |         0
 search_jobs              |         2
```

## Smoke search selected

The smoke used the first existing search job:

```text
SMOKE_SEARCH_ID=2
```

Initial state:

```text
 id |                 name                 | is_active | baseline_initialized |  baseline_initialized_at   |        next_run_at
----+--------------------------------------+-----------+----------------------+----------------------------+----------------------------
  2 | spb_commercial_rent_40_150m2_to_200k | t         | t                    | 2026-05-30 15:02:21.319616 | 2026-06-15 04:03:15.810018
```

## Temporary technical ops enablement

Technical operations were temporarily enabled for the controlled smoke:

```text
ADMIN_UI_TECHNICAL_OPS_ENABLED=true
ADMIN_UI_TECHNICAL_WRITE_KEY configured
```

The technical write key value was not printed or recorded in this handoff.

After restarting `app`, an immediate `/health` request returned:

```text
curl: (56) Recv failure: Connection reset by peer
```

This was interpreted as a transient request during the app restart window. Subsequent Admin UI POST requests succeeded, and the final health check later returned `200 OK`.

## Auth separation and confirmation smoke

The following checks were executed against search `id=2`:

```bash
curl -sS -o /tmp/pr19d_read_key_denied.txt -w "read key deactivate: %{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  -d "confirm_action=deactivate_search" \
  "http://127.0.0.1:8010/admin/searches/$SMOKE_SEARCH_ID/deactivate"

curl -sS -o /tmp/pr19d_write_key_denied.txt -w "write key deactivate: %{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_WRITE_KEY" \
  -d "confirm_action=deactivate_search" \
  "http://127.0.0.1:8010/admin/searches/$SMOKE_SEARCH_ID/deactivate"

curl -sS -o /tmp/pr19d_missing_confirm.txt -w "tech key missing confirm: %{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_TECH_KEY" \
  "http://127.0.0.1:8010/admin/searches/$SMOKE_SEARCH_ID/deactivate"

curl -sS -o /tmp/pr19d_wrong_confirm.txt -w "tech key wrong confirm: %{http_code}\n" \
  -X POST \
  -H "X-API-Key: $ADMIN_TECH_KEY" \
  -d "confirm_action=wrong_action" \
  "http://127.0.0.1:8010/admin/searches/$SMOKE_SEARCH_ID/deactivate"
```

Observed result:

```text
read key deactivate: 403
write key deactivate: 403
tech key missing confirm: 400
tech key wrong confirm: 400
```

State after rejected operations:

```text
 id |                 name                 | is_active | baseline_initialized |  baseline_initialized_at   |        next_run_at
----+--------------------------------------+-----------+----------------------+----------------------------+----------------------------
  2 | spb_commercial_rent_40_150m2_to_200k | t         | t                    | 2026-05-30 15:02:21.319616 | 2026-06-15 04:03:15.810018
```

Interpretation:

```text
Read key cannot mutate technical state.
Write key cannot mutate technical state.
Technical key alone is not enough without confirm_action.
Wrong confirm_action is rejected.
Rejected operations did not mutate the selected search.
```

## Controlled mutation and restore

Initial active state:

```text
ORIGINAL_ACTIVE=t
```

A controlled deactivate was executed with technical key and correct confirmation:

```text
tech deactivate: 303
```

State after deactivate:

```text
 id | is_active
----+-----------
  2 | f
```

The original active state was restored:

```text
restore activate: 303
```

Final state after restore:

```text
 id | is_active
----+-----------
  2 | t
```

Interpretation:

```text
Technical key + correct confirm_action can perform the intended technical mutation.
The mutation was limited to the selected search active flag.
The selected search was restored to its original active state.
```

## DB snapshot after controlled technical smoke

Counts after the controlled technical smoke:

```text
        table_name        | row_count
--------------------------+-----------
 agent_tasks              |         2
 alerts_sent              |      2910
 human_review_actions     |         0
 human_reviews            |         0
 investment_decisions     |         0
 knowledge_notes          |         0
 listing_analyses         |       730
 listing_detail_snapshots |         0
 listing_enrichments      |         0
 listings                 |      1545
 market_evidence_items    |         0
 market_research_runs     |         0
 search_jobs              |         2
```

`diff -u /tmp/pr19d_counts_before.txt /tmp/pr19d_counts_after.txt` produced no diff.

Interpretation:

```text
No new alerts were sent during the controlled technical smoke window.
No listings were created during the controlled technical smoke window.
No listing analyses were created.
No human review rows were created.
No market evidence/research rows were created.
No agent tasks were created.
Search job count remained unchanged.
```

## Technical ops disabled again

After the controlled smoke, technical operations were disabled again:

```text
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
```

An immediate health check during app restart again hit a transient restart window:

```text
curl: (56) Recv failure: Connection reset by peer
```

A final health check was then executed after the restart window.

## Final health and final env state

Final Admin UI env state:

```text
ADMIN_UI_ENABLED=true
ADMIN_UI_ALLOW_QUERY_API_KEY=false
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
ADMIN_UI_TECHNICAL_WRITE_KEY is set
```

Final health check:

```http
HTTP/1.1 200 OK

{"status":"ok"}
```

Final container state:

```text
deploy-app-1        Up / healthy
deploy-postgres-1   Up / healthy
deploy-redis-1      Up / healthy
deploy-worker-1     Up
```

Final selected search state:

```text
 id |                 name                 | is_active | baseline_initialized |  baseline_initialized_at   |        next_run_at
----+--------------------------------------+-----------+----------------------+----------------------------+----------------------------
  2 | spb_commercial_rent_40_150m2_to_200k | t         | t                    | 2026-05-30 15:02:21.319616 | 2026-06-15 04:13:45.098599
```

Notes:

```text
is_active was restored to the original value.
baseline_initialized remained true.
baseline_initialized_at remained unchanged.
next_run_at changed due to the normal worker monitor cycle, not because of Admin UI run-once.
```

## Logs reviewed

App logs were checked for:

```text
traceback
exception
error
warning
secret
token
api_key
password
```

No app-side error or secret leakage was observed in the filtered output.

Worker logs were checked for:

```text
traceback
exception
error
agent
research
llm
delivery
run_once
run-once
```

Observed worker activity included normal runtime diagnostics and normal monitor cycles.

Important note:

```text
A normal worker monitor cycle processed one search during the smoke window:
searches_processed=1
engine_used=camoufox
```

This was not Admin UI `run-once`. It happened through the normal worker loop after deploy/restart.

Subsequent worker cycles showed:

```text
searches_processed=0
blocks=0
engine_errors=0
browser_driver_crashes=0
proxy_failures=0
```

## Browser visual smoke

After production smoke, the Admin UI was also opened visually through an SSH tunnel.

Tunnel command:

```bash
ssh -N -L 8010:127.0.0.1:8010 root@159.194.226.50
```

Browser URL:

```text
http://127.0.0.1:8010/admin
```

Browser auth method:

```text
Request header: X-API-Key: <ADMIN_UI_READ_KEY>
```

The key was passed through a browser header extension such as ModHeader/Requestly. Query-string API keys were not enabled and were not used.

Visual smoke result:

```text
SSH tunnel worked.
Browser reached production Admin UI through localhost.
X-API-Key was passed correctly.
Read-only access was available.
Technical operations remained disabled.
```

## What was intentionally not tested in production

The following actions were intentionally not executed in production smoke:

```text
run-once
reset-baseline
create search
edit search
```

Reason:

```text
run-once may parse Avito and may send alerts depending on existing monitor/delivery rules.
reset-baseline changes baseline behavior and should only be used on a dedicated smoke search.
create/edit search can change future monitoring behavior.
```

The production smoke was intentionally limited to:

```text
disabled-mode route checks
auth separation checks
confirmation checks
one controlled deactivate/activate round trip on an existing search
final state restore
DB count verification
log review
browser visual read-only smoke
```

## Final acceptance checklist

```text
Production main updated to PR19d merge commit ✅
Docker compose config valid ✅
Postgres/Redis healthy ✅
App rebuilt and healthy ✅
Worker rebuilt and running ✅
Alembic current == head ✅
No migration expected ✅
Admin read-only access works ✅
Technical ops disabled by default ✅
Dangerous GET route blocked while disabled ✅
Dangerous POST route blocked while disabled ✅
Read key cannot perform technical mutation ✅
Write key cannot perform technical mutation ✅
Technical key without confirmation cannot mutate ✅
Technical key with wrong confirmation cannot mutate ✅
Technical key with correct confirmation can perform intended mutation ✅
Selected search restored to original is_active state ✅
DB row counts unchanged across controlled smoke ✅
Run-once not executed ✅
No agent/research/evidence/human-review side effects ✅
Technical ops disabled again after smoke ✅
Final health check OK ✅
Visual browser read-only smoke OK ✅
```

## Final status

```text
PR19d — Technical operations hardening
Production deploy: done ✅
Production smoke: closed ✅
Technical ops disabled after smoke ✅
Health: OK ✅
Search state restored ✅
DB side effects: none beyond intended temporary is_active toggle and restore ✅
Run-once not executed ✅
No agent/research side effects ✅
Browser visual read-only access verified ✅
```
