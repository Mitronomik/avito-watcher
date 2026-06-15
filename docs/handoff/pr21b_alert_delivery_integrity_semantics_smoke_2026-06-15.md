# PR21b — Alert delivery integrity semantics production smoke

Date: 2026-06-15

Repository: `Mitronomik/avito-watcher`

PR:

```text
#197 — Normalize alert delivery integrity counters
```

Production deploy commit:

```text
d9b77364a0271b48469099906d2790ae8ba520ae
```

Short commit:

```text
d9b7736 Normalize alert delivery integrity counters (#197)
```

Status:

```text
PASSED ✅
```

---

## Purpose

PR21b normalized the read-model semantics for alert delivery integrity counters.

The production issue discovered after PR21a was that the dashboard showed:

```text
non_success_with_alert_sent: 3
```

as a hard invariant problem, even though these rows represented historical failed `google_sheets` delivery attempts that were later resolved by successful delivery and matching `AlertSent` rows.

PR21b replaced the flat counter with timestamp-aware grouping:

```text
Delivery integrity issues
Resolved delivery history
Retry scheduling indicators
```

The goal was to avoid false alarms while keeping true delivery integrity issues visible.

---

## Scope confirmed

Allowed scope:

- read-only Admin UI semantics cleanup;
- shared helper for `/admin/alerts` and `/admin/system`;
- timestamp-aware split of non-success attempts with matching `AlertSent`;
- docs and tests.

Confirmed non-scope:

- no migration;
- no delivery behavior change;
- no retry behavior change;
- no worker behavior change;
- no parser changes;
- no agent changes;
- no scoring changes;
- no research changes;
- no automatic repair;
- no deletion or rewriting of historical failed attempts.

---

## Deployment commands

Executed on production host:

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

Observed:

```text
Updating d0b9e2a..d9b7736
Fast-forward

app/admin.py                                                                 |  56 +++++--
docs/admin_ui.md                                                             |   9 +-
docs/alert_delivery.md                                                       |  19 ++-
docs/handoff/pr21a_read_only_production_health_dashboard_smoke_2026-06-15.md | 347 +++++++++++++++++++++++++++++++++++++++++++
tests/test_admin_system_health.py                                            |   8 +-
tests/test_admin_ui.py                                                       |  71 ++++++++-
6 files changed, 482 insertions(+), 28 deletions(-)
create mode 100644 docs/handoff/pr21a_read_only_production_health_dashboard_smoke_2026-06-15.md

d9b7736 (HEAD -> main, origin/main, origin/HEAD) Normalize alert delivery integrity counters (#197)
```

Note: PR21a docs handoff was also pulled because the production checkout moved from `d0b9e2a` to `d9b7736`.

---

## Alembic check

Observed:

```text
0015_alert_delivery_attempts (head)
```

for both:

```bash
app alembic heads
app alembic current
```

Conclusion:

```text
No migration was introduced by PR21b ✅
DB revision remains 0015_alert_delivery_attempts ✅
```

---

## Build and restart

Observed:

```text
Image deploy-app Built
Container deploy-app-1 Started
```

Initial health loop showed transient startup resets:

```text
try 1
curl: (56) Recv failure: Connection reset by peer
try 2
curl: (56) Recv failure: Connection reset by peer
```

This matches prior deploys where the app was still starting immediately after `up -d app`.

Subsequent Admin UI smoke requests returned HTTP 200.

---

## Admin endpoints smoke

Read key loaded from `.env` and used only as request header:

```bash
ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"
```

Checked:

```bash
curl -sS -o /tmp/pr21b_alerts.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/alerts"

curl -sS -o /tmp/pr21b_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"
```

Observed:

```text
200
200
```

Conclusion:

```text
/admin/alerts reachable ✅
/admin/system reachable ✅
```

---

## Normalized delivery integrity groups

Checked:

```bash
grep -E "Delivery integrity issues|Resolved delivery history|Retry scheduling indicators" /tmp/pr21b_alerts.html
grep -E "Delivery integrity issues|Resolved delivery history|Retry scheduling indicators" /tmp/pr21b_system.html
```

Confirmed on `/admin/alerts`:

```text
Delivery integrity issues (in selected period)
Resolved delivery history (in selected period)
Retry scheduling indicators (in selected period)
```

Confirmed on `/admin/system`:

```text
Delivery integrity issues (all time)
Resolved delivery history (all time)
Retry scheduling indicators (all time)
```

Conclusion:

```text
Grouped UI semantics render correctly ✅
/admin/alerts uses selected-period scope ✅
/admin/system uses all-time scope ✅
```

---

## `/admin/alerts` counters

Observed in selected period:

```text
Period hours: 168

total attempts in selected period: 329
all-time total attempts: 329
channels observed: google_sheets: 166, jsonl: 163
latest attempt timestamp: 2026-06-15 14:33:02.933557
live delivery observed: yes

success: 326
failed: 3
skipped: 0
unknown: 0
```

