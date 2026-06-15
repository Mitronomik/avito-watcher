# PR20b — Read-only alert delivery dashboard production smoke

Date: 2026-06-15
Environment: production (`avito-watcher-prod`)
PR: #190 — Add read-only alert delivery dashboard
Merge commit: `b1ee6188406f740f667b35ca08f5a975a500d362`

## Summary

PR20b production deploy and smoke completed successfully.

PR20b is a read-only Admin UI observability change over the PR20a `alert_delivery_attempts` ledger.

It adds:

- `/admin/alerts` delivery-attempt dashboard section;
- `/admin/alerts/delivery-attempts/{attempt_id}` detail page;
- bounded filters for delivery attempts;
- delivery invariant counters;
- render-time redaction for `last_error`;
- docs for dashboard behavior and smoke plan.

It intentionally does not add:

- database migration;
- retry;
- manual retry;
- automatic retry;
- POST mutation routes;
- worker heartbeat;
- parser health;
- queue lag;
- SLA metrics;
- run-once behavior changes;
- notifier behavior changes.

## Deploy status

Production `main` was fast-forwarded from PR20a to PR20b:

```text
4a9d1b5..b1ee618 main -> origin/main
b1ee618 Add read-only alert delivery dashboard (#190)
```

Changed files pulled into production:

```text
app/admin.py

docs/admin_ui.md
docs/alert_delivery.md
docs/handoff/pr20a_alert_delivery_attempts_production_smoke_2026-06-15.md
tests/test_admin_ui.py
```

Working tree before deploy was clean and on `main`.

## Alembic state

No PR20b migration was added.

Production Alembic checks:

```text
alembic heads   -> 0015_alert_delivery_attempts (head)
alembic current -> 0015_alert_delivery_attempts (head)
```

This confirms the PR20a ledger migration was already applied and PR20b did not introduce a schema change.

## App deploy

`docker compose config` passed.

App image was rebuilt and app container restarted:

