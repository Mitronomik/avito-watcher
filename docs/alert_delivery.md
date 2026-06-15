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
