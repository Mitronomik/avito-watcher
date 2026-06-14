# PR14 - Manual market_research ResearchAgent production smoke

Date: 2026-06-14  
Environment: production  
Repository: Mitronomik/avito-watcher  
Task type: `market_research`

## Scope

PR14 added a manual ResearchAgent / market research shadow-mode task.

The canonical AgentTask type is:

```text
market_research

The agent is advisory only.

It performs:

manual task execution;
bounded query planning;
source-backed provider abstraction;
comparable candidate extraction into agent_tasks.result_json;
strict schema validation;
confidence and low-confidence human review recommendation.

It does not:

run from monitor cycle;
create tasks automatically;
call external network by default;
change deterministic score;
change verdict;
send alerts;
mutate filters;
mutate search jobs;
write RAG notes;
create knowledge_notes;
create listing_enrichments;
create market_research_runs;
create market_evidence_items;
create market RAG;
trigger reanalysis;
affect investment profiles.
Deployment

Production deploy completed.

Confirmed:

app image built
worker image built
app started
worker started

Alembic unchanged:

0012_listing_enrichments (head)

Health check:

HTTP/1.1 200 OK
Handler registration

Verified default AgentTask handlers:

market_research_registered True
handlers ['data_quality_agent', 'listing_detail_extraction', 'market_research', 'review_copilot']
Baseline

Before smoke:

alerts_sent_baseline_id = 2806
agent_tasks_baseline_id = 3
agent_tasks:
  review_copilot / success = 2

listing_enrichments = 0
knowledge_notes_count = 0
listing_detail_snapshots_count = 0
Safe smoke scenarios

The mandatory smoke did not use a real external research provider.

Temporary listing:

pr14-smoke-2026-06-14-listing

Temporary tasks:

pr14-smoke-2026-06-14-disabled
pr14-smoke-2026-06-14-provider-off
pr14-smoke-2026-06-14-unsupported-provider

Checked scenarios:

RESEARCH_AGENT_ENABLED=false
expected: skipped
error type: research_agent_disabled
RESEARCH_AGENT_ENABLED=true
RESEARCH_AGENT_PROVIDER=off
expected: failed closed
error type: research_agent_provider_disabled
RESEARCH_AGENT_ENABLED=true
unsupported provider
expected: failed closed
error type: research_agent_provider_unsupported
Smoke result
created_task_ids 12 13 14

disabled_result:
  status = skipped
  result_json.error_type = research_agent_disabled

provider_off_result:
  status = failed
  error_type = research_agent_provider_disabled

unsupported_provider_result:
  status = failed
  error_type = research_agent_provider_unsupported

PR14_SMOKE_OK
disabled_task_id 12
provider_off_task_id 13
unsupported_task_id 14
alerts_before 2806
alerts_after 2806
tasks_before 2
tasks_after_before_cleanup 5
enrichments_before 0
enrichments_after 0
notes_before 0
notes_after 0
snapshots_before 0
snapshots_after 0
analyses_before 730
analyses_after 730
PR14_SMOKE_CLEANUP_REMAINING 0
Post-smoke cleanup verification
remaining_pr14_smoke_listings = 0
remaining_pr14_smoke_tasks = 0
smoke_alerts = 0
alerts_after_baseline = 0
agent_tasks_after_baseline = 0

agent_tasks:
  review_copilot / success = 2

listing_enrichments = 0
knowledge_notes_count = 0
listing_detail_snapshots_count = 0
Worker logs

Worker logs after deploy/smoke:

monitor cycle completed
browser_driver_crash_count=0
engine_error_count=0
proxy_failures=0

No traceback was observed.

The warning below is expected for the current production environment and is unrelated to PR14:

PROXY_URLS not set — running without proxies
Verdict
PR14 - Manual market_research ResearchAgent shadow mode ✅
Production smoke closed ✅
Cleanup done ✅
No side effects detected ✅

