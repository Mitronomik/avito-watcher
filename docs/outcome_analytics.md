# Human outcome analytics read model (PR18b)

PR18b adds a deterministic, read-only analytics foundation on top of PR18 human decision logging. PR18 created `human_reviews`, `human_review_actions`, and `investment_decisions`; PR18b aggregates those rows so future UI, StrategyAgent, backtesting, calibration, and reporting work can compare deterministic outputs with human outcomes.

PR18b is not PR19: the Admin UI for searches, analyses, agents, evidence, and reviews remains future scope. PR18b is also not PR35: the Backtesting dashboard remains future scope.

## Read-only guarantee

PR18b is a read-only analytics foundation:

- PR18b does not change deterministic score/verdict.
- PR18b does not calibrate thresholds.
- PR18b does not mutate filters/searches/alerts.
- PR18b does not mutate human reviews.
- PR18b does not mutate listings, listing analyses, market evidence, agent tasks, knowledge notes, enrichments, snapshots, or search jobs.
- PR18b does not call LLMs or external services.
- PR18b only runs SELECT-style repository queries and returns structured DTOs.

## Request and period semantics

`OutcomeAnalyticsRequest` controls the read model. It validates bounded filters, enum-like values, `period_days` from 1 to 365, and `max_examples_per_section` from 0 to 50. The service captures `as_of` once at the boundary and uses that single timestamp for the whole report.

The period is inclusive:

```text
period_start = as_of - period_days
period_end = as_of
include rows where event_at >= period_start and event_at <= period_end
```

Human review date basis:

- `coalesced`: `reviewed_at` or `updated_at` or `created_at`.
- `reviewed_at`: `reviewed_at` only.
- `updated_at`: `updated_at` only.
- `created_at`: `created_at` only.

If a selected non-coalesced field is null, the row is excluded by that date-basis filter. Investment decisions always use `decided_at` or `updated_at` or `created_at`.

## Counting units

Report sections state their units and do not mix them silently:

- `review_count` / review stats count `human_reviews` rows.
- `review_context_count` counts distinct `review_context_key` values.
- `reviewed_listing_count` counts distinct `listing_external_id` values.
- `decision_count` / decision stats count `investment_decisions` rows.
- `linked_analysis_count` counts reviews with an explicit `listing_analysis_id` that joins to a `listing_analyses` row.

Multiple reviews for the same listing increase review counts but do not inflate distinct listing counts.

## Explicit and derived outcome signals

PR18b separates explicit labels from derived unique signal counts.

Explicit positive review signals include:

- `human_verdict = interesting`;
- `watchlist = true`;
- `outcome_status in {watchlist, sent_to_expert, deal_candidate, offer_made, deal_done}`.

Explicit negative review signals include:

- `human_verdict = not_interesting`;
- `human_verdict = false_positive`;
- `false_positive = true`;
- `outcome_status in {rejected_after_call, deal_lost}`.

`human_positive_signal_count` and `human_negative_signal_count` are derived unique review counts. A single review with multiple positive signals is counted once in the unique positive count. A single review with multiple negative signals is counted once in the unique negative count.

`closed` is counted in outcome status distribution but is not automatically treated as a negative signal.

## False positives and false negatives

False positives are counted only when explicit:

- `human_verdict = false_positive`; or
- `false_positive = true`.

False negatives are counted only when explicit:

- `human_verdict = false_negative`; or
- `false_negative = true`.

`not_interesting` is not inferred as a false positive.

## Analysis alignment and score buckets

Analysis alignment is explicit-only in PR18b v0:

```text
human_reviews.listing_analysis_id -> listing_analyses.id
```

PR18b does not infer the latest analysis by `listing_external_id`, because later analyses may use different configuration, hashes, profiles, or facts. Alignment reports observed counts by deterministic verdict, profile, rent source, and market evidence usage when those fields are present.

Score buckets are fixed and deterministic:

- `0-39`
- `40-59`
- `60-74`
- `75-89`
- `90-100`
- `unknown`

Buckets are not tuned dynamically and do not change score/verdict behavior.

## Risk flag aggregation

Risk flag stats use production-shaped `listing_analyses.risks_json`:

```json
{"flags": ["missing_area", "stale_publication"], "items": []}
```

`extract_risk_flags()` uses non-empty string values from `risks_json["flags"]` when it is a list, ignores non-strings, ignores `items`, deduplicates flags per analysis row, and sorts deterministically. Legacy fallback is used only when `flags` is not a list.

Risk flag stats report observed outcome counts only. PR18b makes no recommendations and applies no calibration.

## Search stats

Search-level stats use only explicit `human_reviews.search_job_id`. PR18b does not fallback through `ListingSearchMatch`, because fallback can duplicate rows and may not match the review context.

Each search group reports review counts, distinct reviewed listing counts, human outcome counts, linked analysis counts, average linked score, score bucket distribution, and top risk flags.

Reviews without `search_job_id` are grouped as no-search/unlinked search stats.

## Examples and limits

Bounded examples support explainability without large payloads or full notes. Example sections include explicit false positives, explicit false negatives, high-score rejected reviews, low-score interesting reviews, sent-to-expert reviews, deal candidates, and completed deals.

Examples include listing/review identifiers, explicit search/analysis IDs when present, score/verdict when linked, review status, human verdict, outcome status, boolean flags, timestamps, review context key, and a truncated short title.

Examples are sorted by the repository's deterministic review ordering and capped by `max_examples_per_section`.

## Hashes

`request_hash` is a stable hash of the normalized request, captured `as_of`, include flags, filters, and report schema version.

`stats_snapshot_hash` is a stable hash of the deterministic scoped report snapshot. For the same selected DB state and same request it remains stable; when relevant selected data changes it changes. No database storage is required.

## Limitations and non-goals

PR18b does not implement:

- PR19 Admin UI;
- PR35 Backtesting dashboard;
- automatic calibration;
- score formula or threshold changes;
- filter or search mutation;
- StrategyAgent changes;
- weekly report changes;
- agent automatic actions;
- Google Sheets changes;
- alert delivery changes;
- API endpoints;
- scheduler/background report generation;
- dashboard/report generation.

## Production smoke plan

1. No migration is expected. Check `alembic heads` and `alembic current`; the current head should remain unchanged from PR18.
2. Check health with `curl -i http://127.0.0.1:8010/health`.
3. Capture baseline row counts for `human_reviews`, `human_review_actions`, `investment_decisions`, `listings`, `listing_analyses`, `alerts_sent`, `market_research_runs`, `market_evidence_items`, `agent_tasks`, `knowledge_notes`, `listing_enrichments`, `listing_detail_snapshots`, and `search_jobs`.
4. Create temporary smoke rows manually using PR18 service or direct SQL with prefix `pr18b-smoke-2026-06-14`: one interesting review, one false-positive review, one sent-to-expert review, and one approved investment decision.
5. Run the outcome analytics service for `period_days=7` using a short one-off invocation inside the app container.
6. Assert that the report has `request_hash`, `stats_snapshot_hash`, review counts, distinct listing counts, human verdict counts, outcome status counts, decision counts, score bucket stats when linked analysis exists, risk flag stats when linked analysis exists, and bounded examples.
7. Assert no unrelated row counts changed and no commit was required.
8. Cleanup smoke rows.
9. Confirm smoke counts for `human_reviews`, `human_review_actions`, and `investment_decisions` are zero.
