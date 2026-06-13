import json

from sqlalchemy import func, select

from app.agents.data_quality_agent import (
    DATA_QUALITY_AGENT_TASK_TYPE,
    DataQualityAgentTaskHandler,
)
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.knowledge_note import KnowledgeNote
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_detail_snapshot import ListingDetailSnapshot
from app.models.listing_enrichment import ListingEnrichment
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import (
    AgentTaskRunner,
    build_default_agent_task_handlers,
)
from app.services.data_quality_agent import DataQualityAgentService


class FakeClient:
    provider = "openai_compatible"
    model = "fake-model"

    def __init__(self, raw=None):
        self.calls = []
        self.raw = raw or json.dumps(
            {
                "schema_version": "data-quality-assessment-schema-v1",
                "overall_status": "needs_review",
                "review_priority": "medium",
                "should_human_review": True,
                "issues": [
                    {
                        "code": "extraction_missing",
                        "severity": "warning",
                        "message": "Missing extraction",
                        "evidence": [],
                        "rag_note_ids": [],
                        "confidence": 0.7,
                    }
                ],
                "contradictions": [],
                "missing_evidence": ["extraction_missing"],
                "uncertain_fields": [],
                "rag_references": [],
                "human_review_recommendations": [
                    {
                        "type": "rerun_detail_extraction",
                        "message": "Manual diagnostic review only.",
                        "related_issue_codes": ["extraction_missing"],
                    }
                ],
                "recommended_rule_patch": None,
                "confidence": 0.6,
            }
        )

    def complete(self, prompt):
        self.calls.append(prompt)
        return self.raw


def _listing(db):
    row = Listing(
        external_id="ext-dq",
        url="https://avito.test/1",
        title="Помещение 42 м²",
        price=1000,
        address="Москва",
        area_m2=42,
        published_label="today",
    )
    db.add(row)
    db.commit()
    return row


def test_service_success_persists_idempotently_and_no_side_effects(
    db_session, monkeypatch
):
    listing = _listing(db_session)
    analysis = ListingAnalysis(
        listing_external_id=listing.external_id,
        input_hash="h",
        score=7.0,
        verdict="watch",
    )
    snapshot = ListingDetailSnapshot(
        listing_id=listing.id,
        listing_external_id=listing.external_id,
        source_kind="manual",
        parse_status="success",
        content_hash="c",
        title="Помещение",
        description_text="desc",
    )
    db_session.add_all([analysis, snapshot])
    db_session.commit()
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_provider", "openai_compatible"
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_model", "fake-model"
    )
    client = FakeClient()
    service = DataQualityAgentService(db_session, client=client)
    first = service.assess(listing_external_id=listing.external_id)
    second = service.assess(listing_external_id=listing.external_id)
    db_session.commit()
    assert first.enrichment.id == second.enrichment.id
    assert len(client.calls) == 1
    assert first.enrichment.enrichment_type == "data_quality_assessment"
    assert first.enrichment.structured_facts_json["overall_status"] == "needs_review"
    assert db_session.get(ListingAnalysis, analysis.id).score == 7.0
    assert db_session.get(ListingAnalysis, analysis.id).verdict == "watch"
    assert db_session.scalar(select(func.count()).select_from(AlertSent)) == 0
    assert db_session.scalar(select(func.count()).select_from(KnowledgeNote)) == 0
    assert db_session.get(ListingDetailSnapshot, snapshot.id).content_hash == "c"


def test_disabled_skips_and_provider_off_fails_before_provider(db_session, monkeypatch):
    _listing(db_session)
    task = AgentTask(
        task_type=DATA_QUALITY_AGENT_TASK_TYPE,
        status="pending",
        payload_json={"listing_external_id": "ext-dq"},
        dedupe_key="dq-disabled",
    )
    db_session.add(task)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", False
    )
    client = FakeClient()
    result = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            DATA_QUALITY_AGENT_TASK_TYPE: DataQualityAgentTaskHandler(
                db_session, DataQualityAgentService(db_session, client=client)
            )
        },
    ).run_pending(10)
    assert result["skipped"] == 1
    assert not client.calls
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0


def test_manual_runner_default_handler_registered():
    handlers = build_default_agent_task_handlers(object())
    assert DATA_QUALITY_AGENT_TASK_TYPE in handlers


def test_dry_run_does_not_mutate_or_call_provider(db_session, monkeypatch):
    _listing(db_session)
    task = AgentTask(
        task_type=DATA_QUALITY_AGENT_TASK_TYPE,
        status="pending",
        payload_json={"listing_external_id": "ext-dq"},
        dedupe_key="dq-dry",
    )
    db_session.add(task)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", True
    )
    client = FakeClient()
    result = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            DATA_QUALITY_AGENT_TASK_TYPE: DataQualityAgentTaskHandler(
                db_session, DataQualityAgentService(db_session, client=client)
            )
        },
    ).run_pending(10, dry_run=True)
    assert result["dry_run"] is True
    assert db_session.get(AgentTask, task.id).status == "pending"
    assert not client.calls
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0


def test_missing_listing_fails_before_provider(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_data_quality_agent_enabled", True
    )
    monkeypatch.setattr(
        "app.services.data_quality_agent.settings.llm_provider", "openai_compatible"
    )
    client = FakeClient()
    task = AgentTask(
        task_type=DATA_QUALITY_AGENT_TASK_TYPE,
        status="pending",
        payload_json={"listing_external_id": "missing"},
        dedupe_key="dq-missing",
    )
    db_session.add(task)
    db_session.commit()
    result = AgentTaskRunner(
        AgentTaskRepository(db_session),
        {
            DATA_QUALITY_AGENT_TASK_TYPE: DataQualityAgentTaskHandler(
                db_session, DataQualityAgentService(db_session, client=client)
            )
        },
    ).run_pending(10)
    assert result["failed"] == 1
    assert task.error_type == "data_quality_listing_not_found"
    assert not client.calls
