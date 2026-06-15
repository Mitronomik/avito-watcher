# PR21a — Read-only production health dashboard production smoke

Date: 2026-06-15

Status: **PASSED after PR195 hotfix**

This handoff documents the production deploy and smoke test for:

- PR21a — read-only production health dashboard (`/admin/system`)
- PR195 — hotfix for Google Apps Script deployment URL redaction in admin delivery errors

## Summary

PR21a added a read-only Admin UI health dashboard at:

```text
/admin/system
```

The page is intended to provide operator visibility using existing signals only:

- existing worker status file summary
- parser diagnostics already present in worker status payload
- bounded SQL counters
- PR20 alert delivery invariant counters
- recent failed/unknown delivery attempts
- agent task summary
- analysis summary
- data volume counters
- current DB Alembic revision from `alembic_version`

PR21a does **not** add a migration, scheduler, queue, heartbeat table, technical action, retry action, parser invocation, log reader, shell command, or PR45/SLA observability scope.

## PRs included

### PR21a

```text
PR #194 — Add read-only production health dashboard (/admin/system)
Merge commit: 80e1d57cd1bcdd73ae812d4e5f21dfbeca969d6a
```

### Hotfix PR195

During the first production smoke, `/admin/system` rendered a full Google Apps Script deployment URL from historical `AlertDeliveryAttempt.last_error` values.

That was treated as a blocker because Apps Script deployment IDs are webhook-like secrets and must not be rendered in Admin UI HTML.

PR195 fixed the shared sanitizer.

```text
PR #195 — Redact Google Apps Script deployment URLs in admin delivery errors
Merge commit: d0b9e2ac366dcb785c7d4b3f5b7d154e7a73a624
```

The real deployment ID observed during smoke is intentionally **not recorded** in this handoff.

## Production deploy

Commands executed on production host:

```bash
cd ~/apps/avito-watcher
git pull --ff-only origin main
git log -1 --oneline

docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic heads

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

for i in $(seq 1 20); do
  echo "try $i"
  curl -fsS http://127.0.0.1:8010/health && break
  sleep 2
done
```

Observed after PR195 deploy:

```text
Updating 80e1d57..d0b9e2a
Fast-forward
 app/admin.py                            |  4 ++--
 app/services/alert_delivery_attempts.py |  2 ++
 tests/test_admin_system_health.py       | 27 +++++++++++++++++++++++++++
 tests/test_admin_ui.py                  | 31 +++++++++++++++++++++++++++++++
 4 files changed, 62 insertions(+), 2 deletions(-)

d0b9e2a (HEAD -> main, origin/main, origin/HEAD) Redact Apps Script URLs in admin errors (#195)
```

Alembic state:

```text
0015_alert_delivery_attempts (head)
0015_alert_delivery_attempts (head)
```

App image rebuilt successfully and `deploy-app-1` was restarted.

Health check initially returned short startup connection resets, as seen in prior deploys immediately after `up -d app`:

```text
try 1
curl: (56) Recv failure: Connection reset by peer
try 2
curl: (56) Recv failure: Connection reset by peer
```

The app then served Admin UI requests successfully, confirming startup completed.

## Admin system dashboard smoke

Read key was loaded from `.env` locally on the production host and passed only via request header:

```bash
ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

curl -sS -o /tmp/pr21a_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Observed:

```text
200
```

The page rendered the expected read-only sections:

- `Состояние / System health`
- `Overall status`
- `Worker cycle status`
- `Parser diagnostics`
- `Search jobs`
- `Alert Delivery health`
- `Recent failed delivery attempts`
- `Agent tasks`
- `Analysis summary`
- `Data volume summary`
- `Alembic`

Observed production values during smoke included:

```text
Worker status: Fresh
status file basename: worker_status.json
cycle_ok: Cycle OK
search_jobs total: 2
active search_jobs: 2
alert_delivery_attempts total: 281
failed attempts last 24h/7d: 3/3
manual_retry attempts: 0
alerts_sent total: 3194
agent_tasks total: 2
listing_analyses total: 730
alembic revision: 0015_alert_delivery_attempts
```

The page did not include query-string API key propagation:

```bash
grep -i "api_key=" /tmp/pr21a_system.html || true
```

Observed: no matches.

## Apps Script URL redaction smoke

The original blocker was a full URL of this shape appearing in Admin UI HTML:

```text
https://script.google.com/macros/s/[redacted deployment id]/exec
```

After PR195, the smoke checked that:

- the concrete deployment ID was absent
- raw `script.google.com/macros/s/` was absent
- safe redacted marker was present

Commands used:

```bash
LEAK_ID='[redacted deployment id used only during local smoke]'

