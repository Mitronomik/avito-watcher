from argparse import Namespace
import json

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.models.agent_task import AgentTask
from app.models.listing_analysis import ListingAnalysis
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import (
    AgentTaskHandlerResult,
    AgentTaskRunner,
    NoopAgentTaskHandler,
)
from app.services.agent_task_service import AgentTaskService


def _analysis(db_session, **kwargs) -> ListingAnalysis:
    analysis = ListingAnalysis(
        listing_external_id=kwargs.pop("listing_external_id", "listing-1"),
        snapshot_id=kwargs.pop("snapshot_id", None),
        search_job_id=kwargs.pop("search_job_id", 123),
        context_key=kwargs.pop("context_key", "search:123"),
        profile=kwargs.pop("profile", "commercial_rent"),
        status=kwargs.pop("status", "success"),
        analysis_version=kwargs.pop("analysis_version", "deterministic-v1"),
        input_hash=kwargs.pop("input_hash", "input-hash"),
        score=kwargs.pop("score", 78),
        verdict=kwargs.pop("verdict", "review"),
        facts_json=kwargs.pop(
            "facts_json",
            {"analysis_config": {"hash": "cfg-hash", "max_age_hours": 24}},
        ),
        risks_json=kwargs.pop("risks_json", {"flags": ["missing_area"]}),
        questions_json=kwargs.pop("questions_json", {"items": ["Уточнить площадь"]}),
        report_md=kwargs.pop("report_md", "# Report"),
        **kwargs,
    )
    db_session.add(analysis)
    db_session.flush()
    return analysis


def test_agent_task_repository_create_or_get_is_idempotent(db_session):
    repo = AgentTaskRepository(db_session)

    first = repo.create_or_get_task(
        task_type="manual_review",
        dedupe_key="agent:manual_review:analysis:1",
        priority=50,
        payload_json={"original": True},
    )
    second = repo.create_or_get_task(
        task_type="manual_review",
        dedupe_key="agent:manual_review:analysis:1",
        priority=20,
        payload_json={"original": False},
    )

    assert second.id == first.id
    assert db_session.scalars(select(AgentTask)).all() == [first]
    assert second.payload_json == {"original": True}
    assert second.status == "pending"
    assert second.priority == 50


def test_agent_task_repository_lifecycle(db_session):
    repo = AgentTaskRepository(db_session)
    task = repo.create_or_get_task(
        task_type="review_listing",
        dedupe_key="agent:review_listing:analysis:1",
    )

    repo.mark_running(task)
    assert task.status == "running"
    assert task.started_at is not None

    repo.mark_success(task, result_json={"ok": True})
    assert task.status == "success"
    assert task.finished_at is not None
    assert task.result_json == {"ok": True}


def test_agent_task_repository_failed_canceled_skipped(db_session):
    repo = AgentTaskRepository(db_session)
    failed = repo.create_or_get_task(
        task_type="manual_review",
        dedupe_key="agent:manual_review:analysis:1",
    )
    canceled = repo.create_or_get_task(
        task_type="follow_up",
        dedupe_key="agent:follow_up:analysis:1",
    )
    skipped = repo.create_or_get_task(
        task_type="ignore_candidate",
        dedupe_key="agent:ignore_candidate:analysis:1",
    )

    repo.mark_failed(failed, "x" * 200, "boom")
    repo.mark_canceled(canceled)
    repo.mark_skipped(skipped, result_json={"reason": "low priority"})

    assert failed.status == "failed"
    assert failed.error_type == "x" * 128
    assert failed.error_message == "boom"
    assert failed.finished_at is not None
    assert canceled.status == "canceled"
    assert canceled.finished_at is not None
    assert skipped.status == "skipped"
    assert skipped.result_json == {"reason": "low priority"}
    assert skipped.finished_at is not None


