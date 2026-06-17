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

The endpoint remains backward-compatible with PR29 by keeping `api_version`, `service`, `status`, and the existing `capabilities.read_api`, `capabilities.technical_actions`, and `capabilities.domain_endpoints` fields. PR30 adds `meta_contract_version`, `roles`, `permissions`, `enums`, `labels`, `legacy_labels`, `errors`, and expanded safe capabilities.

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
      "technical_actions": false,
      "technical_api_actions": false,
      "domain_endpoints": false,
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

The enum registry exposes stable display values grounded in code constants where available, including analysis verdicts, human review statuses/actions/outcomes, agent task statuses, source types, verification statuses, and risk levels. Every enum group includes an `unknown_value` fallback so frontend clients can safely render unexpected values.

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
