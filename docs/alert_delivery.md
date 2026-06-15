# Alert delivery

## Alert delivery attempts ledger (PR20a)

PR20a adds a durable `alert_delivery_attempts` ledger for observability of alert delivery outcomes. The existing `alerts_sent` table remains the success-only delivery dedupe table: successful channel deliveries create `alerts_sent` rows, while failed, skipped, and unknown outcomes do not.

The ledger records one row for every actual attempted pending listing/channel delivery. A pending channel is a configured/selected channel that is not already deduped by `alerts_sent` for the listing. Channels that are not configured, not selected, already deduped, or part of baseline/no-delivery paths do not create attempt rows.

Recorded statuses are:

- `success` when a channel returns `True`;
- `failed` when a channel raises an exception;
- `skipped` when a pending channel is attempted and returns `False`;
- `unknown` when a channel is missing at delivery time or returns an unexpected non-boolean result.

`attempt_count` is append-only ordinal metadata for the same `dedupe_key + channel`: the first recorded attempt is `1`, the second is `2`, and so on. PR20a does not use this field to retry delivery.

`payload_hash` stores a SHA-256 hash of the canonical delivery payload instead of storing the raw payload. The table does not store full message bodies, webhook URLs, provider credentials, API keys, Telegram tokens, SMTP credentials, cookies, authorization headers, or other notifier secrets.

Failed delivery errors are sanitized and truncated before being written to `last_error`. Sanitization removes obvious secret-bearing values and sensitive URL query parameter values. Tracebacks and raw notifier configuration are not stored.

Timestamp rules in PR20a are intentionally deterministic:

- `sent_at` is set only for `success` rows;
- `sent_at` is null for `failed`, `skipped`, and `unknown` rows;
- `next_retry_at` remains null for every PR20a row.

PR20a only makes delivery attempts observable. It does not add a dashboard, manual retry route, automatic retry scheduler, retry worker, or retry policy. PR20b/PR20c can build read dashboards and retry behavior on top of this table.

Baseline initialization still sends no alerts and creates no delivery attempts because no delivery is attempted during baseline.

## Production smoke plan

Production smoke should not force production parsing unless explicitly approved.

1. Deploy the migration.
2. Verify Alembic state:

   ```bash
   alembic heads
   alembic current
   ```

   Expected: a single Alembic head and the new migration applied.

3. Verify the table exists:

   ```sql
   select count(*) from alert_delivery_attempts;
   ```

4. Snapshot counts for:

   - `alerts_sent`
   - `alert_delivery_attempts`
   - `listings`
   - `listing_analyses`
   - `agent_tasks`
   - `market_research_runs`
   - `market_evidence_items`
   - `human_reviews`
   - `human_review_actions`
   - `investment_decisions`

5. Let the normal worker cycle process naturally, or use a controlled local/fake test. Do not force production parsing or failed delivery simulation unless explicitly approved.
6. Verify attempts are created only when delivery is actually attempted.
7. Verify a successful channel creates both an `alert_delivery_attempts` row with `status=success` and an `alerts_sent` row.
8. Verify failed/skipped/unknown outcomes do not create `alerts_sent`. In production, these may be validated through logs/tests rather than deliberately breaking real channels.
9. Check logs for exceptions and secret leaks.
10. Confirm there are no agent, research, scoring, or human-review side effects.

## Read-only delivery dashboard (PR20b)

PR20b adds read-only Admin UI observability for the PR20a `alert_delivery_attempts` ledger. It shows what PR20a recorded; it does not retry, schedule retry, mutate the outbox ledger, mutate `alerts_sent`, or add a migration.

Routes:

- `GET /admin/alerts` keeps the existing JSONL alert history and adds the **Попытки доставки уведомлений** delivery attempts section.
- `GET /admin/alerts/delivery-attempts/{attempt_id}` shows one safe delivery attempt detail page.

The dashboard defaults to `hours=168` and bounds the period filter to `1..720` hours. Recent rows are bounded by `limit`, default `50`, with a maximum visible delivery-attempt limit of `200`. Supported filters are `status` (`success`, `failed`, `skipped`, `unknown`), `channel` (max 32 chars), `listing_external_id` (max 128 chars), `dedupe_key` (max 255 chars), `search_job_id` (positive integer; available because the PR20a ledger schema includes it), `hours`, and `limit`. Invalid filters return HTTP 400 rather than falling back to an unbounded query.

The main dashboard shows summary cards/text for selected-period total attempts, all-time total attempts, per-status counts, observed channels, latest attempt timestamp, and whether live delivery has been observed. If no attempts exist, it renders the empty state: no delivery attempts have been observed yet, which is expected if no pending alerts were delivered after PR20a deployment.

The recent attempts table shows bounded safe fields only: id, created time, listing external id, channel, status, attempt count, `sent_at`, `next_retry_at`, search name, payload hash prefix, redacted/truncated last-error preview, matching `AlertSent` state, and a details link. Raw payloads are never stored or rendered. `last_error` is redacted and truncated again at render time, including obvious secret keys and sensitive URL query parameters.

The detail page shows safe scalar fields for a single `AlertDeliveryAttempt`, matching `AlertSent` presence, and a matching listing link when one exists. It does not show raw payload, secrets, retry controls, POST forms, manual actions, or technical controls.

