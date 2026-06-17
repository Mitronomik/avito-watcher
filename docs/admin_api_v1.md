# Admin API v1

Admin API v1 is a small, read-only JSON API foundation for future admin/frontend clients. It does not replace the existing server-rendered `/admin` pages.

## Prefix and scope

All routes live under:

```text
/api/admin/v1
```

Current routes:

- `GET /api/admin/v1/status`
- `GET /api/admin/v1/meta`

PR30 extends `/api/admin/v1/meta` into the stable frontend metadata contract. It still does not implement listing/review/evidence endpoints, decision cards, workflow state, allowed actions, write endpoints, technical actions, report generation, migrations, DB/session dependencies, or CORS changes.

## Authentication

Admin API v1 uses the existing centralized admin read key validation and the existing read-key header:

```text
X-API-Key: <configured read key>
```

There is no new auth key, secret, token, cookie, session, JWT, OAuth, bearer-token transport, or query-string auth. Query-string authentication is not accepted for this API, even when legacy HTML admin query-key compatibility is enabled. If the read key is not configured, requests fail closed.

## Success envelope

Successful responses use this stable envelope:

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T00:00:00+00:00"
  }
}
```

`generated_at` is response metadata only and must not be used in deterministic analysis inputs, `facts_json`, or hashes.

## Error envelope

Admin API v1 errors use this scoped JSON envelope:

```json
{
  "ok": false,
  "error": {
    "code": "forbidden",
    "message": "Invalid admin key",
    "details": null
  },
  "meta": {
    "api_version": "admin-v1"
  }
}
```

Stable public error codes are `unauthorized`, `forbidden`, `not_found`, `validation_error`, `pagination_limit_exceeded`, and `internal_error`. Error handling is scoped to `/api/admin/v1` and does not change the existing `/admin` HTML route behavior.

## `GET /api/admin/v1/status`

Purpose: minimal API smoke endpoint.

Example response:

```json
{
  "ok": true,
  "data": {
    "status": "ok",
    "service": "avito-watcher",
    "api": "admin-v1"
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T00:00:00+00:00"
  }
}
```

The endpoint does not expose environment variables, settings, provider config, database DSNs, worker status, migrations, queue stats, admin key state, technical key state, or system diagnostics.

## `GET /api/admin/v1/meta` — PR30 meta contract

Purpose: static, read-only frontend metadata contract. The contract version is separate from the API version:

```text
META_CONTRACT_VERSION = "v1"
```

The endpoint remains backward-compatible with PR29 by keeping `api_version`, `service`, and `status`. PR30 adds `meta_contract_version`, `roles`, `permissions`, `enums`, `labels`, `legacy_labels`, `errors`, and a new safe `capabilities` contract block.

Example shape:

```json
{
  "ok": true,
  "data": {
    "api_version": "admin-v1",
    "meta_contract_version": "v1",
    "service": "avito-watcher",
    "status": "ok",
    "roles": [],
    "permissions": {},
    "enums": {},
    "labels": {},
    "legacy_labels": {},
    "errors": {},
    "capabilities": {
      "admin_api_v1": true,
      "read_api": true,
      "write_api": false,
      "technical_api_actions": false,
      "decision_card": false,
      "report_export": false
    }
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T00:00:00+00:00"
  }
}
```

### Roles / personas

The contract exposes static frontend personas only: `reader`, `reviewer`, and `technical`. They are not persisted users, not role assignments, and not an RBAC storage model. They exist so frontend clients can render future controls consistently.

### Permission matrix

Permissions are centrally defined in `app/api/admin_v1/meta_contract.py`. Each permission includes role booleans, `implemented`, `available_now`, `requires_endpoint`, `introduced_in`, and RU/EN labels.

Permissions metadata is not backend authorization. Frontend clients may use it to hide/show controls, but every backend endpoint must still enforce authorization independently. PR30 does not activate write permissions: future write and technical permissions are present only as unavailable metadata with `implemented=false` and `available_now=false`.

### Enum registry

The enum registry exposes stable display values grounded in existing code constants, including human review statuses/actions/outcomes, agent task statuses, source types, and verification statuses. Values without real stable code constants are omitted rather than invented. Every enum group includes an `unknown_value` fallback so frontend clients can safely render unexpected values.

### Labels and legacy labels

`labels` contains static RU/EN display labels. Labels are display metadata only; backend business logic must continue to use stable code values.

`legacy_labels` provides safe overrides for legacy UI/action names. In particular, `sent_to_expert` is labeled as “Сформировать экспертное заключение системы” / “Prepare system expert memo” and does not imply external expert handoff.

### Error catalog

`errors` documents stable public API-level error codes with HTTP status, RU/EN label, RU/EN description, and retryability. The catalog intentionally contains no stack traces, file paths, exception class names, route internals, or secrets.

### Safe capabilities

Capabilities describe contract availability, not runtime diagnostics. They do not reveal key presence, environment flags, worker/provider/webhook/LLM state, provider credentials, settings, DSNs, or webhook URLs. In PR30, `write_api=false` and `technical_api_actions=false`.

### Static/no DB behavior

The meta contract is built from static code constants and schemas. It does not query the database, does not require a DB session, does not write audit events, and does not create agent tasks, alerts, listing analyses, search jobs, or domain data.

## Pagination contract

Reusable helpers use bounded `limit` and `offset` values:

- default limit: `50`
- maximum limit: `100`
- default offset: `0`
- negative values are rejected
- too-large limits are rejected deterministically

When used by future list endpoints, pagination metadata should be included in response `meta`.

## Ordering contract

Reusable ordering helpers require an explicit allowlist of sortable fields. Unknown fields and invalid directions are rejected. Directions are limited to `asc` and `desc`, and defaults are deterministic.

## Redaction policy

The API-boundary redaction helper recursively redacts secret-bearing keys in response payloads without mutating the original object. URL query parameters that commonly carry secrets are redacted.

## Non-goals

- PR30 does not add UI.
- PR30 does not add write endpoints.
- PR30 does not add technical API actions.
- PR30 does not add decision cards.
- PR30 does not add listing/review domain data endpoints.
- PR30 does not add workflow state.
- PR30 does not add an allowed-actions endpoint.
- PR30 does not change scoring, parser, alerts, evidence, or agents.
- PR30 does not add migrations.
- PR30 does not add DB/session dependency.
- PR30 does not change CORS.
- PR30 does not expose settings, env, keys, provider config, webhook state, worker diagnostics, or runtime secrets.

## PR31 — read-only listing and review queue APIs

PR31 adds stable JSON read endpoints for frontend listing, detail, review queue, and safe decision-source views. These routes are read-only and live only under `/api/admin/v1`; no unversioned `/api/admin/...` aliases and no `/admin` HTML namespace routes are added.

### Endpoints

- `GET /api/admin/v1/listings`
- `GET /api/admin/v1/listings/{listing_id}`
- `GET /api/admin/v1/review-queue`
- `GET /api/admin/v1/listings/{listing_id}/decision-source`

`listing_id` is the internal integer `listings.id`. PR31 does not add external-id path variants. Exact external id lookup is available only as `GET /api/admin/v1/listings?external_id=...`.

All success responses keep the PR29 envelope:

```json
{
  "ok": true,
  "data": {},
  "meta": {"api_version": "admin-v1", "generated_at": "..."}
}
```

Errors keep the Admin API v1 JSON error envelope. The existing read key header is required; query-string auth, cookies, Bearer auth, technical key-only access, and new auth transports are not supported.

### Pagination, ordering, and filters

List endpoints use bounded `limit` and `offset` pagination with response metadata:

```json
{"pagination": {"limit": 50, "offset": 0, "has_more": true}}
```

`has_more` is computed by fetching one extra row or an equivalent bounded slice. PR31 avoids count queries for these endpoints.

`/listings` supports allowlisted ordering by `id`, `first_seen_at`, `last_seen_at`, `published_at`, `price`, and `area_m2`. Null values sort last and `id` is used as a direction-aware stable tie-breaker. Minimal filters are `is_active`, exact `external_id`, `search_job_id`, `min_price`, `max_price`, `min_area_m2`, and `max_area_m2`.

`/review-queue` accepts bounded pagination, deterministic allowlisted ordering by `analysis_created_at`, `score`, `verdict`, `listing_id`, `published_at`, `price`, and `area_m2`, and the minimal filters `verdict`, `min_score`, `max_score`, and `profile`. The shared review queue read service applies eligibility, filters, ordering, then pagination, so filtered pages and `has_more` are computed from the filtered ordered set instead of an unfiltered slice.

Unknown ordering fields, invalid directions, and unknown query parameters return validation errors.

### DTOs and schema versions

PR31 DTOs are allowlisted and versioned:

- `listing-summary-v1`
- `listing-detail-v1`
- `review-queue-item-v1`
- `decision-source-v1`

Listing summaries include safe listing fields and the latest successful analysis summary. Listing detail adds compact `latest_human_review` and an optional compact `alert_summary` placeholder. Review queue items expose listing summary fields, analysis summary fields, and `review.queue_status = "needs_review"` when the row is in the display queue.

Latest analysis selection for listing APIs is deterministic: latest successful analysis by `created_at desc, id desc`. Failed or skipped analyses do not replace the latest successful analysis.

### Decision-source boundary

`GET /api/admin/v1/listings/{listing_id}/decision-source` returns a safe source bundle only: listing summary, latest analysis summary, compact human review summary when present, availability flags, source refs, and limitations. It is intentionally not Decision Card v1.

PR31 decision-source explicitly does not include recommendations, headline, top reasons, top risks, next steps, missing-data ranking, readiness checklist, `workflow_state`, or `allowed_actions`.

### Security, redaction, and side effects

PR31 does not expose raw `facts_json`, `result_json`, `payload_json`, `risks_json`, `questions_json`, provider/debug payloads, or `report_md`. URLs and nested optional data pass through the Admin API redaction boundary.

PR31 performs SELECT-only read operations. It does not add migrations, write endpoints, technical actions, parser runs, scoring recalculation, evidence mutation, agent calls, LLM/RAG calls, alert delivery, human review writes, audit writes for reads, CORS changes, or new auth transport.

Existing `/admin` HTML routes remain unchanged, and existing `/api/admin/v1/status` and `/api/admin/v1/meta` remain compatible with PR29/PR30, including `meta_contract_version = "v1"`.

## PR32: derived workflow state read API

PR32 adds a deterministic, read-only workflow snapshot for Admin API v1. The endpoint lives only under the versioned prefix:

- `GET /api/admin/v1/listings/{listing_id}/workflow`

`GET /api/admin/v1/listings/{listing_id}/decision-source` also includes the exact same workflow DTO under `workflow`. PR32 does not add unversioned aliases, `/admin` HTML routes, write endpoints, state transitions, action execution endpoints, parser runs, agents, LLM/RAG calls, report generation, or score/verdict recalculation.

### DTO version

Workflow responses use a separate DTO schema version:

```json
{
  "schema_version": "workflow-state-v1"
}
```

This is distinct from `api_version`, `meta_contract_version`, analysis model versions, and future Decision Card versions.

### Workflow states

The PR32 vocabulary is stable:

- `new`
- `analysis_pending`
- `needs_review`
- `needs_data`
- `ready_for_work`
- `watchlist`
- `rejected`
- `report_ready`
- `closed`

PR32 only emits states that are safely derivable from current persisted listing, latest successful analysis, and compact human-review fields. `report_ready` is not emitted unless report readiness exists as safe persisted data; PR32 does not generate or prepare reports.

### State derivation order

The deterministic priority order is:

1. explicit human closed state;
2. explicit human rejected state;
3. explicit human watchlist state;
4. missing required listing data (`price`, `area_m2`) -> `needs_data`;
5. no latest successful analysis -> `analysis_pending`;
6. blocking review conditions such as unknown freshness or analysis verdict `review` -> `needs_review`;
7. latest successful analysis verdict `strong` with no PR32 blocking missing data -> `ready_for_work`;
8. fallback -> `needs_review`.

`ready_for_work` is verdict-based. It does not use a score threshold fallback, so a high score with verdict `review` remains `needs_review`. Verdict `weak` does not automatically become `rejected`; rejected/watchlist/closed states require explicit human-review/outcome data.

### State reasons

`state_reasons` are stable machine-readable codes, not display prose. Examples include:

- `missing_price`
- `missing_area_m2`
- `freshness_unknown`
- `latest_analysis_missing`
- `latest_analysis_verdict_review`
- `latest_analysis_verdict_strong`
- `human_review_rejected`
- `human_review_watchlist`
- `human_review_closed`
- `fallback_needs_review`

### Workflow actions

The PR32 action vocabulary is stable:

- `open_listing`
- `take_in_work`
- `request_data`
- `call_owner`
- `watchlist`
- `reject`
- `generate_memo`
- `generate_commercial_offer`
- `export_report`
- `close`

Action objects are metadata only:

```json
{
  "id": "open_listing",
  "business_applicable": true,
  "implemented": true,
  "available_now": true,
  "requires_write_endpoint": false,
  "reason": "listing_url_available"
}
```

`allowed_actions` are not backend authorization. Future write endpoints must independently enforce auth, permissions, and business rules. Frontend code should treat an action as executable only when `implemented=true` and `available_now=true`.

In PR32, every write/report action is non-executable:

```json
{
  "implemented": false,
  "available_now": false,
  "requires_write_endpoint": true
}
```

`open_listing` is the only action that can be `implemented=true` and `available_now=true`, and only when a safe public listing URL is present. PR32 does not expose phone/contact data, provider/debug URLs, webhook URLs, execution endpoints, or raw URL diagnostics through actions.

### Response shape

A successful workflow response uses the normal Admin API v1 envelope:

```json
{
  "ok": true,
  "data": {
    "schema_version": "workflow-state-v1",
    "listing_id": 123,
    "listing_external_id": "7520363836",
    "workflow_state": "needs_review",
    "allowed_actions": [],
    "blocked_actions": [],
    "state_reasons": ["latest_analysis_verdict_review"],
    "source_refs": {
      "listing_id": 123,
      "listing_external_id": "7520363836",
      "listing_analysis_id": 730,
      "human_review_id": null
    },
    "limitations": [
      "derived_read_only_state",
      "write_transitions_not_implemented_in_pr32",
      "decision_card_not_implemented_in_pr32"
    ]
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "..."
  }
}
```

`generated_at` remains envelope metadata only and is not part of deterministic workflow semantics.

### Security and side effects

PR32 workflow reads require the existing Admin API v1 read-key header. Query auth, cookie auth, Authorization/Bearer auth, and technical-key-only access are not accepted by Admin API v1.

The workflow layer is read-only. It does not create audit rows, human reviews, alerts, agent tasks, evidence rows, parser work, reports, or listing/analysis mutations. It does not read or expose raw `facts_json`, `result_json`, `payload_json`, `risks_json`, `questions_json`, `report_md`, provider payloads, debug HTML, request headers, cookies, auth keys, database URLs, Telegram tokens, SMTP secrets, LLM keys, or webhook URLs.

### Decision Card boundary

PR32 is not Decision Card v1. Workflow and decision-source responses intentionally do not include `decision_card`, `recommendation`, `primary_recommendation`, `headline`, `top_reasons`, `top_risks`, `next_steps`, missing-data ranking, readiness checklist, risk severity/visual attention, price position DTOs, memo generation, commercial-offer generation, or export/report readiness.
