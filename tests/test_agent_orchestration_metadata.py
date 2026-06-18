from pathlib import Path

from sqlalchemy import inspect

from app.agents.orchestration_metadata import validate_agent_task_orchestration_metadata
from app.agents.registry import get_agent_workflow_registry
from app.models.agent_task import (
    AGENT_TASK_DEPENDENCY_STATUSES,
    AGENT_TASK_ORCHESTRATION_STATUSES,
    AgentTask,
)


def test_agent_task_orchestration_fields_exist_and_defaults_are_backward_compatible(db_session):
    columns = {column.name for column in inspect(AgentTask).columns}
    assert {
        "orchestration_run_id",
        "workflow_id",
        "parent_task_id",
        "depends_on_task_id",
        "chain_depth",
        "blocking",
        "dependency_status",
        "orchestration_status",
    }.issubset(columns)

    task = AgentTask(task_type="manual_review", dedupe_key="pr38:plain", payload_json={}, result_json={})
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    assert task.orchestration_run_id is None
    assert task.workflow_id is None
    assert task.parent_task_id is None
    assert task.depends_on_task_id is None
    assert task.chain_depth == 0
    assert task.blocking is False
    assert task.dependency_status == "not_applicable"
    assert task.orchestration_status == "not_applicable"


def test_agent_task_can_store_safe_orchestration_metadata(db_session):
    workflow_id = next(iter(get_agent_workflow_registry()))
    parent = AgentTask(task_type="manual_review", dedupe_key="pr38:parent", payload_json={}, result_json={})
    db_session.add(parent)
    db_session.flush()
    task = AgentTask(
        task_type="manual_review",
        dedupe_key="pr38:child",
        payload_json={},
        result_json={},
        orchestration_run_id="run-1",
        workflow_id=workflow_id,
        parent_task_id=parent.id,
        depends_on_task_id=parent.id,
        chain_depth=1,
        blocking=True,
        dependency_status="waiting",
        orchestration_status="queued",
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    assert task.workflow_id == workflow_id
    assert task.parent_task_id == parent.id
    assert task.depends_on_task_id == parent.id
    assert validate_agent_task_orchestration_metadata(task=task).valid is True


def test_status_enums_are_closed_and_validation_rejects_unknown_values():
    assert AGENT_TASK_DEPENDENCY_STATUSES == ("not_applicable", "waiting", "ready", "blocked")
    assert AGENT_TASK_ORCHESTRATION_STATUSES == (
        "not_applicable",
        "queued",
        "running",
        "completed",
        "failed",
        "skipped",
        "blocked",
    )
    assert validate_agent_task_orchestration_metadata(dependency_status="surprise").reason == "invalid_dependency_status"
    assert validate_agent_task_orchestration_metadata(orchestration_status="surprise").reason == "invalid_orchestration_status"


def test_chain_depth_and_workflow_validation_are_safe():
    known_workflow = next(iter(get_agent_workflow_registry()))
    assert validate_agent_task_orchestration_metadata(workflow_id=known_workflow, chain_depth=0).valid
    assert validate_agent_task_orchestration_metadata(chain_depth=-1).reason == "invalid_chain_depth"
    assert validate_agent_task_orchestration_metadata(chain_depth=2, max_chain_depth=1).reason == "chain_depth_exceeds_max"
    assert validate_agent_task_orchestration_metadata(workflow_id="unknown_workflow").reason == "unknown_workflow_id"


def test_self_reference_validation_is_safe():
    assert validate_agent_task_orchestration_metadata(task_id=7, parent_task_id=7).reason == "parent_task_self_reference"
    assert validate_agent_task_orchestration_metadata(task_id=7, depends_on_task_id=7).reason == "dependency_task_self_reference"


def test_pr38_scope_does_not_add_runtime_or_artifact_layers():
    assert not Path("app/services/agent_orchestrator_service.py").exists()
    assert not Path("app/models/agent_artifact.py").exists()
    assert "agent_artifacts" not in Path("alembic/versions/0018_agent_task_orchestration_metadata.py").read_text()
    assert "score" not in Path("alembic/versions/0018_agent_task_orchestration_metadata.py").read_text()
    assert "verdict" not in Path("alembic/versions/0018_agent_task_orchestration_metadata.py").read_text()
