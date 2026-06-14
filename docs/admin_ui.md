# Admin UI safety shell (PR19a)

The Admin UI is an internal, server-side FastAPI operator workspace. It is **not a public UI** and is intended for access through a trusted channel such as an SSH tunnel:

```bash
ssh -L 8010:localhost:8010 deploy@server
```

Then open `http://localhost:8010/admin`.

PR19a adds the operator dashboard at `/admin`, shared navigation/layout, a small i18n-ready UI text dictionary with Russian as the default language, safety settings, redaction/truncation helpers, and safer read-only operator views for existing admin pages.

## Settings

- `ADMIN_UI_ENABLED=false` disables mounting the admin router by default.
- `ADMIN_UI_MODE=operator` shows the operator shell and safe read-only pages by default.
- `ADMIN_UI_LANGUAGE=ru` uses Russian labels by default. `en` is available for basic navigation labels.
- `ADMIN_UI_ALLOW_QUERY_API_KEY=false` disables query-string key authentication and prevents new operator links from propagating keys in URLs.
- `ADMIN_UI_TECHNICAL_OPS_ENABLED=false` hides and blocks technical write operations by default.
- `ADMIN_UI_READ_KEY`, `ADMIN_UI_WRITE_KEY`, and `ADMIN_UI_TECHNICAL_WRITE_KEY` are admin-specific keys. Existing `API_KEY` is accepted as a fallback only when admin-specific keys are not configured.

No secrets, API keys, webhook URLs, SMTP passwords, Telegram bot tokens, or full environment values should be displayed in the UI. Technical payloads are escaped, bounded, and redacted.

## Operator dashboard

`/admin` answers: what happened, what is important, what needs attention, what can be done safely, and where technical details live. The dashboard is read-only and does not mutate data.

## Existing pages preserved

PR19a preserves existing read functionality:

- `/admin/searches`
- `/admin/alerts`
- `/admin/listings`
- `/admin/listing-analyses`
- `/admin/technical`

Operator pages avoid raw JSON by default. Raw analysis details remain available only in collapsed “Technical details” blocks after redaction/truncation.

## Technical mode

Technical operations are hidden or blocked unless `ADMIN_UI_TECHNICAL_OPS_ENABLED=true` and the request is technically authorized. These operations can change monitoring behavior:

- create search
- edit search
- activate/deactivate search
- reset baseline
- run once

When disabled, write endpoints return `403`.

## Explicit non-goals for PR19a

PR19a does **not** change score/verdict and does not mutate listings or listing analyses. It adds no new agent tasks, no alert retry, no LLM calls, no external calls, no scheduler/background jobs, and no DB migration.

Not included until later PRs:

- listing detail workflow (PR19b)
- human review create/update form (PR19b)
- evidence, agents, and outcome analytics pages (PR19c)
- technical operations hardening beyond safety gating (PR19d)
- full RBAC, public UI, separate frontend app, desktop app, SPA, charts, calibration, or score/verdict overrides

## Production smoke plan

1. Pull main and check `git log -1 --oneline`.
2. Confirm no migration is expected with `alembic heads` and `alembic current`.
3. Check health: `curl -i http://127.0.0.1:8010/health`.
4. Verify disabled default: `curl -i http://127.0.0.1:8010/admin` should return `404` or `403`.
5. Enable Admin UI for smoke only and access through an SSH tunnel.
6. Smoke GET `/admin`, `/admin/searches`, `/admin/alerts`, `/admin/listings`, `/admin/listing-analyses`, `/admin/technical`.
7. Assert HTTP 200, no Traceback, no secrets, and no raw JSON on the operator dashboard.
8. Verify technical operations are hidden/disabled or return 403.
9. Capture baseline counts for core tables, call read-only admin pages, and verify counts are unchanged.
10. Check worker logs: no Traceback, no unexpected run-once, no unexpected agent execution, and normal monitor cycle behavior.
