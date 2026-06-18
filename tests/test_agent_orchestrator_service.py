from sqlalchemy import select

from app.agents.registry import get_agent_task_registry, get_agent_workflow_registry
from app.agents.workflow_blueprints import AgentWorkflowBlueprint, AgentWorkflowBlueprintNode, get_agent_workflow_blueprints
from app.models.agent_artifact import AgentArtifact
from app.models.agent_task import AgentTask
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_orchestrator_service import AgentOrchestratorService
from app.services.agent_task_runner import AgentTaskHandlerResult, AgentTaskRunner


def test_blueprints_are_subset_of_pr37_registries():
    workflows = get_agent_workflow_registry()
    tasks = get_agent_task_registry()
    blueprints = get_agent_workflow_blueprints()

    assert set(blueprints) <= set(workflows)
    for workflow_id, blueprint in blueprints.items():
        assert blueprint.workflow_id == workflow_id
        for node in blueprint.nodes:
            assert node.task_type in tasks


def test_known_workflow_builds_safe_deterministic_future_only_plan():
    plan = AgentOrchestratorService().build_plan(workflow_id="listing_evidence_pipeline")

    assert plan.valid is True
    assert plan.planning_supported is True
    assert plan.enqueue_supported is False
    assert [node.node_id for node in plan.nodes] == ["evidence_collector", "evidence_normalizer"]
    assert all(node.handler_implemented is False for node in plan.nodes)
    assert all(node.can_enqueue is False for node in plan.nodes)
    assert all(node.blocked_reason in {"handler_unimplemented", "non_root_node"} for node in plan.nodes)
    for node in plan.nodes:
        assert not hasattr(node, "execution_endpoint")
        assert not hasattr(node, "auth_param")


def test_unknown_workflow_and_missing_blueprint_return_invalid_without_exception(monkeypatch):
    svc = AgentOrchestratorService()
    assert svc.build_plan(workflow_id="missing").reason == "unknown_workflow_id"
    monkeypatch.setattr("app.services.agent_orchestrator_service.get_agent_workflow_blueprints", lambda: {})
    plan = svc.build_plan(workflow_id="listing_evidence_pipeline")
    assert plan.valid is False
    assert plan.reason == "missing_blueprint"


def test_validation_rejects_unknown_task_cycle_and_caps(monkeypatch):
    svc = AgentOrchestratorService()
    monkeypatch.setattr("app.services.agent_orchestrator_service.get_agent_workflow_blueprints", lambda: {"listing_evidence_pipeline": AgentWorkflowBlueprint("listing_evidence_pipeline", (AgentWorkflowBlueprintNode("x", "missing"),))})
    assert svc.validate_blueprint(workflow_id="listing_evidence_pipeline").reason == "unknown_task_type"

    monkeypatch.setattr("app.services.agent_orchestrator_service.get_agent_workflow_blueprints", lambda: {"listing_evidence_pipeline": AgentWorkflowBlueprint("listing_evidence_pipeline", (AgentWorkflowBlueprintNode("a", "evidence_collector_future", ("b",)), AgentWorkflowBlueprintNode("b", "evidence_collector_future", ("a",))))})
    assert svc.validate_blueprint(workflow_id="listing_evidence_pipeline").reason == "dependency_cycle"

    monkeypatch.setattr("app.services.agent_orchestrator_service.settings.agent_orchestration_max_tasks_per_listing", 1)
    monkeypatch.setattr("app.services.agent_orchestrator_service.get_agent_workflow_blueprints", get_agent_workflow_blueprints)
    assert svc.validate_blueprint(workflow_id="listing_evidence_pipeline").reason == "max_tasks_per_listing_exceeded"


def test_dry_run_and_disabled_enqueue_create_no_rows(db_session, monkeypatch):
    svc = AgentOrchestratorService(db_session)
    before_tasks = db_session.scalar(select(AgentTask).count()) if False else len(db_session.scalars(select(AgentTask)).all())
    result = svc.enqueue_workflow(workflow_id="listing_evidence_pipeline", listing_external_id="l1", dry_run=True)
    disabled = svc.enqueue_workflow(workflow_id="listing_evidence_pipeline", listing_external_id="l1", dry_run=False)

    assert result.ok is True
    assert result.dry_run is True
    assert disabled.ok is False
    assert disabled.blocked_reason == "orchestration_disabled"
    assert len(db_session.scalars(select(AgentTask)).all()) == before_tasks
    assert db_session.scalars(select(AgentArtifact)).all() == []


def test_enabled_future_only_enqueue_zero_tasks(db_session, monkeypatch):
    monkeypatch.setattr("app.services.agent_orchestrator_service.settings.agent_orchestration_enabled", True)
    result = AgentOrchestratorService(db_session).enqueue_workflow(workflow_id="listing_evidence_pipeline", listing_external_id="l1", dry_run=False)

    assert result.ok is True
    assert result.enqueued_task_ids == ()
    assert result.orchestration_run_id is None
    assert result.blocked_reason == "no_implemented_root_nodes"
    assert db_session.scalars(select(AgentTask)).all() == []


def test_repository_orchestration_metadata_and_runner_dependency_guard(db_session):
    repo = AgentTaskRepository(db_session)
    ready = repo.create_or_get_task(task_type="noop", dedupe_key="ready", dependency_status="ready", orchestration_status="queued", workflow_id="listing_evidence_pipeline", chain_depth=0)
    waiting = repo.create_or_get_task(task_type="noop", dedupe_key="waiting", dependency_status="waiting", orchestration_status="queued", workflow_id="listing_evidence_pipeline", chain_depth=1)
    blocked = repo.create_or_get_task(task_type="noop", dedupe_key="blocked", dependency_status="blocked", orchestration_status="queued", workflow_id="listing_evidence_pipeline", chain_depth=1)
    null_dep = repo.create_or_get_task(task_type="noop", dedupe_key="null")

    assert set(repo.list_pending(10)) == {ready, null_dep}

    class Success:
        def handle(self, task):
            return AgentTaskHandlerResult(status="success", result_json={"ok": True})

    result = AgentTaskRunner(repo, handlers={"noop": Success()}).run_pending(limit=10)
    assert result["processed"] == 2
    assert waiting.status == "pending"
    assert blocked.status == "pending"
    assert waiting.orchestration_status == "queued"
    assert db_session.scalars(select(AgentArtifact)).all() == []


def test_repository_rejects_invalid_orchestration_metadata(db_session):
    repo = AgentTaskRepository(db_session)
    try:
        repo.create_or_get_task(task_type="noop", dedupe_key="bad", dependency_status="bad")
    except ValueError as exc:
        assert "invalid_dependency_status" in str(exc)
    else:
        raise AssertionError("expected invalid metadata")


def test_orchestrator_artifact_summary_uses_safe_serializer(db_session):
    artifact = AgentArtifact(artifact_type="claim_review", schema_version="v1", input_hash="input", content_hash="content", payload_json={"summary": "safe", "raw_payload_json": "secret"}, source_refs_json={}, redaction_status="redacted", orchestration_run_id="orch_test")
    db_session.add(artifact)
    db_session.flush()

    summary = AgentOrchestratorService(db_session).summarize_run(orchestration_run_id="orch_test")

    assert summary.artifact_count == 1
    assert summary.artifacts[0]["artifact_id"] == artifact.id
    assert "payload_json" not in summary.artifacts[0]
    assert "payload_preview" not in summary.artifacts[0]
