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
- `ADMIN_UI_READ_KEY`, `ADMIN_UI_WRITE_KEY`, and `ADMIN_UI_TECHNICAL_WRITE_KEY` are admin-specific keys. Existing `API_KEY` is accepted as a fallback only when admin-specific keys are not configured. In production, set `ADMIN_UI_WRITE_KEY` explicitly and separately from the read key so browser form writes require a distinct operator write secret.

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

## Listing detail and human review workflow (PR19b)

PR19b adds a server-rendered listing detail workflow for operators:

- `/admin/listings` links each row to `/admin/listings/{listing_id}`.
- The detail page shows listing core fields: internal id, `external_id`, title, price, area, address, source URL, publication label/date, first/last seen timestamps, and active status.
- Source links are clickable only when the URL is valid `http`/`https`; unsafe schemes such as `javascript:` are displayed as escaped plain text.
- The latest successful deterministic analysis is selected by `listing_external_id`, `status='success'`, then `created_at desc, id desc`. Failed, skipped, stale, pending, and running analyses do not override the latest successful one.
- Analysis text is escaped as plain text. `report_md` is not trusted HTML. JSON facts/questions are shown only inside collapsed technical details after Admin UI redaction/truncation.
- Existing human review state is shown when a review exists for the stable listing detail context.

### Stable review context

Listing detail uses one stable admin review context per listing/analysis snapshot:

```text
context_type = admin_listing_detail
search_job_id = None
review_context_key = listing:{listing_external_id}:search:none:analysis:{listing_analysis_id_or_none}:context:admin_listing_detail
```

The key is generated through the PR18 `build_review_context_key` service helper. On first create, the review links to the listing and the latest successful analysis. On later updates, identity/context fields are not changed: `listing_id`, `listing_external_id`, `search_job_id`, `listing_analysis_id`, `review_context_key`, and `created_at` remain stable, so a newer analysis cannot silently move an existing human decision.

### Human decision fields

The form writes only PR18 human-review fields through `HumanReviewService`; it does not write investment decisions. Operators can save:

- `human_verdict`
- `outcome_status`
- `watchlist`
- `next_action`
- `notes`

Stored enum values remain English/internal. Russian labels are UI-only:

| Field | Stored value | Russian label |
| --- | --- | --- |
| `human_verdict` | `interesting` | Интересно |
| `human_verdict` | `neutral` | Нейтрально |
| `human_verdict` | `not_interesting` | Не интересно |
| `human_verdict` | `false_positive` | Ложное срабатывание |
| `human_verdict` | `false_negative` | Пропущенная возможность |
| `human_verdict` | `needs_more_data` | Нужны данные |
| `outcome_status` | `not_started` | Не начато |
| `outcome_status` | `contacted_owner` | Связались с владельцем |
| `outcome_status` | `waiting_response` | Ждём ответ |
| `outcome_status` | `documents_requested` | Запрошены документы |
| `outcome_status` | `sent_to_expert` | Отправлено эксперту |
| `outcome_status` | `under_review` | На проверке |
| `outcome_status` | `rejected_after_call` | Отклонено после звонка |
| `outcome_status` | `watchlist` | В наблюдении |
| `outcome_status` | `deal_candidate` | Кандидат в сделку |
| `outcome_status` | `offer_made` | Сделано предложение |
| `outcome_status` | `deal_lost` | Сделка потеряна |
| `outcome_status` | `deal_done` | Сделка состоялась |
| `outcome_status` | `closed` | Закрыто |
| `next_action` | `open_listing` | Открыть объявление |
| `next_action` | `call_owner` | Позвонить владельцу |
| `next_action` | `request_documents` | Запросить документы |
| `next_action` | `run_market_research` | Запустить исследование рынка |
| `next_action` | `run_data_quality_review` | Проверить качество данных |
| `next_action` | `send_to_expert` | Отправить эксперту |
| `next_action` | `add_to_watchlist` | Добавить в наблюдение |
| `next_action` | `reject` | Отклонить |
| `next_action` | `do_nothing` | Ничего не делать |

### Write auth and safety

GET `/admin/listings/{listing_id}` uses the existing PR19a read-key behavior. POST `/admin/listings/{listing_id}/human-review` is an operator write and requires `ADMIN_UI_WRITE_KEY`; `ADMIN_UI_TECHNICAL_WRITE_KEY` is not required, and `ADMIN_UI_TECHNICAL_OPS_ENABLED=false` does not block human review writes.

Because browser HTML forms cannot set `X-API-Key`, the human-review form includes a visible password input named `admin_write_key`. The submitted key is used only for authorization, is removed before validation/persistence, is never rendered back to HTML, and is not stored in review notes, action payloads, or JSON fields. Header-based `X-API-Key` remains supported for clients/tests. Query-string `api_key` remains disabled by default through `ADMIN_UI_ALLOW_QUERY_API_KEY=false`.

### Boundaries

PR19b does not mutate listings, listing analyses, deterministic score/verdict, searches, alerts, agents, market evidence, outcome analytics, parser behavior, monitor cycles, Google Sheets, or external services. POST writes only `human_reviews` and `human_review_actions`. Invalid form data or service errors roll back the transaction and do not partially write review/action rows. No DB migration is expected.

### Production smoke plan

1. Check branch/commit with `git status` and `git rev-parse --short HEAD`.
2. Confirm Alembic with `alembic heads` and `alembic current`; no PR19b migration is expected.
3. Check health with `curl -i http://127.0.0.1:8010/health`.
4. Open `GET /admin/listings/{existing_listing_id}` through the read-key admin access path and verify listing fields, latest analysis, escaped text, and collapsed redacted details.
5. Create a review through the form with a smoke note prefix such as `pr19b-smoke-2026-06-14`; verify `human_reviews +1`, `human_review_actions` increments, and forbidden tables remain unchanged.
6. Update the same review and verify the same `human_reviews` row is updated, `review_context_key` and `listing_analysis_id` remain unchanged, and actions increment.
7. Remove smoke review/action rows by the smoke note prefix and verify smoke counts return to zero.
