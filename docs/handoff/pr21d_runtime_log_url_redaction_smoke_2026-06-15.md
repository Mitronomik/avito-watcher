# PR21d — Runtime log URL redaction production smoke

Date: 2026-06-15
Status: PASSED after hotfix
Scope: production runtime log redaction hardening

## Summary

PR21d added centralized runtime log redaction so sensitive external URLs and token-like fragments are sanitized in app and worker logs.

The production rollout required two steps:

1. Deploy PR21d runtime log redaction.
2. Deploy a hotfix for a formatter regression found during production smoke.

Final state: runtime log redaction is deployed, the formatter regression is fixed, sensitive URL/token patterns are not visible in recent logs, operational counters remain observable, and Admin UI remains healthy.

## Related PRs / commits

### PR201 — Runtime log URL redaction

Merged commit:

```text
15161a9821f079d6b1316b9110fb0aaf51e1bbea
```

Short SHA observed in production before hotfix:

```text
15161a9 Redact sensitive external URLs in runtime logs (#201)
```

Purpose:

- add `app/core/log_sanitizer.py`;
- add rendered-output log redaction;
- install log redaction in FastAPI app startup path;
- install log redaction in worker startup path;
- add sanitizer tests;
- document runtime log redaction in `docs/admin_ui.md`.

### PR202 — Runtime log redaction formatting regression hotfix

Merged commit:

```text
792a45bdc44fa775403bfa076d1cb9f6215b43f2
```

Short SHA observed in production:

```text
792a45b Fix runtime log redaction formatting regression (#202)
```

Purpose:

- keep redaction at final rendered-output layer only;
- make `RedactingFilter` compatibility no-op;
- preserve operational counters;
- restore exact URL query redaction for `key=` without broad substring matching;
- add regression tests for `%`-style logging formatting and operational counter preservation.

## Deployment commands executed

```bash
cd ~/apps/avito-watcher

git pull --ff-only origin main
git log -1 --oneline

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app worker

for i in $(seq 1 20); do
  echo "try $i"
  curl -fsS http://127.0.0.1:8010/health && break
  sleep 2
done
```

Observed after final hotfix deploy:

```text
792a45b (HEAD -> main, origin/main, origin/HEAD) Fix runtime log redaction formatting regression (#202)
```

Build/restart result:

```text
Image deploy-app    Built
Image deploy-worker Built
Container deploy-redis-1    Healthy
Container deploy-postgres-1 Healthy
Container deploy-app-1      Started
Container deploy-worker-1   Started
```

Health check behavior after restart:

```text
try 1: connection reset by peer
try 2: connection reset by peer
try 3: health endpoint available
```

This matches the usual startup race already seen in prior deploys.

## Migration status

No DB migration was added in PR21d or the hotfix.

Admin system page after final hotfix still reported:

```text
current DB revision: 0016_monitor_cycle_runs
```

## Initial PR21d smoke result before hotfix

The first PR21d deploy successfully redacted raw sensitive URL patterns, but exposed a logging formatter regression.

Successful checks from initial deploy:

```text
OK no raw Apps Script deployment URL
OK no raw googleusercontent macro query
OK no obvious secret-like fragments
/admin/system: 200
```

Failure found:

```text
Message: 'monitor_service.cycle_summary ... proxy_failures=<redacted> ...'
```

Interpretation:

- production logs showed Python logging internal `Message:` output;
- the sanitizer/filter had redacted part of the raw logging message before `%`-style formatting;
- `record.args` remained present, causing a placeholder/argument mismatch;
- operational counters such as proxy counters were also over-redacted.

Decision:

```text
PR21d smoke before hotfix: FAILED
```

Required hotfix:

- do not sanitize `record.msg` before formatting;
- do not mutate `record.args`;
- rely on formatter-wrapper final output redaction;
- preserve operational counters.

## Final hotfix smoke checks

Recent logs were captured after the hotfix deploy:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=300 app worker > /tmp/pr21d_hotfix_logs.txt
```

### Logging formatter regression check

Command:

```bash
grep -E "Logging error|Message:|Arguments:" /tmp/pr21d_hotfix_logs.txt \
  && echo "FAIL logging formatter regression still present" \
  || echo "OK no logging formatter regression"
```

Observed:

```text
OK no logging formatter regression
```

Result: PASSED.

### Operational counter over-redaction check

Command:

```bash
grep -E "proxy_failures=<redacted>|proxy_success_count': '<redacted>'|proxy_failure_count': '<redacted>'|proxy_quarantine_on_failure_count': '<redacted>'" /tmp/pr21d_hotfix_logs.txt \
  && echo "FAIL operational counters over-redacted" \
  || echo "OK operational counters not over-redacted"