def test_create_task_from_analysis_builds_payload_and_dedupe(db_session):
    analysis = _analysis(db_session)
    task = AgentTaskService(AgentTaskRepository(db_session)).create_task_from_analysis(analysis)

    assert task.task_type == "manual_review"
    assert task.priority == 50
    assert task.dedupe_key == f"agent:manual_review:analysis:{analysis.id}"
    assert task.status == "pending"
    assert task.listing_external_id == analysis.listing_external_id
    assert task.listing_analysis_id == analysis.id
    assert task.search_job_id == analysis.search_job_id
    assert task.context_key == analysis.context_key
    assert task.payload_json["listing_analysis_id"] == analysis.id
    assert task.payload_json["listing_external_id"] == "listing-1"
    assert task.payload_json["analysis_score"] == 78
    assert task.payload_json["analysis_verdict"] == "review"
    assert task.payload_json["risk_flags"] == ["missing_area"]
    assert task.payload_json["questions"] == ["Уточнить площадь"]
    assert task.payload_json["analysis_config_hash"] == "cfg-hash"
    assert task.payload_json["analysis_config"] == {"hash": "cfg-hash", "max_age_hours": 24}
    assert task.payload_json["recommended_next_action"] == "manual_review"


def test_create_task_from_analysis_is_idempotent(db_session):
    analysis = _analysis(db_session)
    service = AgentTaskService(AgentTaskRepository(db_session))

    first = service.create_task_from_analysis(analysis)
    second = service.create_task_from_analysis(analysis, payload_extra={"new": "ignored"})

    assert second.id == first.id
    assert len(db_session.scalars(select(AgentTask)).all()) == 1


def test_create_task_from_analysis_derives_task_type_and_priority(db_session):
    service = AgentTaskService(AgentTaskRepository(db_session))
    cases = [
        ("strong", 90, "review_listing", 20),
        ("review", 70, "manual_review", 50),
        ("weak", 40, "ignore_candidate", 100),
        (None, None, "manual_review", 80),
    ]

    for index, (verdict, score, expected_type, expected_priority) in enumerate(cases):
        analysis = _analysis(
            db_session,
            listing_external_id=f"listing-{index}",
            input_hash=f"hash-{index}",
            verdict=verdict,
            score=score,
        )
        task = service.create_task_from_analysis(analysis)
        assert task.task_type == expected_type
        assert task.priority == expected_priority


def test_payload_extra_cannot_override_identity_fields(db_session):
    analysis = _analysis(db_session)
    task = AgentTaskService(AgentTaskRepository(db_session)).create_task_from_analysis(
        analysis,
        payload_extra={
            "listing_analysis_id": 999,
            "analysis_input_hash": "evil",
            "listing_external_id": "other",
            "search_job_id": 999,
            "context_key": "other",
            "note": "kept",
        },
    )

    assert task.payload_json["listing_analysis_id"] == analysis.id
    assert task.payload_json["analysis_input_hash"] == analysis.input_hash
    assert task.payload_json["listing_external_id"] == analysis.listing_external_id
    assert task.payload_json["search_job_id"] == analysis.search_job_id
    assert task.payload_json["context_key"] == analysis.context_key
    assert task.payload_json["note"] == "kept"


def test_alembic_env_imports_agent_task():
    with open("alembic/env.py") as env_file:
        assert "from app.models.agent_task import AgentTask" in env_file.read()


def _task(repo: AgentTaskRepository, dedupe_key: str, task_type: str = "manual_review") -> AgentTask:
    return repo.create_or_get_task(
        task_type=task_type,
        dedupe_key=dedupe_key,
        priority=50,
        listing_external_id=f"listing-{dedupe_key}",
        listing_analysis_id=1,
        search_job_id=123,
        context_key="search:123",
        payload_json={"dedupe_key": dedupe_key},
    )


def test_agent_task_runner_noop_success(db_session):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "runner:noop:1", task_type="noop")
    runner = AgentTaskRunner(repo, handlers={"noop": NoopAgentTaskHandler()})

    result = runner.run_pending(limit=10)

    assert result["processed"] == 1
    assert result["succeeded"] == 1
    assert task.status == "success"
    assert task.started_at is not None
    assert task.finished_at is not None
    assert task.result_json["handler"] == "noop"


def test_agent_task_runner_dry_run_does_not_change_status(db_session):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "runner:dry:1")

    result = AgentTaskRunner(repo).run_pending(limit=10, dry_run=True)

    assert result["pending"] == 1
    assert task.status == "pending"
    assert task.started_at is None
    assert task.finished_at is None


