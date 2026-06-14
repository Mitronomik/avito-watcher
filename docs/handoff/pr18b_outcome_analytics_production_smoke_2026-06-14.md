# PR18b — Human outcome analytics production smoke handoff

Date: 2026-06-14

Status: **CLOSED ✅**

Production environment: `avito-watcher-prod`

Canonical roadmap name:

```text
PR18b — Human outcome analytics / backtesting read model
```

GitHub implementation PR:

```text
PR #176 — Add human outcome analytics read model
merge_commit_sha: 0146a3f811f5e0f9f3a45e2be67f62e69787c7af
```

Naming cleanup:

```text
PR18c — Normalize outcome analytics naming to PR18b
```

Note: the implementation and first production smoke were originally recorded under the temporary `PR19` label. The canonical roadmap label is now `PR18b`; the next full roadmap item can remain available for the real PR19 scope.

---

## 1. Runtime scope

```text
read-only analytics service only
new DB migrations: none
new scheduler: none
new agent integration: none
new alert delivery: none
new Google Sheets integration: none
LLM/external calls: none
```

PR18b is safe because it only reads PR18 human outcome tables and deterministic analysis rows.

---

## 2. What PR18b added

PR18b added a read-only outcome analytics / backtesting foundation on top of PR18 human review data.

New files:

```text
app/repositories/outcome_analytics.py
app/schemas/outcome_analytics.py
app/services/outcome_analytics.py
docs/outcome_analytics.md
tests/test_outcome_analytics.py
```

The service reads:

```text
human_reviews
human_review_actions
investment_decisions
listing_analyses, only for explicit listing_analysis_id alignment
```

The service reports:

```text
review totals
human verdict counts
outcome status counts
false positive / false negative counts
investment decision counts
score bucket stats
risk flag stats
search-level stats
bounded examples
request_hash
stats_snapshot_hash
```

Canonical report version after PR18c naming cleanup:

```text
pr18b-outcome-analytics-v1
```

The original production smoke before naming cleanup printed:

```text
pr19-outcome-analytics-v1
```

That old value was a naming artifact only; aggregation behavior did not change.

---

## 3. Non-goals confirmed

PR18b does **not** implement:

```text
automatic calibration
score formula changes
threshold changes
filter mutation
search mutation
admin UI
dashboard
StrategyAgent changes
weekly report changes
agent automatic actions
Google Sheets changes
alert delivery changes
comp quality scoring
adjusted comps
DCF/scenario/financing
investment memo generation
```

PR18b must remain a read-only analytical layer.

---

## 4. Deploy evidence

Production deploy was performed from `main`.

Command:

```bash
cd ~/apps/avito-watcher

git status
git checkout main
git pull --ff-only origin main
git log -1 --oneline
```

Observed:

```text
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean
Already on 'main'
Your branch is up to date with 'origin/main'.
Updating a6173e0..0146a3f
Fast-forward
0146a3f (HEAD -> main, origin/main, origin/HEAD) Add human outcome analytics read model (#176)
```

Changed files pulled into production:

```text
app/repositories/outcome_analytics.py
app/schemas/outcome_analytics.py
app/services/outcome_analytics.py
docs/handoff/pr18_human_review_tracking_production_smoke_2026-06-14.md
docs/outcome_analytics.md
tests/test_outcome_analytics.py
```

Note: the PR18 production smoke handoff doc was also pulled as a docs-only artifact. Runtime impact: none.

---

## 5. Build evidence

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml config >/dev/null
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d postgres redis

docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker stop worker

docker compose --env-file .env -f deploy/docker-compose.prod.yml build app worker
```

Observed:

```text
Container deploy-redis-1 Running
Container deploy-postgres-1 Running
Container deploy-worker-1 Stopped
Image deploy-app Built
Image deploy-worker Built
```

Result:

```text
app image build: OK ✅
worker image build: OK ✅
```

---

## 6. Alembic evidence

PR18b added no migration. Production DB head remained PR18 migration `0014_human_review_tracking`.

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic heads

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current
```

