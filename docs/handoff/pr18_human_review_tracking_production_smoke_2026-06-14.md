# PR18 — Human review tracking production smoke

Date: 2026-06-14  
Environment: `avito-watcher-prod`  
Repository: `Mitronomik/avito-watcher`  
Branch deployed: `main`  
Merge commit deployed: `a6173e0aee88c37a87aa9bcb356ad8a04c570f4c`  
PR: `#174 — Add human review tracking`  
Status: `CLOSED ✅`

---

## 1. Purpose

PR18 starts the post-PR17 outcome-feedback phase for `avito-watcher`.

Before PR18 the system could:

```text
find listings
clean/filter them
run deterministic analysis
use market evidence
explain/recommend via agents
produce weekly strategy reports
```

After PR18 the system can also persist what a human decided about a listing:

```text
human review status
human verdict
next action
outcome status
confirmed facts
review notes
investment decision record
action audit trail
```

This is the foundation for future:

```text
backtesting
calibration
false-positive analysis
false-negative analysis
team workflows
admin UI
human-reviewed investment memo
```

PR18 does **not** change deterministic scoring or verdicts.

---

## 2. Architectural boundary

PR18 follows the accepted project principle:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates market assumptions.
Human approves action.
Outcomes calibrate the system.
```

The PR adds human-outcome memory only.

Allowed persistence in PR18:

```text
human_reviews
human_review_actions
investment_decisions
```

Explicitly not changed by PR18:

```text
listings
listing_analyses.score
listing_analyses.verdict
alerts_sent
market_research_runs
market_evidence_items
agent_tasks
knowledge_notes
listing_enrichments
listing_detail_snapshots
search_jobs
filters_json
```

Human-confirmed facts are stored separately from parsed/researched facts.

---

## 3. Deployed commit

Production checkout was updated from PR17 to PR18:

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

Updating 6672991..a6173e0
Fast-forward
...
a6173e0 (HEAD -> main, origin/main, origin/HEAD) Add human review tracking (#174)
```

Files added by PR18 included:

```text
alembic/versions/0014_human_review_tracking.py
app/models/human_review.py
app/repositories/human_reviews.py
app/schemas/human_reviews.py
app/services/human_reviews.py
docs/human_reviews.md
tests/test_human_reviews.py
```

The pull also brought already-merged docs artifacts, including:

```text
docs/handoff/pr17_weekly_strategy_agent_production_smoke_2026-06-14.md
docs/roadmap/amend_controlled_avito_comparable_source_provider.md
```

The roadmap doc is a documentation artifact only and had no runtime effect in this smoke.

---

## 4. Build / deploy

Commands executed:

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
Image deploy-worker Built
Image deploy-app Built
```

Result:

```text
app image built ✅
worker image built ✅
```

---

## 5. Alembic migration

PR18 introduced migration:

```text
0014_human_review_tracking
```

Commands executed:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic heads

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic upgrade head

docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app alembic current
```

Observed:

```text
0014_human_review_tracking (head)
INFO  [alembic.runtime.migration] Running upgrade 0013_market_evidence_storage -> 0014_human_review_tracking, human review tracking
0014_human_review_tracking (head)
```

Result:

```text
alembic heads: 0014_human_review_tracking ✅
alembic upgrade head: OK ✅
alembic current: 0014_human_review_tracking ✅
```

---

## 6. Runtime startup

Commands executed:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d app

docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker up -d worker
```

Observed:

```text
Container deploy-postgres-1 Healthy
Container deploy-redis-1 Healthy
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
{"status":"ok"}
```

Result:

```text
app health OK ✅
worker started ✅
```

---

## 7. Worker logs

Command:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml --profile worker logs -f worker --tail=200
```

Observed:

```text
WARNING __main__ PROXY_URLS not set — running without proxies (likely blocked by Avito)
INFO __main__ monitor worker runtime diagnostics: {...}
INFO app.parsers.avito_parser avito_parser.end_cycle stats={'engine_used': 'camoufox', ...}
INFO app.services.monitor_service monitor_service.cycle_summary searches_processed=1 ...
INFO __main__ monitor cycle completed
```

No startup errors were observed:

