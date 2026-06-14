# PR19b - Listing detail human review workflow production smoke

Date: 2026-06-14
Environment: production (`avito-watcher-prod`)
Repository: `Mitronomik/avito-watcher`
Merged PR: #182 - `Add listing detail human review workflow`
Merge commit: `2805058d559fb69968338beb49f3de33247cb2f7`

## Status

```text
PR19b deploy: OK
PR19b listing detail GET smoke: OK
PR19b human review create POST smoke: OK
PR19b human review update POST smoke: OK
PR19b forbidden mutation boundary: OK
PR19b cleanup: OK
PR19b worker restart: OK
PR19b production smoke: CLOSED
```

## Scope

PR19b added the operator-facing listing detail human review workflow:

```text
/admin/listings/{listing_id}
POST /admin/listings/{listing_id}/human-review
operator-facing listing detail page
latest successful deterministic analysis display
human review create/update form
admin write key support
safe source URL rendering
stable admin_listing_detail review context
PR18 HumanReviewService-backed writes
```

## Non-goals

This handoff is docs-only and records the completed production smoke. It does not change:

```text
code
tests
migrations
config
admin UI runtime behavior
worker behavior
scoring
agents
evidence
alerts
services
```

PR19b smoke did not introduce scoring, verdict, listing, analysis, alert, agent, evidence, or search mutations.

## Deployment

Deployment evidence:

```text
app image built successfully
worker image built successfully
app container started
postgres and redis healthy
```

## Alembic

Both `heads` and `current` showed:

```text
0014_human_review_tracking (head)
```

No new migration was introduced by PR19b.

## Health

Request:

```text
GET /health
```

Result:

```http
HTTP/1.1 200 OK
{"status":"ok"}
```

## Admin key configuration

During smoke:

```env
ADMIN_UI_ENABLED=true
ADMIN_UI_ALLOW_QUERY_API_KEY=false
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
```

The initial smoke showed only `ADMIN_UI_READ_KEY`, so `ADMIN_WRITE_KEY` initially fell back to the read key. After smoke, keys were rotated and separated:

```env
ADMIN_UI_READ_KEY=<rotated read key>
ADMIN_UI_WRITE_KEY=<separate rotated write key>
```

Actual key values must not be pasted into docs or chat logs. The previously exposed read key was rotated after smoke. Production should keep `ADMIN_UI_READ_KEY` and `ADMIN_UI_WRITE_KEY` different.

## Smoke listing

The smoke selected a listing with successful analysis:

```text
LISTING_ID=30
listing_external_id=8147836490
latest successful listing_analysis_id=730
profile=commercial_rent
analysis_status=success
score=50.0
verdict=review
```

## GET listing detail smoke

Request:

```text
GET /admin/listings/30
auth: X-API-Key
```

Result:

```text
detail=200
```

The page showed:

```text
Объявление
Последний успешный детерминированный анализ
Human review
Сохранить решение
```

Before POST, no operator decision was saved:

```text
Решение оператора ещё не сохранено.
```

## Baseline SQL counts

Baseline SQL counts before POST:

```text
listings = 1522
listing_analyses = 730
alerts_sent = 2864
search_jobs = 2
agent_tasks = 2
market_research_runs = 0
market_evidence_items = 0
knowledge_notes = 0
listing_enrichments = 0
listing_detail_snapshots = 0
investment_decisions = 0
human_reviews = 0
human_review_actions = 0
```

## POST create human review smoke

Request:

```text
POST /admin/listings/30/human-review
auth: X-API-Key
human_verdict=interesting
outcome_status=watchlist
next_action=call_owner
watchlist=on
notes=PR19b production smoke - create human review
```

Result:

```http
HTTP/1.1 303 See Other
location: /admin/listings/30?saved=1
```

Follow-up GET:

```text
GET /admin/listings/30?saved=1
detail_after=200
Решение сохранено.
```

The page showed current human review:

