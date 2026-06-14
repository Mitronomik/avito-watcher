# PR17 — Weekly StrategyAgent production smoke

Date: 2026-06-14  
Environment: `avito-watcher-prod`  
Repository: `Mitronomik/avito-watcher`  
Roadmap item: PR17 — Weekly StrategyAgent with system memory RAG  
PR: #171 — `Add weekly strategy agent`  
Merge commit: `667299176f46939fd3c671f5fdc76ac258ade0dd`  
Status: **CLOSED ✅**

---

## 1. Purpose of this handoff

This document records the production deployment and safe smoke test for PR17.

PR17 added a manual / opt-in advisory `weekly_strategy_agent` AgentTask type.

The PR17 production smoke verified that:

```text
weekly_strategy_agent handler is registered
weekly_strategy_agent is disabled by default
provider is off by default
no external call is made in default production smoke
task fails/skips closed according to disabled behavior
no operational tables are mutated
only the temporary AgentTask row is created during smoke
smoke task cleanup succeeds
post-cleanup SQL returns 0
```

This document is intentionally operational and factual. It is not a design proposal and does not change runtime behavior.

---

## 2. Architectural boundary

PR17 is an advisory strategy loop, not an autonomous optimizer.

The governing architecture remains:

```text
Clean data first.
Deterministic gates second.
Deterministic scoring third.
Human-readable LLM explanation fourth.
RAG memory fifth.
External research sixth.
Investment scoring with comps seventh.
Agent strategy loop last.
```

Correct model:

```text
Deterministic system decides.
Agents investigate and explain.
RAG provides context.
Research validates market assumptions.
Human approves action.
```

PR17 explicitly does **not** allow the agent to mutate:

```text
score
verdict
filters
searches
code
alerts
market evidence
knowledge notes / RAG memory
monitor cycle behavior
```

The agent may analyze and propose. A human must approve any action.

---

## 3. What PR17 added

PR17 added:

```text
manual weekly_strategy_agent AgentTask type
WeeklyStrategyAgent service
strict Pydantic payload/result schemas
deterministic SQL stats collector
bounded system-memory / knowledge-note context retrieval
advisory-only prompt builder
provider-off fail-closed behavior
optional OpenAI-compatible provider adapter
stable input hashing over payload, window, stats hash, context hash, prompt/schema version
default-off settings
docs and tests
```

Important properties:

```text
manual-only
opt-in only
disabled by default
provider off by default
no external calls by default
no monitor integration
no automatic task creation
no scheduler integration
agent_tasks-only mutation scope
```

---

## 4. Non-goals confirmed

PR17 did not implement:

```text
human outcome tracking
human_reviews table
investment_decisions table
admin UI
alert retry dashboard
health dashboard
backup / restore
access control
comp quality scoring
comparable selection policy v2
adjusted comps
source quality scoring
DCF / scenario / financing
SPb taxonomy
confirmed data workflow
automatic threshold calibration
automatic filter mutation
automatic code mutation
autonomous strategy execution
```

Those belong to later roadmap phases.

---

## 5. Deployment evidence

Production deploy was performed on `avito-watcher-prod`.

The production images were rebuilt successfully:

```text
✔ Image deploy-worker Built
✔ Image deploy-app    Built
```

Alembic heads:

```text
0013_market_evidence_storage (head)
```

Alembic current:

```text
0013_market_evidence_storage (head)
```

This confirms PR17 introduced no new database migration.

App and worker started successfully:

```text
✔ Container deploy-app-1      Started
✔ Container deploy-worker-1   Started
```

Health check:

```text
HTTP/1.1 200 OK
{"status":"ok"}
```

Worker startup logs were clean. The worker completed its monitor cycle without traceback or DB errors:

```text
monitor worker runtime diagnostics: {...}
avito_parser.end_cycle stats={...}
monitor_service.cycle_summary searches_processed=0 ...
monitor cycle completed
```

No `Traceback`, `OperationalError`, `UndefinedTable`, or unsupported profile error was observed.

---

## 6. Smoke test mode

The smoke was intentionally safe and default-off:

```bash
-e WEEKLY_STRATEGY_AGENT_ENABLED=false
-e WEEKLY_STRATEGY_AGENT_PROVIDER=off
```

Expected behavior:

```text
handler registered
temporary weekly_strategy_agent task created
runner processes the task
task is skipped with weekly_strategy_agent_disabled
no external provider call
no side effects outside temporary agent_tasks row
cleanup removes temporary task
```

This is the correct production smoke for PR17 because PR17 must be default-off and advisory-only.

---

## 7. Smoke command summary

The smoke used the existing AgentTask runner path:

```python
from app.agents.weekly_strategy_agent import WEEKLY_STRATEGY_AGENT_TASK_TYPE
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import AgentTaskRunner, build_default_agent_task_handlers
```