```text
no Traceback ✅
no OperationalError ✅
no UndefinedTable ✅
worker completed monitor cycle ✅
```

The `PROXY_URLS not set` warning is pre-existing operational context and not introduced by PR18.

---

## 8. Production smoke goal

The smoke validated the new human-review persistence path only:

```text
create human review
append created action
update review
append update action
record investment decision
append decision action
verify unrelated tables unchanged
cleanup all smoke rows
```

Smoke prefix:

```text
pr18-smoke-2026-06-14
```

Smoke listing external id:

```text
pr18-smoke-2026-06-14-listing
```

---

## 9. Production smoke command

Executed a temporary Python smoke through the app container:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml run --rm \
  -e PYTHONPATH=/app \
  app python - <<'PY'
# PR18 production smoke script
# - create review
# - update review
# - record investment decision
# - assert no unrelated side effects
# - cleanup
PY
```

The smoke used:

```python
HumanReviewService.create_review(...)
HumanReviewService.update_review(...)
HumanReviewService.record_investment_decision(...)
```

---

## 10. Smoke create review

Smoke created a human review with:

```text
listing_external_id = pr18-smoke-2026-06-14-listing
review_status = needs_review
human_verdict = interesting
next_action = call_owner
reviewer = production-smoke
notes = PR18 smoke create
confirmed_monthly_rent_rub = 120000
confirmed_purchase_price_rub = 12000000
confirmed_area_m2 = 50
confirmed_opex_ratio = 0.15
confirmed_vacancy_rate = 0.08
payload_json = {"smoke": "pr18-create"}
```

Observed after create:

```text
review_id 1
review_context_key listing:pr18-smoke-2026-06-14-listing:search:none:analysis:none:context:listing
human_verdict interesting
```

The review row was created and one action was appended.

Result:

```text
human review create passed ✅
created action appended ✅
reviewed_at populated ✅
review_context_key generated ✅
```

---

## 11. Smoke update review

Smoke updated the same review to:

```text
review_status = reviewed
outcome_status = sent_to_expert
next_action = send_to_expert
reviewer = production-smoke
notes = PR18 smoke update
payload_json = {"smoke": "pr18-update"}
```

Observed:

```text
review_status reviewed
outcome_status sent_to_expert
next_action send_to_expert
```

The same review row was updated and another action was appended.

Result:

```text
human review update passed ✅
update action appended ✅
outcome_status persisted ✅
next_action persisted ✅
```

---

## 12. Smoke investment decision

Smoke recorded an investment decision linked to the review:

```text
decision_type = send_to_expert
decision_status = approved
decision_reason = PR18 production smoke
amount_rub = 12000000
expected_monthly_rent_rub = 120000
actor = production-smoke
note = PR18 smoke investment decision
payload_json = {"smoke": "pr18-decision"}
```

Observed:

```text
decision_id 1
decision_type send_to_expert
decision_status approved
```

The decision was created and an additional action was appended.

Result:

```text
investment decision create passed ✅
decision linked to review ✅
decision action appended ✅
```

---

## 13. Smoke output

Observed smoke output:

```text
PR18_SMOKE_OK
review_id 1
review_context_key listing:pr18-smoke-2026-06-14-listing:search:none:analysis:none:context:listing
review_status reviewed
human_verdict interesting
outcome_status sent_to_expert
next_action send_to_expert
decision_id 1
decision_type send_to_expert
decision_status approved
human_reviews_before 0
human_reviews_after_before_cleanup 1
human_actions_before 0
human_actions_after_before_cleanup 3
decisions_before 0
decisions_after_before_cleanup 1
listings_before 1513
listings_after 1513
analyses_before 730
analyses_after 730
alerts_before 2846
alerts_after 2846
tasks_before 2
tasks_after 2
runs_before 0
runs_after 0
items_before 0
items_after 0
PR18_SMOKE_REMAINING_REVIEWS 0
PR18_SMOKE_REMAINING_ACTIONS 0
PR18_SMOKE_REMAINING_DECISIONS 0
```

---

## 14. Side-effect check

Expected PR18 smoke deltas before cleanup:

```text
human_reviews +1
human_review_actions +3
investment_decisions +1
```

Observed:

```text
human_reviews_before 0
human_reviews_after_before_cleanup 1
human_actions_before 0
human_actions_after_before_cleanup 3
decisions_before 0
decisions_after_before_cleanup 1
```

Expected unrelated tables unchanged.

Observed:

```text
listings_before 1513
listings_after 1513

