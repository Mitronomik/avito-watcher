# Human review tracking

PR18 adds a safe human decision and outcome layer for listing review. It records what a human decided after deterministic parsing, filtering, analysis, RAG context, and optional research.

## Core boundaries

- Human review does not change deterministic score/verdict.
- Human-confirmed facts are stored separately.
- Investment decisions are logged separately from deterministic analysis.
- No agent may mutate human review automatically in PR18.
- No automatic calibration is implemented in PR18.
- No admin UI is implemented in PR18.

PR18 does not change monitor cycles, alerts, market evidence, Google Sheets, search filters, agent tasks, scoring formulas, backtesting dashboards, or calibration loops.

## Tables

### `human_reviews`

Stores the current/latest human state for one listing context: lifecycle status, human verdict, next action, rejection reason, outcome status, watchlist flag, false-positive/false-negative flags, separate human-confirmed financial facts, reviewer, notes, JSON payload, and timestamps.

### `human_review_actions`

Append-only audit trail for review actions. `before_json` and `after_json` are compact snapshots of changed human-review fields only. They must not contain full listing rows, full analysis payloads, HTML, provider raw payloads, secrets, credentials, contact data, or unbounded text.

### `investment_decisions`

Durable investment/outcome events linked to a human review. These events record funnel decisions such as watchlist, send to expert, call owner, offer, deal done, or deal lost. They do not mutate deterministic analysis.

## Review context key

`review_context_key` is deterministic and prevents ambiguity when a listing appears in multiple searches or analysis contexts.

Default format:

```text
listing:{listing_external_id}:search:{search_job_id_or_none}:analysis:{listing_analysis_id_or_none}:context:{context_type}
```

The service generates it when it is not provided. It is unique in the database and is used by latest-review and context query helpers.

## Allowed values

- `review_status`: `new`, `needs_review`, `reviewed`, `closed`
- `human_verdict`: `interesting`, `neutral`, `not_interesting`, `false_positive`, `false_negative`, `needs_more_data`
- `next_action`: `open_listing`, `call_owner`, `request_documents`, `run_market_research`, `run_data_quality_review`, `send_to_expert`, `add_to_watchlist`, `reject`, `do_nothing`
- `rejected_reason`: `bad_price`, `bad_location`, `bad_area`, `bad_condition`, `stale_listing`, `wrong_object_type`, `duplicate`, `bad_market_evidence`, `low_yield`, `legal_risk`, `data_quality_issue`, `not_relevant`, `other`
- `outcome_status`: `not_started`, `contacted_owner`, `waiting_response`, `documents_requested`, `sent_to_expert`, `under_review`, `rejected_after_call`, `watchlist`, `deal_candidate`, `offer_made`, `deal_lost`, `deal_done`, `closed`
- `action_type`: `created`, `updated`, `status_changed`, `verdict_set`, `next_action_set`, `rejected`, `watchlisted`, `sent_to_expert`, `confirmed_facts_updated`, `notes_added`, `outcome_updated`, `investment_decision_recorded`, `closed`
- `decision_type`: `watchlist`, `reject`, `send_to_expert`, `call_owner`, `deal_candidate`, `offer`, `deal_done`, `deal_lost`
- `decision_status`: `proposed`, `approved`, `rejected`, `done`, `cancelled`

Unknown values fail closed in the service layer.

## Confirmed human facts

Human-confirmed purchase price, rent, area, opex, capex, vacancy, and source fields are stored on `human_reviews`. They do not overwrite parsed listing fields, researched market evidence, or `listing_analyses` facts.

## False positive / false negative semantics

`false_positive` means the deterministic system surfaced or scored a listing as worthy of attention, but a human later marked it as irrelevant, not interesting, or a bad match.

`false_negative` means the deterministic system did not surface a listing as strong/interesting enough, but a human later marked it as interesting or deal-candidate.

Both flags cannot be true at the same time. `human_verdict=false_positive` sets/allows `false_positive=true`; `human_verdict=false_negative` sets/allows `false_negative=true`. These flags never mutate deterministic score or verdict.

## Service usage

Use `HumanReviewService` from application code or tests:

```python
service.create_review(
    listing_external_id="7520000000",
    review_status="needs_review",
    human_verdict="interesting",
    next_action="call_owner",
    outcome_status="not_started",
    reviewer="human",
)

service.update_review(
    review_id,
    review_status="reviewed",
    outcome_status="sent_to_expert",
    next_action="send_to_expert",
    notes="Sent to expert for manual underwriting",
)

service.record_investment_decision(
    review_id,
    decision_type="send_to_expert",
    decision_status="done",
    actor="human",
)
```

## Future use

The data supports future backtesting, calibration, false-positive analysis, false-negative analysis, precision@strong, and conversion metrics. PR18 only persists the data and query helpers; it does not implement dashboards or automatic calibration.

## Production smoke plan

1. Run `alembic heads` and `alembic current`; confirm the latest head includes the human review tracking migration without hardcoding a revision in runbooks.
2. Run `curl -i http://127.0.0.1:8010/health`.
3. Capture baseline counts for `human_reviews`, `human_review_actions`, `investment_decisions`, `listings`, `listing_analyses`, `alerts_sent`, `market_research_runs`, `market_evidence_items`, `agent_tasks`, `knowledge_notes`, `listing_enrichments`, `listing_detail_snapshots`, and `search_jobs`.
4. Create a smoke review for `pr18-smoke-2026-06-14-listing` with `needs_review`, `interesting`, `call_owner`, `not_started`, reviewer `production-smoke`, and confirmed rent/purchase facts.
5. Assert only `human_reviews` and `human_review_actions` changed.
6. Update the review to `reviewed`, `sent_to_expert`, `send_to_expert`; assert the same review row updated, one action was added, and no unrelated tables changed.
7. Record an investment decision `send_to_expert` / `done`; assert `investment_decisions +1`, one `investment_decision_recorded` action, and no unrelated side effects.
8. Cleanup smoke rows by smoke prefix from `investment_decisions`, `human_review_actions`, and `human_reviews`; delete a temporary listing only if one was created.
9. Verify smoke counts are zero after cleanup.