The smoke:

1. Deleted any old `pr17-smoke-2026-06-14%` tasks.
2. Captured baseline table counts.
3. Built default AgentTask handlers.
4. Verified `weekly_strategy_agent` handler registration.
5. Created one temporary pending `weekly_strategy_agent` task.
6. Ran `AgentTaskRunner.run_pending(...)` for that task type.
7. Verified skipped disabled behavior.
8. Verified no side effects.
9. Cleaned up the temporary task.
10. Verified cleanup remaining count was zero.

---

## 8. Smoke result

Smoke output:

```text
PR17_SMOKE_OK
handler_registered True
task_id 17
task_status skipped
task_error_type weekly_strategy_agent_disabled
runner_processed 1
runner_skipped 1
alerts_before 2830
alerts_after 2830
listings_before 1505
listings_after 1505
analyses_before 730
analyses_after 730
tasks_before 2
tasks_after_before_cleanup 3
runs_before 0
runs_after 0
items_before 0
items_after 0
notes_before 0
notes_after 0
enrichments_before 0
enrichments_after 0
snapshots_before 0
snapshots_after 0
PR17_SMOKE_CLEANUP_REMAINING 0
```

This confirms:

```text
handler registration passed
default-disabled path passed
provider-off/default-off smoke made no external call
task lifecycle worked
smoke task was skipped with expected error_type
no production data changed outside the temporary AgentTask row
cleanup succeeded
```

---

## 9. No-side-effects verification

Baseline and post-smoke counts:

| Table / area | Before | After | Result |
|---|---:|---:|---|
| `alerts_sent` | 2830 | 2830 | unchanged ✅ |
| `listings` | 1505 | 1505 | unchanged ✅ |
| `listing_analyses` | 730 | 730 | unchanged ✅ |
| `agent_tasks` | 2 | 3 before cleanup | exactly one temporary smoke task ✅ |
| `market_research_runs` | 0 | 0 | unchanged ✅ |
| `market_evidence_items` | 0 | 0 | unchanged ✅ |
| `knowledge_notes` | 0 | 0 | unchanged ✅ |
| `listing_enrichments` | 0 | 0 | unchanged ✅ |
| `listing_detail_snapshots` | 0 | 0 | unchanged ✅ |

After cleanup:

```text
PR17_SMOKE_CLEANUP_REMAINING 0
```

---

## 10. Post-cleanup SQL check

SQL check:

```sql
select count(*) as pr17_smoke_tasks
from agent_tasks
where dedupe_key like 'pr17-smoke-2026-06-14%';
```

Result:

```text
 pr17_smoke_tasks 
------------------
                0
(1 row)
```

This confirms no temporary PR17 smoke task remains in production.

---

## 11. What was not tested in production smoke

The production smoke intentionally did not test real external LLM provider execution.

Not tested in this smoke:

```text
OpenAI-compatible provider call
real model output
real weekly report generation from external provider
scheduled execution
automatic task creation
```

Reason:

```text
PR17 default production behavior must be safe/off.
Real provider execution is optional and should be tested separately only when explicitly configured.
```

The smoke did test the most important safety contract:

```text
with defaults/off, weekly_strategy_agent cannot perform external calls or operational mutations.
```

---

## 12. Operational notes

The new agent is safe to deploy with defaults:

```env
WEEKLY_STRATEGY_AGENT_ENABLED=false
WEEKLY_STRATEGY_AGENT_PROVIDER=off
```

To use it later, enable manually and run through the AgentTask runner path. Any real provider run should be treated as a separate controlled smoke:

```text
small payload
bounded period_days
no automatic mutations
manual review of result_json
verify no side effects
cleanup temporary tasks if needed
```

Do not wire the agent into monitor cycle or scheduler without a separate PR and explicit opt-in design.

---

## 13. Final verdict

```text
PR17 production deploy: done ✅
Alembic: unchanged at 0013 ✅
App health: OK ✅
Worker logs: clean ✅
Handler registration: passed ✅
Default-off behavior: passed ✅
weekly_strategy_agent disabled path: passed ✅
Task status: skipped ✅
error_type: weekly_strategy_agent_disabled ✅
No external call: passed ✅
No operational side effects: passed ✅
Cleanup: done ✅
Post-cleanup SQL check: 0 ✅

Status: CLOSED ✅
```

PR17 is production-smoked and closed.

---

## 14. Roadmap transition

With PR17 closed, the original roadmap PR1–PR17 is complete.

Next phase begins with production workflow / outcome tracking and operational hardening.

Recommended next roadmap item:

```text
PR18 — Human decision logging + outcome tracking
```

PR18 should remain separate from PR17 and should not be mixed with StrategyAgent logic.
