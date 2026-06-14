import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, func

from app.agents.weekly_strategy_agent import (
    WEEKLY_STRATEGY_AGENT_TASK_TYPE,
    WeeklyStrategyAgentTaskHandler,
)
from app.core.config import Settings
from app.models.agent_task import AgentTask
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.search_job import SearchJob
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import (
    AgentTaskRunner,
    build_default_agent_task_handlers,
)
from app.services.weekly_strategy_agent import (
    WeeklyStrategyAgentPayload,
    WeeklyStrategyAgentService,
    WeeklyStrategyStatsCollector,
    build_weekly_strategy_input_hash,
    sha256_json,
    validate_model_output,
)


class FakeProvider:
    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.calls = []

    def complete(self, *, prompt, timeout_sec, max_output_chars):
        self.calls.append(prompt)
        return json.dumps(
            {
                "confidence": 0.5,
                "executive_summary": "Low data, manual review required.",
                "health_status": "watch",
                "key_findings": ["facts from stats only"],
                "search_quality_findings": [],
                "data_quality_findings": [],
                "market_evidence_findings": [],
                "agent_task_findings": [],
                "operational_findings": [],
                "recommendations": [
                    {
                        "area": "operations",
                        "priority": "low",
                        "recommendation": "Review report manually",
                        "rationale": "advisory",
                        "suggested_human_action": "Human reviews the report",
                    }
                ],
                "suggested_next_pr": "PR18",
                "limitations": [],
            }
        )


def test_config_defaults_and_env_parse():
    cfg = Settings(database_url="sqlite:///:memory:", _env_file=None)
    assert cfg.weekly_strategy_agent_enabled is False
    assert cfg.weekly_strategy_agent_provider == "off"
    cfg2 = Settings(
        database_url="sqlite:///:memory:",
        weekly_strategy_agent_enabled=True,
        weekly_strategy_agent_provider="openai_compatible",
        _env_file=None,
    )
    assert cfg2.weekly_strategy_agent_enabled is True


def test_handler_registered():
    assert WEEKLY_STRATEGY_AGENT_TASK_TYPE in build_default_agent_task_handlers(
        object()
    )


def test_disabled_and_provider_off_no_external_call(db_session, monkeypatch):
    task = AgentTask(
        task_type=WEEKLY_STRATEGY_AGENT_TASK_TYPE, dedupe_key="ws1", payload_json={}
    )
    db_session.add(task)
    db_session.commit()
    fake = FakeProvider()
    monkeypatch.setattr(
        "app.services.weekly_strategy_agent.settings.weekly_strategy_agent_enabled",
        False,
    )
    res = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            WEEKLY_STRATEGY_AGENT_TASK_TYPE: WeeklyStrategyAgentTaskHandler(
                db_session, WeeklyStrategyAgentService(db_session, fake)
            )
        },
    ).run_pending(1)
    assert res["skipped"] == 1
    assert fake.calls == []
    assert (
        db_session.get(AgentTask, task.id).result_json["error_type"]
        == "weekly_strategy_agent_disabled"
    )

    task2 = AgentTask(
        task_type=WEEKLY_STRATEGY_AGENT_TASK_TYPE, dedupe_key="ws2", payload_json={}
    )
    db_session.add(task2)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_strategy_agent.settings.weekly_strategy_agent_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.weekly_strategy_agent.settings.weekly_strategy_agent_provider",
        "off",
    )
    res = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            WEEKLY_STRATEGY_AGENT_TASK_TYPE: WeeklyStrategyAgentTaskHandler(
                db_session, WeeklyStrategyAgentService(db_session, fake)
            )
        },
    ).run_pending(1)
    assert res["failed"] == 1
    assert fake.calls == []
    assert (
        db_session.get(AgentTask, task2.id).error_type
        == "weekly_strategy_agent_provider_disabled"
    )


def test_payload_validation_bounds():
    assert WeeklyStrategyAgentPayload().period_days == 7
    assert (
        WeeklyStrategyAgentPayload(max_examples_per_section=25).max_examples_per_section
        == 25
    )
    try:
        WeeklyStrategyAgentPayload(period_days=31)
        assert False
    except Exception:
        assert True