```text
docker compose --env-file .env -f deploy/docker-compose.prod.yml build app
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

A short transient readiness window occurred immediately after restart:

```text
curl: (56) Recv failure: Connection reset by peer
```

This cleared after the app finished starting.

Final health:

```text
GET /health -> 200 OK
{"status":"ok"}
```

Final container state:

```text
deploy-app-1        Up 2 minutes (healthy)
deploy-postgres-1   Up 4 hours (healthy)
deploy-redis-1      Up 2 weeks (healthy)
deploy-worker-1     Up 3 hours
```

Worker was not restarted intentionally because PR20b is UI-only/read-only.

## Admin configuration

Runtime admin settings after deploy:

```text
ADMIN_UI_ENABLED=True
ADMIN_UI_ALLOW_QUERY_API_KEY=False
ADMIN_UI_TECHNICAL_OPS_ENABLED=False
ADMIN_UI_READ_KEY set=True
ADMIN_UI_WRITE_KEY set=True
ADMIN_UI_TECHNICAL_WRITE_KEY set=True
```

Technical operations remained disabled.

## DB counts before UI smoke

Counts before dashboard smoke:

```text
agent_tasks              2
alert_delivery_attempts  68
alerts_sent              2984
human_review_actions     0
human_reviews            0
investment_decisions     0
knowledge_notes          0
listing_analyses         730
listing_detail_snapshots 0
listing_enrichments      0
listings                 1582
market_evidence_items    0
market_research_runs     0
search_jobs              2
```

Important observation: unlike the PR20a deploy smoke, production now had live delivery-attempt rows.

## `/admin/alerts` dashboard smoke

`GET /admin/alerts` with `X-API-Key` read key returned:

```text
200
```

The page rendered:

- legacy JSONL alert history;
- new `Попытки доставки уведомлений` section;
- delivery invariant section;
- recent delivery-attempt table;
- detail links for attempt rows.

Dashboard delivery summary:

```text
Period hours: 168
Total attempts in selected period: 68
All-time total attempts: 68
Channels observed: google_sheets: 34, jsonl: 34
Latest attempt timestamp: 2026-06-15 08:24:23.695081
Live delivery observed: yes
```

Status summary:

```text
success: 68
failed: 0
skipped: 0
unknown: 0
```

This confirms the PR20a delivery ledger is not only migrated but actively recording live delivery attempts in production.

## Query-key leakage checks

Because `ADMIN_UI_ALLOW_QUERY_API_KEY=False`, PR20b dashboard UI must not render or propagate the read key.

Checks passed:

```text
grep -F "$ADMIN_READ_KEY" /tmp/pr20b_alerts.html
-> OK: read key not rendered
```

Delivery detail links:

```text
delivery-attempts/68
delivery-attempts/67
...
```

No query key in delivery detail links:

```text
OK: no api_key in delivery detail links
```

Detail page also did not render the read key:

```text
OK: read key not rendered in detail
```

Note: the legacy JSONL form still rendered an empty hidden field:

```html
<input type='hidden' name='api_key' value=''>
```

This is not a key leak because the value is empty.

## Filter checks

Valid filters returned `200`:

```text
/admin/alerts?limit=10       -> 200
/admin/alerts?hours=168      -> 200
/admin/alerts?status=failed  -> 200
/admin/alerts?channel=jsonl  -> 200
```

Invalid filters returned `400`:

```text
/admin/alerts?status=bad        -> 400
/admin/alerts?limit=bad         -> 400
/admin/alerts?hours=0           -> 400
/admin/alerts?hours=721         -> 400
/admin/alerts?search_job_id=bad -> 400
```

This confirms bounded filter validation is active.

## POST/mutation checks

`POST /admin/alerts` returned:

```text
405
```

Latest live attempt id was:

```text
ATTEMPT_ID=68
```

`GET /admin/alerts/delivery-attempts/68` returned:

```text
200
```

The detail page showed safe scalar data, including:

```text
Alert delivery attempt 68
listing_external_id: 8191896898
channel: google_sheets
dedupe_key: google_sheets:new:8191896898
payload_hash prefix: 9df454ed1898
status: success
attempt_count: 1
matching AlertSent: yes
matching listing: /admin/listings/1599
```

`POST /admin/alerts/delivery-attempts/68` returned:

```text
405
```

This confirms the new detail page is read-only.

## Delivery invariant checks

Dashboard counters and direct SQL invariant check were clean.

SQL result:

```text
success_without_alert_sent      0
non_success_with_alert_sent     0
success_missing_sent_at         0
non_success_with_sent_at        0
non_null_next_retry_at          0
bad_payload_hash_count          0
```

Interpretation:

- every success attempt has matching `AlertSent`;
- no failed/skipped/unknown attempt has a false matching `AlertSent`;
- every success has `sent_at`;
- no non-success has `sent_at`;
- `next_retry_at` remains null, as expected for PR20a/PR20b because retry scheduling is not implemented;
- every `payload_hash` matches `^[0-9a-f]{64}$`.

## No-mutation verification

Counts after UI smoke:

```text
agent_tasks              2
alert_delivery_attempts  68
alerts_sent              2984
human_review_actions     0
human_reviews            0
investment_decisions     0
knowledge_notes          0
listing_analyses         730
listing_detail_snapshots 0
listing_enrichments      0
listings                 1582
market_evidence_items    0
market_research_runs     0
search_jobs              2
```

Counts before and after were identical.

This confirms read-only GET dashboard/detail checks did not mutate:

- `alert_delivery_attempts`;
- `alerts_sent`;
- `listings`;
- `listing_analyses`;
- `search_jobs`;
- `agent_tasks`;
- research/evidence/human-review tables.

## Logs

App logs filtered for tracebacks, errors, warnings, secret-like terms, and delivery-related lines.

Observed only expected access logs:

```text
GET /admin/alerts/delivery-attempts/68 HTTP/1.1" 200 OK
POST /admin/alerts/delivery-attempts/68 HTTP/1.1" 405 Method Not Allowed
```

No tracebacks, exceptions, raw secrets, tokens, passwords, authorization headers, webhook values, or unexpected errors were observed.

## Final status

```text
PR20b production smoke: CLOSED ✅
```

Validated:

- PR20b commit deployed;
- no new migration;
- app healthy;
- worker left running and not restarted intentionally;
- `/admin/alerts` dashboard works;
- JSONL alert view remains compatible;
- live delivery attempts are visible;
- detail page works for real attempt id 68;
- filters are bounded and validated;
- POST mutation routes are not available;
- query-string read key is not rendered or propagated by the new delivery dashboard/detail links;
- delivery invariant counters are clean;
- DB counts are unchanged after UI smoke;
- logs are clean;
- no retry/manual retry/technical operations/run-once were triggered.

## Follow-up

PR20b is complete.

The next logical roadmap step is PR20c — manual retry for failed delivery attempts, but only after explicitly scoping write authorization, confirmation, retry eligibility, and safety invariants.