Delivery invariant counters are visible on `/admin/alerts`; healthy values are `0` for every counter. Each counter counts `AlertDeliveryAttempt` rows, not distinct dedupe keys:

- `success_without_alert_sent`: `status = success` and no matching `AlertSent` exists.
- `non_success_with_alert_sent`: `status in failed/skipped/unknown` and a matching `AlertSent` exists.
- `success_missing_sent_at`: `status = success` and `sent_at is null`.
- `non_success_with_sent_at`: `status in failed/skipped/unknown` and `sent_at is not null`.
- `non_null_next_retry_at`: `next_retry_at is not null`; this should normally be zero in PR20a/PR20b because retry scheduling is not implemented.
- `bad_payload_hash_count`: `payload_hash` is null/empty or does not match `^[0-9a-f]{64}$`.

Matching `AlertSent` semantics are exact: same `dedupe_key`, same `listing_external_id`, and same `channel`. The dashboard does not infer fuzzy matches.

PR20b is intentionally not a health dashboard: it does not add worker heartbeat, parser health, queue lag, delivery-latency trends, SLA metrics, PR21 health-dashboard scope, or PR45 production uptime scope.

### PR20b production smoke plan

1. Pull current main and verify the deployed commit.
2. Run `alembic heads` and `alembic current`; no new PR20b migration is expected and the head should remain the current PR20a/main head.
3. Build/restart the app as needed; worker restart is not required unless deployment packaging requires it.
4. Check `/health`.
5. Snapshot counts for `alert_delivery_attempts`, `alerts_sent`, `listings`, `listing_analyses`, `search_jobs`, `agent_tasks`, `market_research_runs`, `market_evidence_items`, `knowledge_notes`, `listing_enrichments`, `listing_detail_snapshots`, `human_reviews`, `human_review_actions`, and `investment_decisions`.
6. Open `/admin/alerts` with the read key and verify the page works with an empty ledger or existing attempts.
7. Verify safe filters such as `limit=10`, `hours=168`, `status=failed`, plus invalid status/limit returning 400.
8. If an attempt exists, open `/admin/alerts/delivery-attempts/{attempt_id}`.
9. Verify invariant counters and unsupported POST routes (`POST /admin/alerts`, `POST /admin/alerts/delivery-attempts/{attempt_id}`) do not mutate state.
10. Snapshot the same DB counts and confirm they are unchanged.
11. Check logs for tracebacks, errors, and secret leakage. Do not trigger run-once, technical ops, manual retry, or automatic retry.

## PR20c manual delivery retry

PR20c adds a controlled manual retry action for one existing delivery attempt. It is not an automatic retry system: there is no scheduler, queue, retry daemon, retry-all action, `next_retry_at` policy engine, worker heartbeat, parser health, SLA metric, migration, or raw payload replay.

Operators open `GET /admin/alerts/delivery-attempts/{attempt_id}` and, only for eligible failed/skipped/unknown attempts, submit `POST /admin/alerts/delivery-attempts/{attempt_id}/retry`. The form is shown only when technical operations are enabled and requires `ADMIN_UI_TECHNICAL_WRITE_KEY` plus a visible typed confirmation of `retry_delivery_attempt_{attempt_id}`. Read keys and normal admin write keys cannot perform the retry.

Eligibility is intentionally narrow. The original attempt must have status `failed`, `skipped`, or `unknown`; the listing must still exist; `channel` and `dedupe_key` must be non-empty; the dedupe key must match the delivery convention `{channel}:new:{listing_external_id}`; and there must be no exact matching `AlertSent` row for the same dedupe key, listing external id, and channel. Successful attempts, including success-without-AlertSent invariant rows, are not repairable through PR20c.

Immediately before sending, the POST route rechecks the exact matching `AlertSent`. If it exists, no notifier is called and no delivery attempt is written. This last-moment check plus the existing `alerts_sent.dedupe_key` uniqueness is the small duplicate protection used by PR20c; no lock table or retry lock migration is added.

The retry targets exactly the original attempt channel. It does not recompute all pending channels, call the monitor cycle, parser, LLM, scoring, deterministic analysis, agents, market research, or human review. Manual retry regenerates the message and delivery payload from the current stored `Listing` row and safe existing alert builders. It is not a byte-for-byte replay, does not store raw payloads, and the new retry attempt receives its own payload hash.

A successful external retry records a new `alert_delivery_attempts(status=success)` row and creates `AlertSent` in the same DB session flow. Failed, skipped, unknown, or channel-not-configured outcomes record only a new delivery attempt row and do not create `AlertSent`. Manual retry rows are marked through `search_name` as `manual_retry` or `manual_retry:{original_search_name}` when the original attempt had a search name. Auth, confirmation, eligibility, and precondition failures are not delivery attempts and create no rows.

Production smoke should remain safe. Leave `ADMIN_UI_TECHNICAL_OPS_ENABLED=false`, open `/admin/alerts`, open a recent attempt detail page, confirm successful attempts do not render an active retry form, POST a retry while technical ops are disabled and confirm HTTP 403, then snapshot table counts before/after to confirm no mutation and review logs for secret leakage. Only with explicit operator approval should technical ops be enabled to retry one real failed/skipped/unknown attempt; after that, confirm exactly one attempt row was added, `AlertSent` was added only on success, no other channels were called, invariant counters remain healthy, and technical ops are disabled again.
