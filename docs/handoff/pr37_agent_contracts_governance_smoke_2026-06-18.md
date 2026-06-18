# PR37 production smoke handoff - Agent contracts and governance registry

## PR

PR37 - Agent contracts, orchestration and task governance v1.

Merged PR:

- #239 - Add agent task/workflow contracts, registry, redaction improvements, and runner diagnostics

Production commit:

- `c96eef5` - Add agent task/workflow contracts, registry, redaction improvements, and runner diagnostics (#239)

Deploy date:

- 2026-06-18

## Deploy summary

Production was updated from:

- `b0a4089` - Add Price Position v1 read API (#237)

To:

- `c96eef5` - Add agent task/workflow contracts, registry, redaction improvements, and runner diagnostics (#239)

Docker services rebuilt and started:

- `app`
- `worker`

Observed services after deploy:

- `postgres` healthy
- `redis` healthy
- `app` running
- `worker` running

## Smoke results

### Health

Command:

```bash
curl -i http://127.0.0.1:8010/health
```

Result:

```json
{"status":"ok"}
```

Status:

- PASS

### Alembic

Commands:

```bash
alembic current
alembic heads
```

Result:

```text
0017_admin_audit_events (head)
0017_admin_audit_events (head)
```

Status:

- PASS

### Agent orchestration flags

Observed values:

```text
AGENT_ORCHESTRATION_ENABLED False
AGENT_ORCHESTRATION_ALLOW_MONITOR_TRIGGER False
AGENT_ORCHESTRATION_MAX_CHAIN_DEPTH 4
AGENT_ORCHESTRATION_MAX_TASKS_PER_LISTING 10
AGENT_ORCHESTRATION_DEFAULT_TIMEOUT_SEC 120
```

Status:

- PASS

### Agent task registry

Observed:

```text
task_registry_count 11
claim_guard_future implemented= False handler_required= False class= claim_guard
data_gap_agent_future implemented= False handler_required= False class= data_gap_analysis
data_quality_agent implemented= True handler_required= True class= data_gap_analysis
decision_card_wording_future implemented= False handler_required= False class= decision_wording
evidence_collector_future implemented= False handler_required= False class= data_collection
evidence_normalizer_future implemented= False handler_required= False class= data_normalization
listing_detail_extraction implemented= True handler_required= True class= data_collection
market_research implemented= True handler_required= True class= data_collection
owner_call_prep_future implemented= False handler_required= False class= call_preparation
review_copilot implemented= True handler_required= True class= decision_wording
weekly_strategy_agent implemented= True handler_required= True class= portfolio_memory
```

Status:

- PASS

### Workflow registry

Observed:

```text
workflow_registry_count 3
listing_evidence_pipeline implemented= False blocking_policy= non_blocking_metadata_only
listing_decision_support_pipeline implemented= False blocking_policy= non_blocking_metadata_only
report_safety_pipeline implemented= False blocking_policy= non_blocking_metadata_only
```

Status:

- PASS

### Admin meta contract

The Admin API meta endpoint returns the standard Admin v1 response wrapper:

```json
{
  "ok": true,
  "data": {
    "agent_contracts": "..."
  },
  "meta": {
    "api_version": "...",
    "generated_at": "..."
  }
}
```

Observed top-level keys:

```text
data
meta
ok
```

Observed `data` keys included:

```text
agent_contracts
api_version
capabilities
decision_card_contract_version
enums
errors
labels
legacy_labels
meta_contract_version
permissions
price_position_contract_version
readiness_checklist_contract_version
risk_attention_contract_version
roles
service
status
workflow_contract_version
```

Direct contract builder smoke:

```text
builder_has_agent_contracts True
task_types 11
workflows 3
has_required_permission_refs True
has_agent_task_namespace False
```

Route check:

```text
app/api/admin_v1/routes.py:23:@router.get("/meta")
app/api/admin_v1/routes.py:24:def meta() -> dict[str, object]:
app/api/admin_v1/routes.py:25:    return success_response(build_meta_contract())
```

Confirmed:

- `/api/admin/v1/meta` exposes `data.agent_contracts`
- `required_permission_refs` are present
- permission refs reuse the existing PR30 permission registry
- no `agent_task.*` permission namespace is exposed
- no execution endpoint metadata is exposed
- no HTTP method metadata is exposed
- no absolute URL metadata is exposed
- no auth param metadata is exposed
- no raw result_json metadata is exposed

Status:

- PASS

### Agent runner dry-run

Command:

```bash
python3 -m app.cli run-agent-tasks --limit 10 --dry-run
```

Result:

```json
{
  "ok": true,
  "limit": 10,
  "task_type": null,
  "dry_run": true,
  "pending": 0,
  "tasks": []
}
```

Status:

- PASS

### Production counts

Observed after smoke:

```text
listings_total: 2374
listing_analyses_total: 730
human_reviews_total: 0
market_evidence_items_total: 0
alerts_sent_total: 4568
agent_tasks_total: 2
```

Status:

- PASS for PR37 smoke. PR37 registry, meta, and dry-run checks did not intentionally create listings, analyses, human reviews, market evidence, alerts, or agent tasks.

### Logs and secret grep

Checked app and worker logs for:

- `ERROR`
- `CRITICAL`
- `Traceback`
- `Exception`
- `Authorization:`
- `Cookie:`
- `X-API-Key`
- bearer token patterns
- `OPENAI_API_KEY`
- `DATABASE_URL`
- `POSTGRES_PASSWORD`
- `WEBHOOK_URL`
- `SMTP_PASSWORD`
- `TELEGRAM_BOT_TOKEN`

Result:

- no matches

Status:

- PASS

## Confirmed PR37 boundaries

Confirmed:

- no `AgentOrchestratorService`
- no dependency graph
- no `agent_artifacts` table
- no new runnable agents
- no automatic task creation
- no monitor-cycle integration
- no score mutation
- no verdict mutation
- no workflow state mutation
- no allowed actions mutation
- no filter or config mutation
- no `AlertSent` creation from PR37 smoke
- no persisted `result_json` validation on read
- no output wrapping or rewrite of existing handler outputs
- no parallel `AgentPermission` authorization system
- `declared_side_effects` remain descriptive metadata only
- `required_permission_refs` reuse the existing PR30 permission registry
- future workflows remain `implemented=false`
- future task contracts remain `implemented=false` / `handler_required=false`

## Operational notes

The server login banner reported:

- 50 zombie processes
- system restart required
- root filesystem usage around 75%

These are operational maintenance items and not PR37 regressions. They are intentionally not addressed in this docs-only PR.

## Next roadmap PR

Next:

- PR38 - Agent orchestration metadata and dependency graph

PR38 should remain metadata/storage focused and must not implement `AgentOrchestratorService` runtime. Runtime orchestration belongs to PR40.