def test_agent_task_runner_missing_handler_skips_unregistered_task_type(db_session):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "runner:missing:1", task_type="review_copilot")

    result = AgentTaskRunner(repo).run_pending(limit=10)

    assert result["processed"] == 1
    assert result["succeeded"] == 0
    assert result["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json == {
        "reason": "no_handler_registered",
        "task_type": "review_copilot",
    }


def test_agent_task_runner_filters_by_task_type(db_session):
    repo = AgentTaskRepository(db_session)
    manual_task = _task(repo, "runner:filter:manual", task_type="manual_review")
    other_task = _task(repo, "runner:filter:other", task_type="review_listing")
    runner = AgentTaskRunner(repo, handlers={"manual_review": NoopAgentTaskHandler()})

    result = runner.run_pending(limit=10, task_type="manual_review")

    assert result["processed"] == 1
    assert result["succeeded"] == 1
    assert manual_task.status == "success"
    assert other_task.status == "pending"


class FailingAgentTaskHandler:
    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        raise RuntimeError(f"boom {task.id}")


class SkippingAgentTaskHandler:
    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        return AgentTaskHandlerResult(status="skipped", result_json={"reason": "not needed"})


def test_agent_task_runner_continues_after_handler_failure(db_session):
    repo = AgentTaskRepository(db_session)
    failed_task = _task(repo, "runner:fail:1", task_type="failing_task")
    success_task = _task(repo, "runner:fail:2", task_type="manual_review")
    runner = AgentTaskRunner(
        repo,
        handlers={
            "failing_task": FailingAgentTaskHandler(),
            "manual_review": NoopAgentTaskHandler(),
        },
    )

    result = runner.run_pending(limit=10)

    assert result["failed"] == 1
    assert result["succeeded"] == 1
    assert failed_task.status == "failed"
    assert failed_task.error_type == "RuntimeError"
    assert success_task.status == "success"


def test_agent_task_runner_skipped_result(db_session):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "runner:skip:1", task_type="skipping_task")
    runner = AgentTaskRunner(repo, handlers={"skipping_task": SkippingAgentTaskHandler()})

    result = runner.run_pending(limit=10)

    assert result["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json == {"reason": "not needed"}


def _prepare_agent_task_cli_db(monkeypatch, db_session):
    SessionLocal = sessionmaker(
        bind=db_session.get_bind(), autoflush=False, autocommit=False
    )
    monkeypatch.setattr(cli, "init_db", lambda: None)
    monkeypatch.setattr(cli, "SessionLocal", SessionLocal)


def test_cli_run_agent_tasks_dry_run_outputs_pending_without_changes(
    db_session, monkeypatch, capsys
):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "cli:dry:1")
    db_session.commit()
    _prepare_agent_task_cli_db(monkeypatch, db_session)

    cli.cmd_run_agent_tasks(Namespace(limit=10, task_type=None, dry_run=True))

    output = json.loads(capsys.readouterr().out)
    db_session.refresh(task)
    assert output["ok"] is True
    assert output["dry_run"] is True
    assert output["pending"] == 1
    assert task.status == "pending"


def test_cli_run_agent_tasks_skips_task_without_registered_handler(db_session, monkeypatch, capsys):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "cli:missing:1", task_type="review_copilot")
    db_session.commit()
    _prepare_agent_task_cli_db(monkeypatch, db_session)

    cli.cmd_run_agent_tasks(Namespace(limit=10, task_type=None, dry_run=False))

    output = json.loads(capsys.readouterr().out)
    db_session.refresh(task)
    assert output["ok"] is True
    assert output["processed"] == 1
    assert output["succeeded"] == 0
    assert output["skipped"] == 1
    assert task.status == "skipped"
    assert task.result_json == {
        "reason": "no_handler_registered",
        "task_type": "review_copilot",
    }


def test_cli_run_agent_tasks_rejects_non_positive_limit(db_session, monkeypatch, capsys):
    repo = AgentTaskRepository(db_session)
    task = _task(repo, "cli:limit:1")
    db_session.commit()
    _prepare_agent_task_cli_db(monkeypatch, db_session)

    cli.cmd_run_agent_tasks(Namespace(limit=0, task_type=None, dry_run=False))

    output = json.loads(capsys.readouterr().out)
    db_session.refresh(task)
    assert output["ok"] is False
    assert output["error_type"] == "validation_error"
    assert task.status == "pending"
