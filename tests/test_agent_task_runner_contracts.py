from __future__ import annotations

from sqlalchemy import func, select

from app.agents.registry import get_agent_task_registry
from app.db.base import Base
from app.models.agent_task import AgentTask
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import AgentTaskHandlerResult, AgentTaskRunner, build_default_agent_task_handlers


class SuccessHandler:
    def __init__(self):
        self.calls = 0

    def handle(self, task):
        self.calls += 1
        return AgentTaskHandlerResult(status="success", result_json={"legacy": True})


class FailingHandler:
    def handle(self, task):
        raise RuntimeError("boom")


def _create_task(db_session, task_type: str, dedupe_key: str = "d") -> AgentTask:
    task = AgentTask(task_type=task_type, status="pending", dedupe_key=dedupe_key, payload_json={}, result_json={})
    db_session.add(task)
    db_session.commit()
    return task


def test_known_handler_still_runs_and_legacy_result_shape_is_not_wrapped(db_session):
    task = _create_task(db_session, "known")
    handler = SuccessHandler()
    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={"known": handler}).run_pending(limit=1)
    db_session.refresh(task)
    assert result["succeeded"] == 1
    assert handler.calls == 1
    assert task.status == "success"
    assert task.result_json == {"legacy": True}


def test_handler_exception_remains_failed(db_session):
    task = _create_task(db_session, "known")
    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={"known": FailingHandler()}).run_pending(limit=1)
    db_session.refresh(task)
    assert result["failed"] == 1
    assert task.status == "failed"
    assert task.error_type == "RuntimeError"
    assert task.error_message == "boom"


def test_unknown_task_type_does_not_succeed_or_call_handler(db_session):
    task = _create_task(db_session, "unknown_contract_task")
    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={}).run_pending(limit=1)
    db_session.refresh(task)
    assert result["succeeded"] == 0
    assert result["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json["error_type"] == "unknown_agent_task_type"


def test_registered_future_task_without_handler_does_not_succeed(db_session):
    future_type = next(task_type for task_type, contract in get_agent_task_registry().items() if not contract.implemented)
    task = _create_task(db_session, future_type)
    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={}).run_pending(limit=1)
    db_session.refresh(task)
    assert result["succeeded"] == 0
    assert result["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json["error_type"] == "agent_handler_not_registered"


def test_dry_run_does_not_mutate_or_call_handler(db_session):
    task = _create_task(db_session, "known")
    handler = SuccessHandler()
    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={"known": handler}).run_pending(limit=1, dry_run=True)
    db_session.refresh(task)
    assert result["dry_run"] is True
    assert result["pending"] == 1
    assert handler.calls == 0
    assert task.status == "pending"
    assert task.result_json == {}


def test_default_handler_registration_api_matches_contract_registry():
    handlers = build_default_agent_task_handlers(object())
    implemented = {task_type for task_type, contract in get_agent_task_registry().items() if contract.implemented}
    assert set(handlers) == implemented


def test_registry_reads_do_not_create_or_mutate_side_effect_tables(db_session):
    before = {name: db_session.scalar(select(func.count()).select_from(table)) for name, table in Base.metadata.tables.items()}
    get_agent_task_registry()
    after = {name: db_session.scalar(select(func.count()).select_from(table)) for name, table in Base.metadata.tables.items()}
    assert after == before


def test_unknown_workflow_id_does_not_break_runner(db_session):
    task = _create_task(db_session, "known", "unknown-workflow")
    task.workflow_id = "unknown_workflow"
    db_session.commit()
    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={"known": SuccessHandler()}).run_pending(limit=1)
    db_session.refresh(task)
    assert result["succeeded"] == 1
    assert task.status == "success"


def test_depends_on_task_id_does_not_enforce_runtime_dependency_or_create_tasks(db_session):
    parent = _create_task(db_session, "known", "dependency-parent")
    task = _create_task(db_session, "known", "dependency-child")
    task.depends_on_task_id = parent.id
    db_session.commit()

    result = AgentTaskRunner(AgentTaskRepository(db_session), handlers={"known": SuccessHandler()}).run_pending(limit=10)
    db_session.refresh(task)

    assert result["succeeded"] == 2
    assert task.status == "success"
    assert db_session.scalar(select(func.count()).select_from(AgentTask)) == 2