Observed:

```text
0014_human_review_tracking (head)

INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0014_human_review_tracking (head)
```

Result:

```text
alembic heads: 0014_human_review_tracking ✅
alembic current: 0014_human_review_tracking ✅
new migrations: none ✅
```

---

## 7. App / worker start evidence

Commands:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Observed:

```text
Container deploy-redis-1 Healthy
Container deploy-postgres-1 Healthy
Container deploy-app-1 Started
Container deploy-worker-1 Started
```

Health check:

```bash
curl -i http://127.0.0.1:8010/health
```

Observed:

```text
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok"}
```

Worker log summary:

```text
PROXY_URLS not set — running without proxies (likely blocked by Avito)
monitor worker runtime diagnostics emitted
avito_parser.end_cycle stats emitted
monitor_service.cycle_summary searches_processed=0
monitor cycle completed
```

Result:

```text
app health: OK ✅
worker started: OK ✅
worker logs: clean ✅
Traceback: none ✅
OperationalError: none ✅
```

---

## 8. Production smoke purpose

The PR18b smoke verified that outcome analytics can read PR18 human review data and produce a deterministic report without mutating production tables.

Smoke flow:

```text
1. cleanup any old smoke rows
2. create temporary PR18 human review rows through HumanReviewService
3. create temporary investment decision through HumanReviewService
4. capture table counts after setup
5. run HumanOutcomeAnalyticsService.build_report(...)
6. assert report values
7. assert table counts are unchanged after analytics call
8. cleanup smoke rows
9. verify post-cleanup SQL count = 0
```

Smoke prefix used in the original production run:

```text
pr19-smoke-2026-06-14
```

Canonical prefix for any future rerun after PR18c:

```text
pr18b-smoke-2026-06-14
```

The smoke intentionally creates temporary human-review rows before the analytics call. The read-only guarantee applies to the PR18b analytics call itself, not to the setup/cleanup phase.

---

## 9. Production smoke output

Observed smoke output from the original production run:

```text
PR19_SMOKE_OK
report_version pr19-outcome-analytics-v1
request_hash 05abbfc8d771f5bec79056c0dcbf6269139daef3d3de99de28f0d28834c7a351
stats_snapshot_hash 85468240f333f05a5f12802a4734ddbe109d24e6a443f3485b769e8250a633b9
human_reviews_in_period 2
investment_decisions_in_period 1
linked_analysis_used True
linked_analysis_id 730
search_job_id 2
interesting_count 1
false_positive_count 1
explicit_false_positive_count 1
sent_to_expert_count 1
rejected_after_call_count 1
decision_send_to_expert_count 1
decision_approved_count 1
false_positive_examples 1
sent_to_expert_examples 1
human_reviews_after_setup 2
human_reviews_after_report 2
human_review_actions_after_setup 3
human_review_actions_after_report 3
investment_decisions_after_setup 1
investment_decisions_after_report 1
listings_after_setup 1517
listings_after_report 1517
listing_analyses_after_setup 730
listing_analyses_after_report 730
alerts_sent_after_setup 2854
alerts_sent_after_report 2854
market_research_runs_after_setup 0
market_research_runs_after_report 0
market_evidence_items_after_setup 0
market_evidence_items_after_report 0
agent_tasks_after_setup 2
agent_tasks_after_report 2
knowledge_notes_after_setup 0
knowledge_notes_after_report 0
listing_enrichments_after_setup 0
listing_enrichments_after_report 0
listing_detail_snapshots_after_setup 0
listing_detail_snapshots_after_report 0
search_jobs_after_setup 2
search_jobs_after_report 2
PR19_SMOKE_REMAINING_REVIEWS 0
PR19_SMOKE_REMAINING_ACTIONS 0
PR19_SMOKE_REMAINING_DECISIONS 0
```

Canonical interpretation after PR18c:

