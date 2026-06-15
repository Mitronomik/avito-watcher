# PR23c — Human review queue / shortlist read model production smoke

Date: 2026-06-15

Environment: production

Repository: `Mitronomik/avito-watcher`

Production path: `~/apps/avito-watcher`

Compose file: `deploy/docker-compose.prod.yml`

## Scope

PR23c is a bridge/operator feature between PR23b and PR24.

It is **not PR24**.

The next formal roadmap item remains:

```text
PR24 — Comparable quality scoring
```

PR23c adds a read-only human review queue / shortlist read model for operator use.

Expected behavior:

* add read-only `/admin/review-queue`;
* use PR23b centralized read-key admin access;
* no technical write key required;
* no POST route;
* no write workflow UI;
* no new migration;
* no new table or persisted shortlist state;
* no new priority score/formula;
* no scoring/verdict mutation;
* no alert delivery mutation;
* no agent task creation;
* no parser/monitor changes;
* no external API calls;
* no raw `payload_json` / `result_json` rendering;
* GET page views do not create audit events.

## Merge / deployed commit

Production was updated from:

```text
f7cd120 Harden admin key-based access control (#210)
```

to:

```text
fbbb2e1 Add read-only human review queue (#212)
```

Recent production history after pull:

```text
fbbb2e1 (HEAD -> main, origin/main, origin/HEAD) Add read-only human review queue (#212)
cc5f974 Add PR23b production smoke handoff
f7cd120 Harden admin key-based access control (#210)
5b3174d Add PR23a production smoke handoff (#209)
0aa7545 Add admin audit log ledger (#208)
```

## Deployment

Commands run:

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

docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app

docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app
```

Result:

```text
git pull: OK
build app: OK
app restarted: OK
postgres healthy: OK
redis healthy: OK
```

Worker was not rebuilt for this PR because PR23c only changes admin UI/read-model code.

## Alembic

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec app \
  sh -lc 'alembic current'
```

Result:

```text
0017_admin_audit_events (head)
```

No migration was added or expected for PR23c.

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

## Admin key loading

The admin read key was loaded from `.env` without printing the secret value.

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

## Audit baseline before GET

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
select count(*) as audit_before_review_queue_get from admin_audit_events;
SQL
```

Result:

```text
audit_before_review_queue_get = 2
```

## Read-only review queue route

Command:

```bash
curl -sS -o /tmp/pr23c_review_queue.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue"
```

Result:

```text
HTTP 200
```

The page rendered successfully and showed:

* `Human review queue`;
* read-only warning;
* `PR23c bridge/operator view, not PR24`;
* `Ordering is display-only`;
* unknown values are not fake zeros;
* filters summary for `limit`, `profile`, and `unreviewed_only`;
* listing rows with existing analysis, alert, human review, and investment decision summaries.

## GET does not create audit events

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
select count(*) as audit_after_review_queue_get from admin_audit_events;
SQL
```

Result:

```text
audit_after_review_queue_get = 2
```

Conclusion:

```text
GET /admin/review-queue did not create admin_audit_events.
```

## Unauthorized read access

Command:

```bash
curl -sS -o /tmp/pr23c_review_queue_no_key.json -w "%{http_code}\n" \
  "http://127.0.0.1:8010/admin/review-queue"

cat /tmp/pr23c_review_queue_no_key.json
```

Result:

```text
HTTP 403
{"detail":"Invalid admin key"}
```

Audit count after invalid GET:

```text
audit_after_invalid_review_queue_get = 2
```

Conclusion:

```text
Unauthorized GET is fail-closed and does not create audit noise.
```

## Read-only UI safety check

Command:

```bash
grep -Ei "method=['\"]?post|<form|admin_technical_write_key|confirm_action|approve|reject|assign|comment|mark reviewed|shortlist" \
  /tmp/pr23c_review_queue.html || true
```

Result:

```text
no matches
```

Confirmed absent:

* `<form>`;
* `method=post`;
* `admin_technical_write_key`;
* `confirm_action`;
* approve/reject/assign/comment/mark-reviewed actions;
* shortlist action buttons/state mutation UI.

## Raw JSON / secret / delivery URL check

Command:

```bash
grep -Ei "payload_json|result_json|script\.google\.com/macros|admin_technical_write_key|X-API-Key|Authorization|Cookie" \
  /tmp/pr23c_review_queue.html || true
```

Result:

```text
no matches
```

Confirmed absent:

* raw `payload_json`;
* raw `result_json`;
* raw Apps Script deployment URLs;
* admin technical write key;
* API key/header/cookie names.

## Profile filter smoke

Command:

```bash
curl -sS -o /tmp/pr23c_review_queue_commercial.html -w "%{http_code}\n" \
  -H "X-API-Key: $ADMIN_READ_KEY" \
  "http://127.0.0.1:8010/admin/review-queue?profile=commercial_rent"
```

Result:

```text
HTTP 200
```

The page rendered with:

```text
profile: commercial_rent
```

Rows rendered with:

```text
profile=commercial_rent
```

No 500/error occurred.

Production data currently mostly uses `commercial_rent`, so this smoke verifies that the filtered page is functional and does not regress after the profile-filter fix.

## App logs

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml logs --tail=200 app \
  | grep -Ei "Traceback|ERROR|Exception|admin_technical_write_key|X-API-Key|Authorization|Cookie|script\.google\.com/macros" || true
```

Result:

```text
no matches
```

Confirmed:

* no Traceback;
* no ERROR;
* no Exception;
* no admin technical key leak;
* no X-API-Key / Authorization / Cookie leak;
* no raw Apps Script deployment URL leak.

## Smoke note

The review queue HTML is rendered as a long single-line response, so simple `grep | head` output can include large chunks of the page.

This is not a PR23c blocker. Future smoke checks can use more targeted grep commands or HTML slicing to keep output shorter.

## Final verdict

```text
PR23c — Human review queue / shortlist read model ✅
Merged ✅
Deployed ✅
No migration ✅
Production-smoked ✅
Read-only route OK ✅
Read auth OK ✅
Unauthorized fail-closed OK ✅
GET no-audit OK ✅
No POST/forms/write UI ✅
No raw JSON/secrets/delivery URLs ✅
Profile filter page OK ✅
Logs clean ✅
```

## Next step

After this handoff is merged, the formal roadmap can continue to:

```text
PR24 — Comparable quality scoring
```

PR23c remains a bridge/operator feature and does not replace PR24.