grep -F "$LEAK_ID" /tmp/pr21a_system.html && echo "LEAK: deployment id rendered" || echo "OK: deployment id hidden"

grep -F "script.google.com/macros/s/" /tmp/pr21a_system.html && echo "LEAK: raw Apps Script URL rendered" || echo "OK: raw Apps Script URL hidden"

grep -F "https://script.google.com/.../exec" /tmp/pr21a_system.html && echo "OK: redacted Apps Script marker visible" || echo "WARN: no redacted Apps Script marker visible"
```

Observed:

```text
OK: deployment id hidden
OK: raw Apps Script URL hidden
OK: redacted Apps Script marker visible
```

The redacted marker appeared in recent failed delivery attempts:

```text
https://script.google.com/.../exec
```

## Alert dashboard and detail smoke

The hotfix was also verified on `/admin/alerts` and delivery attempt detail page.

Commands:

```bash
curl -sS -o /tmp/pr21a_alerts.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/alerts"

for f in /tmp/pr21a_system.html /tmp/pr21a_alerts.html; do
  echo "checking $f"
  grep -F "$LEAK_ID" "$f" && echo "LEAK in $f" || echo "OK no deployment id in $f"
  grep -F "script.google.com/macros/s/" "$f" && echo "LEAK raw URL in $f" || echo "OK no raw URL in $f"
done
```

Observed:

```text
200
checking /tmp/pr21a_system.html
OK no deployment id in /tmp/pr21a_system.html
OK no raw URL in /tmp/pr21a_system.html
checking /tmp/pr21a_alerts.html
OK no deployment id in /tmp/pr21a_alerts.html
OK no raw URL in /tmp/pr21a_alerts.html
```

Delivery attempt detail was checked using historical failed attempt `140`:

```bash
curl -sS -o /tmp/pr21a_attempt_140.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/alerts/delivery-attempts/140"

grep -F "$LEAK_ID" /tmp/pr21a_attempt_140.html && echo "LEAK in detail" || echo "OK no deployment id in detail"
grep -F "script.google.com/macros/s/" /tmp/pr21a_attempt_140.html && echo "LEAK raw URL in detail" || echo "OK no raw URL in detail"
grep -F "https://script.google.com/.../exec" /tmp/pr21a_attempt_140.html && echo "OK redacted marker in detail" || echo "WARN no marker in detail"
```

Observed:

```text
200
OK no deployment id in detail
OK no raw URL in detail
OK redacted marker in detail
```

The detail page kept useful operational metadata visible:

```text
id: 140
channel: google_sheets
status: failed
error_type: HTTPStatusError
matching AlertSent: yes
manual retry eligibility: not eligible because matching AlertSent already exists
```

## Read-only behavior

No technical operations were enabled during this smoke.

No manual retry was executed.

No run-once was executed.

No parser, agent, market research, analysis, or delivery operation was triggered intentionally.

The data volume counters changed during the wider smoke window because the production worker continued normal operation, but there is no evidence that `/admin/system` mutated state.

Observed `/admin/system` values after hotfix deploy:

```text
listings: 1687
listing_analyses: 730
alert_delivery_attempts: 281
alerts_sent: 3194
agent_tasks: 2
human_reviews: 0
human_review_actions: 0
investment_decisions: 0
market_research_runs: 0
market_evidence_items: 0
listing_detail_snapshots: 0
listing_enrichments: 0
knowledge_notes: 0
search_jobs: 2
```

## Known production observations

The dashboard currently shows three failed historical Google Sheets delivery attempts:

```text
failed google_sheets: 3
```

The PR20 invariant counter reports:

```text
non_success_with_alert_sent: 3
```

This reflects existing historical state: those failed attempts have matching `AlertSent` rows. PR20c intentionally does not allow retry for such rows, and the detail page correctly renders them as not eligible for manual retry.

This is not a PR21a deploy blocker.

## Final verdict

```text
PR21a production smoke: PASSED
PR195 hotfix production smoke: PASSED
/admin/system deployed and usable
Apps Script deployment ID no longer rendered
Raw Apps Script URL no longer rendered
Admin read/write/technical keys were not rendered
No migration
No real retry executed
No technical operation executed
```

PR21a is closed from a production smoke perspective.