```

Observed:

```text
OK operational counters not over-redacted
```

Result: PASSED.

### Raw Apps Script deployment URL leak check

Command:

```bash
grep -E "script\.google\.com/macros/s/[^[:space:]]+/(exec|dev)" /tmp/pr21d_hotfix_logs.txt \
  && echo "FAIL raw Apps Script deployment URL leak" \
  || echo "OK no raw Apps Script deployment URL"
```

Observed:

```text
OK no raw Apps Script deployment URL
```

Result: PASSED.

### Raw googleusercontent macro query leak check

Command:

```bash
grep -E "script\.googleusercontent\.com/macros/echo\?[^[:space:]]*(user_content_key|lib|key)=" /tmp/pr21d_hotfix_logs.txt \
  && echo "FAIL raw googleusercontent macro query leak" \
  || echo "OK no raw googleusercontent macro query"
```

Observed:

```text
OK no raw googleusercontent macro query
```

Result: PASSED.

### Obvious secret-like fragment check

Command:

```bash
grep -Ei "(api_key|apikey|access_token|refresh_token|user_content_key|password|secret|signature|Authorization: Bearer|Bearer )[=: ][^[:space:]'\"]+" /tmp/pr21d_hotfix_logs.txt \
  && echo "CHECK possible secret-like fragment" \
  || echo "OK no obvious secret-like fragments"
```

Observed:

```text
OK no obvious secret-like fragments
```

Result: PASSED.

## Admin UI smoke

Command:

```bash
ADMIN_READ_KEY="$(
  grep -E '^ADMIN_UI_READ_KEY=' .env \
  | cut -d= -f2- \
  | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
)"

curl -sS -o /tmp/pr21d_hotfix_system.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/system"

grep -E "Monitor cycle history|Alert Delivery health|Alembic" /tmp/pr21d_hotfix_system.html
```

Observed:

```text
200
Alert Delivery health
Monitor cycle history
Alembic
```

Result: PASSED.

## Production state after final hotfix smoke

From `/admin/system` after hotfix:

```text
Alert Delivery health:
  delivery attempts total: 361
  last 24h: 361
  last 7d: 361
  failed 24h/7d: 3/3
  unknown 24h/7d: 0/0
  manual_retry attempts: 0
  alerts_sent total: 3274

Delivery integrity issues:
  success_without_alert_sent: 0
  success_missing_sent_at: 0
  non_success_with_sent_at: 0
  bad_payload_hash_count: 0
  non_success_after_alert_sent: 0

Resolved delivery history:
  resolved_non_success_with_later_alert_sent: 3

Retry scheduling indicators:
  next_retry_at_non_null: 0

Monitor cycle history:
  last 24h cycles total: 44
  success: 44
  partial: 0
  failed: 0
  skipped: 0
  stale running count: 0

Data volume:
  listings: 1727
  listing_analyses: 730
  alert_delivery_attempts: 361
  alerts_sent: 3274
  agent_tasks: 2
  search_jobs: 2

Alembic:
  current DB revision: 0016_monitor_cycle_runs
```

## Security notes

The smoke intentionally used pattern checks and did not print or store real production secrets in this handoff.

Sensitive production values such as deployment IDs, API keys, bearer tokens, user content keys, webhook URLs, and raw macro query strings are not included here.

Safe examples only:

```text
https://script.google.com/.../exec
https://script.googleusercontent.com/macros/echo?...
<redacted>
```

## Acceptance result

```text
PR21d runtime log URL redaction: DEPLOYED
PR21d initial production smoke: FAILED due logging formatter regression
PR21d hotfix production smoke: PASSED
Final production status: PASSED
```

Final checklist:

```text
Code deployed: 792a45b ✅
Images rebuilt: app + worker ✅
Containers restarted: app + worker ✅
/admin/system returns 200 ✅
No DB migration needed ✅
DB revision remains 0016_monitor_cycle_runs ✅
No raw Apps Script deployment URL in recent logs ✅
No raw googleusercontent macro query in recent logs ✅
No obvious secret-like fragments in recent logs ✅
No Python logging formatter regression in recent logs ✅
Operational counters are not over-redacted ✅
Alert delivery integrity remains healthy ✅
Monitor cycle history remains healthy ✅
```

## Follow-up

PR21d is complete after this handoff.

Next roadmap step should return to Phase B roadmap rather than expanding PR21 further:

```text
PR22 — Backup / restore / retention policy
```