```text
PR18b smoke passed ✅
The old PR19 label in the raw output was a naming artifact ✅
Read-only analytics behavior passed ✅
```

Validated report values:

```text
report generated ✅
request_hash present ✅
stats_snapshot_hash present ✅
human_reviews_in_period = 2 ✅
investment_decisions_in_period = 1 ✅
linked analysis alignment used ✅
interesting_count = 1 ✅
false_positive_count = 1 ✅
explicit_false_positive_count = 1 ✅
sent_to_expert_count = 1 ✅
rejected_after_call_count = 1 ✅
decision_send_to_expert_count = 1 ✅
decision_approved_count = 1 ✅
false_positive_examples = 1 ✅
sent_to_expert_examples = 1 ✅
```

---

## 10. Read-only verification

Counts after setup and after analytics report generation were identical.

```text
human_reviews: 2 -> 2 ✅
human_review_actions: 3 -> 3 ✅
investment_decisions: 1 -> 1 ✅
listings: 1517 -> 1517 ✅
listing_analyses: 730 -> 730 ✅
alerts_sent: 2854 -> 2854 ✅
market_research_runs: 0 -> 0 ✅
market_evidence_items: 0 -> 0 ✅
agent_tasks: 2 -> 2 ✅
knowledge_notes: 0 -> 0 ✅
listing_enrichments: 0 -> 0 ✅
listing_detail_snapshots: 0 -> 0 ✅
search_jobs: 2 -> 2 ✅
```

Result:

```text
PR18b analytics DB mutation: none ✅
read-only guarantee: passed ✅
```

---

## 11. Cleanup verification

Smoke cleanup output:

```text
PR19_SMOKE_REMAINING_REVIEWS 0
PR19_SMOKE_REMAINING_ACTIONS 0
PR19_SMOKE_REMAINING_DECISIONS 0
```

Post-cleanup SQL:

```sql
select count(*) as pr19_smoke_reviews
from human_reviews
where listing_external_id like 'pr19-smoke-2026-06-14%';

select count(*) as pr19_smoke_actions
from human_review_actions
where human_review_id in (
  select id from human_reviews
  where listing_external_id like 'pr19-smoke-2026-06-14%'
);

select count(*) as pr19_smoke_decisions
from investment_decisions
where listing_external_id like 'pr19-smoke-2026-06-14%';
```

Observed:

```text
pr19_smoke_reviews
------------------
0

pr19_smoke_actions
------------------
0

pr19_smoke_decisions
--------------------
0
```

Result:

```text
smoke reviews cleanup: 0 ✅
smoke actions cleanup: 0 ✅
smoke decisions cleanup: 0 ✅
```

---

## 12. Final production status

```text
PR18b production deploy: done ✅
PR18b app/worker build: done ✅
Alembic unchanged at 0014: done ✅
App health: OK ✅
Worker logs: clean ✅
Outcome analytics report generation: passed ✅
Human verdict counts: passed ✅
Outcome status counts: passed ✅
Investment decision counts: passed ✅
Linked analysis alignment: passed ✅
Bounded examples: passed ✅
Read-only/no-side-effects: passed ✅
Cleanup: passed ✅
Post-cleanup SQL check: passed ✅

Status: CLOSED ✅
```

---

## 13. Operational notes

PR18b is safe to keep deployed because:

```text
it has no scheduler integration
it has no worker integration
it has no automatic report generation
it has no LLM/external calls
it has no DB writes inside analytics service
it has no score/verdict mutation
it has no search/filter mutation
it has no alert mutation
```

The service is currently a read-only foundation for future backtesting/reporting/admin workflow.

---

## 14. Next step

Recommended next roadmap step after PR18c naming cleanup:

```text
PR19 — Admin UI v0 for operations and human review
```

Initial PR19 scope should stay small:

```text
operator-facing read-only console
human review create/update form
outcome analytics readout
no automatic calibration
no score/verdict mutation
no StrategyAgent auto-action
```
