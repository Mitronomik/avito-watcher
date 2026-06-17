# Admin API v1

PR29 adds a small, read-only JSON API foundation for future admin/frontend clients. It does not replace the existing server-rendered `/admin` pages.

## Prefix and scope

All routes live under:

```text
/api/admin/v1
```

PR29 includes only:

- `GET /api/admin/v1/status`
- `GET /api/admin/v1/meta`

PR29 does not implement listing/review/evidence endpoints. PR29 does not implement decision cards. PR29 does not implement workflow state. PR29 does not implement technical actions. PR29 does not implement PR30 meta registry, capability matrix, permissions, enums, labels, roles, or error catalog. PR29 does not replace server-rendered `/admin` pages. PR29 does not change CORS.

## Authentication

Admin API v1 uses the existing centralized admin read key validation and the existing header:

```text
X-API-Key: <ADMIN_UI_READ_KEY>
```

There is no new auth key, secret, token, cookie, session, JWT, OAuth, or bearer-token transport. Query-string authentication is not accepted for this API, even when legacy HTML admin query-key compatibility is enabled.

If the read key is not configured, requests fail closed.

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

Initial stable error codes are `unauthorized`, `forbidden`, `not_found`, `validation_error`, `pagination_limit_exceeded`, and `internal_error`. Error handling is scoped to `/api/admin/v1` and does not change the existing `/admin` HTML route behavior.

## Pagination contract

Reusable helpers use bounded `limit` and `offset` values:

- default limit: `50`
- maximum limit: `100`
- default offset: `0`
- negative values are rejected
- too-large limits are rejected deterministically

When used by future list endpoints, pagination metadata should be included in response `meta`:

```json
{
  "pagination": {
    "limit": 50,
    "offset": 0,
    "has_more": false
  }
}
```

PR29 does not add a public list endpoint just to exercise pagination.

## Ordering contract

Reusable ordering helpers require an explicit allowlist of sortable fields. Unknown fields and invalid directions are rejected. Directions are limited to `asc` and `desc`, and defaults are deterministic.

PR29 does not expose ordering through a public domain endpoint.

## Redaction policy

The API-boundary redaction helper recursively redacts secret-bearing keys in response payloads without mutating the original object. Matching is case-insensitive and includes keys such as `api_key`, `token`, `secret`, `password`, `authorization`, `cookie`, `x-api-key`, admin key names, webhook secrets, SMTP passwords, LLM/OpenAI keys, Telegram tokens, and `user_content_key`.

URL query parameters such as `token`, `secret`, `key`, `api_key`, `signature`, `sig`, and `user_content_key` are redacted.

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

## `GET /api/admin/v1/meta`

Purpose: minimal API smoke/self-description, not the PR30 registry or capability matrix.

Example response:

```json
{
  "ok": true,
  "data": {
    "api_version": "admin-v1",
    "service": "avito-watcher",
    "status": "ok"
  },
  "meta": {
    "api_version": "admin-v1",
    "generated_at": "2026-06-17T00:00:00+00:00"
  }
}
```

The endpoint does not include capability-style fields, a permission registry, enum registry, label registry, role matrix, workflow actions, UI labels, or a full error catalog.

## Non-goals

- No UI or frontend.
- No SPA or templates.
- No listing/review/evidence domain-heavy endpoints.
- No decision card.
- No workflow state or allowed actions.
- No technical admin actions or capability matrix.
- No scoring, alert, parser, market evidence, source quality, or sale/cap-rate changes.
- No migrations.
- No CORS changes.