```text
review id = 4
review_status = reviewed
human_verdict = interesting
outcome_status = watchlist
watchlist = true
next_action = call_owner
listing_analysis_id = 730
review_context_key = listing:8147836490:search:none:analysis:730:context:admin_listing_detail
```

## Post-create SQL counts

After create POST:

```text
listings = 1522
listing_analyses = 730
alerts_sent = 2864
search_jobs = 2
agent_tasks = 2
market_research_runs = 0
market_evidence_items = 0
knowledge_notes = 0
listing_enrichments = 0
listing_detail_snapshots = 0
investment_decisions = 0
human_reviews = 1
human_review_actions = 1
```

Conclusion:

```text
Only human_reviews and human_review_actions changed.
Forbidden tables did not change.
No investment_decisions row was created.
```

## Created review/action evidence

Created review:

```text
id = 4
listing_id = 30
listing_external_id = 8147836490
listing_analysis_id = 730
review_context_key = listing:8147836490:search:none:analysis:730:context:admin_listing_detail
review_status = reviewed
human_verdict = interesting
outcome_status = watchlist
watchlist = true
next_action = call_owner
notes = PR19b production smoke - create human review
```

Created action:

```text
id = 7
human_review_id = 4
action_type = created
note = PR19b production smoke - create human review
created_at = 2026-06-14 20:04:35.458587
```

## POST update human review smoke

Request:

```text
POST /admin/listings/30/human-review
auth: X-API-Key
human_verdict=interesting
outcome_status=sent_to_expert
next_action=send_to_expert
watchlist=on
notes=PR19b production smoke - update human review
```

Result:

```http
HTTP/1.1 303 See Other
location: /admin/listings/30?saved=1
```

No duplicate review was created:

```text
smoke_reviews = 1
```

Updated review remained the same row and context:

```text
id = 4
listing_id = 30
listing_analysis_id = 730
review_context_key = listing:8147836490:search:none:analysis:730:context:admin_listing_detail
human_verdict = interesting
outcome_status = sent_to_expert
next_action = send_to_expert
notes = PR19b production smoke - update human review
```

## No-duplicate review check

The update POST reused the existing smoke review row:

```text
smoke_reviews = 1
human_reviews.id = 4
review_context_key = listing:8147836490:search:none:analysis:730:context:admin_listing_detail
```

## Cleanup

Before cleanup:

```text
human_reviews smoke id = 4
```

Cleanup deleted:

```text
DELETE 2 from human_review_actions
DELETE 1 from human_reviews
```

Post-cleanup:

```text
remaining_reviews = 0
remaining_actions = 0
```

## Worker restart/logs

Worker was restarted after cleanup:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Result:

```text
redis healthy
postgres healthy
worker started
```

Worker logs showed startup diagnostics and the known pre-existing warning:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
```

This warning was not introduced by PR19b and is existing runtime/environment behavior.

## Security follow-up

Actual key values must not be pasted into docs or chat logs. The previously exposed read key was rotated after smoke.

Production should keep:

```text
ADMIN_UI_READ_KEY and ADMIN_UI_WRITE_KEY different
ADMIN_UI_ALLOW_QUERY_API_KEY=false
ADMIN_UI_TECHNICAL_OPS_ENABLED=false
```

## Verdict

```text
PR19b production smoke: CLOSED

The operator can open a listing detail page, see the latest successful deterministic analysis, create a human review, update the same human review without duplication, and clean up smoke rows.

The only write side effects during POST were in PR18 human review tables:
- human_reviews
- human_review_actions

Forbidden tables did not change:
- listings
- listing_analyses
- alerts_sent
- search_jobs
- agent_tasks
- market_research_runs
- market_evidence_items
- knowledge_notes
- listing_enrichments
- listing_detail_snapshots
- investment_decisions

No scoring, verdict, listing, analysis, alert, agent, evidence, or search mutation was observed.
```

## Next step

```text
PR19c - Read-only evidence, agents and outcome analytics pages
```

PR19c should remain read-only and must not trigger agents, mutate evidence, calibrate scoring, or change score/verdict.