Delivery integrity issues:

```text
success_without_alert_sent: 0
success_missing_sent_at: 0
non_success_with_sent_at: 0
bad_payload_hash_count: 0
non_success_after_alert_sent: 0
```

Resolved delivery history:

```text
resolved_non_success_with_later_alert_sent: 3
```

Retry scheduling indicators:

```text
next_retry_at_non_null: 0
```

Conclusion:

```text
Hard integrity issues are zero ✅
Historical failed attempts resolved by later delivery are informational ✅
Retry scheduling indicator is zero ✅
```

---

## `/admin/system` counters

Observed all-time:

```text
delivery attempts total: 329
last 24h: 329
last 7d: 329
failed 24h/7d: 3/3
unknown 24h/7d: 0/0
manual_retry attempts: 0
alerts_sent total: 3242

status counts 24h: failed: 3, success: 326
status counts 7d: failed: 3, success: 326
channel counts 24h: google_sheets: 166, jsonl: 163
channel counts 7d: google_sheets: 166, jsonl: 163
alerts_sent by channel: google_sheets: 1591, jsonl: 1651
```

Delivery integrity issues:

```text
success_without_alert_sent: 0
success_missing_sent_at: 0
non_success_with_sent_at: 0
bad_payload_hash_count: 0
non_success_after_alert_sent: 0
```

Resolved delivery history:

```text
resolved_non_success_with_later_alert_sent: 3
```

Retry scheduling indicators:

```text
next_retry_at_non_null: 0
```

Conclusion:

```text
System health no longer treats resolved historical failures as hard violations ✅
All-time integrity issues are zero ✅
```

---

## Old misleading counter hidden

Checked:

```bash
grep -F "non_success_with_alert_sent" /tmp/pr21b_alerts.html && echo "OLD COUNTER LEAK" || echo "OK old counter hidden"
grep -F "non_success_with_alert_sent" /tmp/pr21b_system.html && echo "OLD COUNTER LEAK" || echo "OK old counter hidden"
```

Observed:

```text
OK old counter hidden
OK old counter hidden
```

Conclusion:

```text
Old flat misleading counter is no longer rendered ✅
```

---

## Redaction regression check

Checked that PR195 redaction still holds after PR21b.

The real Apps Script deployment id was not written into this handoff. Production smoke used the concrete value locally and confirmed it was absent.

Checked files:

```text
/tmp/pr21b_alerts.html
/tmp/pr21b_system.html
/tmp/pr21b_attempt_140.html
```

Observed:

```text
OK no deployment id in /tmp/pr21b_alerts.html
OK no raw Apps Script URL in /tmp/pr21b_alerts.html

OK no deployment id in /tmp/pr21b_system.html
OK no raw Apps Script URL in /tmp/pr21b_system.html

OK no deployment id in detail
OK no raw URL in detail
```

Conclusion:

```text
Apps Script deployment id is not rendered ✅
Raw script.google.com/macros/s/ URL is not rendered ✅
Redaction regression did not reappear ✅
```

---

## Detail page smoke

Checked failed attempt detail page:

```bash
curl -sS -o /tmp/pr21b_attempt_140.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/alerts/delivery-attempts/140"
```

Observed:

```text
200
```

Detail page showed:

```text
Alert delivery attempt 140
status: failed
channel: google_sheets
error_type: HTTPStatusError
matching AlertSent: yes
delivery resolution: Resolved by later delivery
manual retry blocked because a matching AlertSent already exists
```

Conclusion:

```text
Resolved-by-later-delivery label renders ✅
Manual retry remains blocked when matching AlertSent exists ✅
PR20c retry eligibility unchanged ✅
Detail page remains read-only ✅
```

---

## Security and safety notes

Confirmed:

```text
No real Apps Script deployment id committed to docs ✅
No raw Apps Script URL committed to docs ✅
Admin read key not rendered in smoke snippets ✅
No retry executed ✅
No run-once executed ✅
No technical operation executed ✅
No DB migration ✅
```

---

## Known non-blocking observations

Two cosmetic observations were found in rendered output:

```text
1. A detail-page sentence appears to contain a minor typo: "manual retr" instead of "manual retry".
2. The redacted marker may render as a safe placeholder variant rather than a pretty exact string.
```

These are not blockers because:

```text
- the deployment id is hidden;
- raw script.google.com/macros/s/ is hidden;
- delivery semantics are correct;
- manual retry behavior is unchanged;
- all hard integrity counters are zero.
```

They can be handled later in a small polish PR if needed.

---

## Final verdict

```text
PR21b production smoke: PASSED ✅
Delivery integrity semantics normalized ✅
Hard integrity issues: 0 ✅
Resolved historical failures: 3 ✅
Retry scheduling indicators: 0 ✅
Old flat counter hidden ✅
Apps Script deployment id not rendered ✅
Manual retry eligibility unchanged ✅
```

PR21b is closed from production smoke perspective.