analyses_before 730
analyses_after 730

alerts_before 2846
alerts_after 2846

tasks_before 2
tasks_after 2

runs_before 0
runs_after 0

items_before 0
items_after 0
```

Result:

```text
listings unchanged ✅
listing_analyses unchanged ✅
alerts_sent unchanged ✅
agent_tasks unchanged ✅
market_research_runs unchanged ✅
market_evidence_items unchanged ✅
```

The smoke did not mutate deterministic scoring, alerts, agents, or evidence.

---

## 15. Cleanup

The smoke cleanup deleted temporary review/action/decision rows with prefix:

```text
pr18-smoke-2026-06-14%
```

Observed from smoke script:

```text
PR18_SMOKE_REMAINING_REVIEWS 0
PR18_SMOKE_REMAINING_ACTIONS 0
PR18_SMOKE_REMAINING_DECISIONS 0
```

---

## 16. Post-cleanup SQL check

Command executed:

```bash
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec postgres \
  sh -lc 'psql -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
select count(*) as pr18_smoke_reviews
from human_reviews
where listing_external_id like '\''pr18-smoke-2026-06-14%'\'';

select count(*) as pr18_smoke_actions
from human_review_actions
where human_review_id in (
  select id from human_reviews
  where listing_external_id like '\''pr18-smoke-2026-06-14%'\''
);

select count(*) as pr18_smoke_decisions
from investment_decisions
where listing_external_id like '\''pr18-smoke-2026-06-14%'\'';
"'
```

Observed:

```text
pr18_smoke_reviews
------------------
0

pr18_smoke_actions
------------------
0

pr18_smoke_decisions
--------------------
0
```

Result:

```text
post-cleanup reviews = 0 ✅
post-cleanup actions = 0 ✅
post-cleanup decisions = 0 ✅
```

---

## 17. Final production status

```text
PR18 production deploy: passed ✅
PR18 Alembic migration: passed ✅
PR18 app health: passed ✅
PR18 worker startup: passed ✅
PR18 human review create: passed ✅
PR18 human review update: passed ✅
PR18 investment decision logging: passed ✅
PR18 audit action trail: passed ✅
PR18 no deterministic scoring mutation: passed ✅
PR18 no alert mutation: passed ✅
PR18 no market evidence mutation: passed ✅
PR18 no agent task mutation: passed ✅
PR18 cleanup: passed ✅
PR18 post-cleanup SQL: passed ✅
```

Final verdict:

```text
PR18 production smoke: CLOSED ✅
```

---

## 18. Operational notes

PR18 is now safe for internal/manual use.

What is now possible:

```text
record human review state
record human verdict
record next action
record sent-to-expert / watchlist / deal-candidate outcome via outcome layer
record confirmed rent/price/area/opex/capex/vacancy facts
record investment decisions
keep action audit trail
```

What remains intentionally not implemented:

```text
admin UI
team workflow queues
role-based access control
alert retry dashboard
backtesting dashboard
calibration loop
automatic threshold mutation
automatic filter mutation
automatic score/verdict mutation
agent-driven review mutation
```

---

## 19. Next step

PR18 closes the first human-outcome feedback layer.

Recommended next roadmap step:

```text
PR19 — Admin UI for searches, analyses, agents, evidence and reviews
```

Reason:

```text
Human review tracking now exists in the database/service layer,
but humans still need a practical UI/workflow to use it without SQL/scripts.
```

Alternative if prioritizing operations before UI:

```text
PR20 — Alert delivery retry dashboard / outbox v1
```

But the natural continuation after PR18 is PR19 because the new review layer needs a human-facing workflow.

---

## 20. Status

```text
PR18 — Human decision logging + outcome tracking: CLOSED ✅
Production deploy: done ✅
Production smoke: closed ✅
Cleanup: done ✅
Handoff: this document ✅
```