def test_time_window_input_hash_changes():
    payload = WeeklyStrategyAgentPayload(search_ids=[1])
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = start + timedelta(days=7)
    h1 = build_weekly_strategy_input_hash(
        payload=payload,
        period_start_at=start,
        period_end_at=end,
        report_as_of_date="2026-06-08",
        stats_snapshot_hash="a",
        context_hash=None,
        prompt_version="p",
        schema_version="s",
    )
    h2 = build_weekly_strategy_input_hash(
        payload=payload,
        period_start_at=start + timedelta(days=7),
        period_end_at=end + timedelta(days=7),
        report_as_of_date="2026-06-15",
        stats_snapshot_hash="a",
        context_hash=None,
        prompt_version="p",
        schema_version="s",
    )
    h3 = build_weekly_strategy_input_hash(
        payload=payload,
        period_start_at=start,
        period_end_at=end,
        report_as_of_date="2026-06-08",
        stats_snapshot_hash="b",
        context_hash=None,
        prompt_version="p",
        schema_version="s",
    )
    h4 = build_weekly_strategy_input_hash(
        payload=payload,
        period_start_at=start,
        period_end_at=end,
        report_as_of_date="2026-06-08",
        stats_snapshot_hash="a",
        context_hash="c",
        prompt_version="p",
        schema_version="s",
    )
    assert h1 != h2 and h1 != h3 and h1 != h4


def test_stats_collector_stable_and_read_only(db_session):
    sj = SearchJob(name="S", source_url="u", is_active=True)
    db_session.add(sj)
    db_session.flush()
    db_session.add(Listing(external_id="e1", url="u", title="T", area_m2=None))
    db_session.add(
        ListingAnalysis(
            listing_external_id="e1",
            search_job_id=sj.id,
            status="success",
            verdict="review",
            profile="p",
            input_hash="h",
            score=0.8,
            risks_json={"missing_area": True},
        )
    )
    db_session.commit()
    before = db_session.scalar(select(func.count()).select_from(Listing))
    snap = WeeklyStrategyStatsCollector(db_session).collect(
        payload=WeeklyStrategyAgentPayload(),
        period_start_at=datetime(2000, 1, 1),
        period_end_at=datetime(2100, 1, 1),
    )
    assert snap["analysis_stats"][0]["profile"] == "p"
    assert snap["risk_flags"][0]["risk_flag"] == "missing_area"
    assert sha256_json(snap) == sha256_json(snap)
    assert db_session.scalar(select(func.count()).select_from(Listing)) == before


def test_success_sets_service_metadata_and_sanitizes_refs(db_session, monkeypatch):
    sj = SearchJob(name="S", source_url="u", is_active=True)
    db_session.add(sj)
    db_session.flush()
    db_session.add(Listing(external_id="known", url="u", title="T"))
    db_session.commit()
    task = AgentTask(
        task_type=WEEKLY_STRATEGY_AGENT_TASK_TYPE,
        dedupe_key="ws3",
        payload_json={"search_ids": [sj.id]},
    )
    db_session.add(task)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.weekly_strategy_agent.settings.weekly_strategy_agent_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.weekly_strategy_agent.settings.weekly_strategy_agent_provider",
        "openai_compatible",
    )
    fake = FakeProvider()
    res = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            WEEKLY_STRATEGY_AGENT_TASK_TYPE: WeeklyStrategyAgentTaskHandler(
                db_session, WeeklyStrategyAgentService(db_session, fake)
            )
        },
    ).run_pending(1)
    saved = db_session.get(AgentTask, task.id)
    assert res["succeeded"] == 1
    assert saved.result_json["human_approval_required"] is True
    assert saved.result_json["side_effects_performed"] is False
    assert saved.result_json["allowed_mutation_scope"] == "agent_tasks_only"
    assert saved.result_json["stats_snapshot_hash"]
    assert saved.payload_json["stats_snapshot_json"]


def test_invalid_json_rejected():
    try:
        validate_model_output(
            "not-json",
            known_search_ids=set(),
            known_listing_external_ids=set(),
            known_evidence_refs=set(),
        )
        assert False
    except ValueError as exc:
        assert str(exc) == "weekly_strategy_agent_invalid_result"
