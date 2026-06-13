import json

from sqlalchemy import func, select

from app.agents.listing_detail_extraction import (
    LISTING_DETAIL_EXTRACTION_TASK_TYPE,
    ListingDetailExtractionAgentTaskHandler,
)
from app.models.agent_task import AgentTask
from app.models.listing_enrichment import ListingEnrichment
from app.models.listing_detail_snapshot import ListingDetailSnapshot
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_task_runner import (
    AgentTaskRunner,
    build_default_agent_task_handlers,
)
from app.services.listing_detail_extraction import ListingDetailExtractionService


class FakeClient:
    provider = "openai_compatible"
    model = "fake-model"

    def __init__(self, raw=None, fail=False):
        self.calls = 0
        self.raw = raw
        self.fail = fail

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.raw or json.dumps(
            {
                "schema_version": "listing-detail-extraction-schema-v1",
                "structured_facts": {"area_m2": 42},
                "field_confidence": {"area_m2": 0.9},
                "evidence": [
                    {
                        "field": "area_m2",
                        "value": 42,
                        "confidence": 0.9,
                        "source_field": "attributes_json",
                        "snippet": "Общая площадь: 42 м²",
                    }
                ],
                "missing_fields": [],
                "uncertain_fields": [],
                "contradictions": [],
                "confidence": 0.9,
            }
        )


def _snapshot(db_session, external_id="8147836490"):
    row = ListingDetailSnapshot(
        listing_external_id=external_id,
        source_kind="fixture",
        fetch_status="not_applicable",
        parse_status="success",
        content_hash="hash1",
        title="Помещение",
        description_text="42 м²",
        attributes_json={"Общая площадь": "42 м²"},
        facts_json={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _task(db_session, payload, external_id="8147836490"):
    task = AgentTask(
        task_type=LISTING_DETAIL_EXTRACTION_TASK_TYPE,
        status="pending",
        dedupe_key=f"d-{external_id}-{len(payload)}-{payload.get('snapshot_id')}-{id(payload)}",
        listing_external_id=external_id,
        payload_json=payload,
    )
    db_session.add(task)
    db_session.flush()
    return task


def test_manual_task_succeeds_idempotently(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_listing_detail_extraction_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_provider",
        "openai_compatible",
    )
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_model", "fake-model"
    )
    snap = _snapshot(db_session)
    client = FakeClient()
    _task(db_session, {"snapshot_id": snap.id, "extraction_profile": "commercial_rent"})
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, client)
            )
        },
    )
    result = runner.run_pending(limit=10)
    assert result["succeeded"] == 1
    assert client.calls == 1
    row = db_session.scalar(select(ListingEnrichment))
    assert row.structured_facts_json["area_m2"] == 42
    assert row.field_confidence_json["area_m2"] == 0.9
    assert row.evidence_json[0]["source_field"] == "attributes_json"
    _task(db_session, {"snapshot_id": snap.id, "extraction_profile": "commercial_rent"})
    result = runner.run_pending(limit=10)
    assert result["succeeded"] == 1
    assert client.calls == 1
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 1


def test_disabled_skips_without_provider(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_listing_detail_extraction_enabled",
        False,
    )
    snap = _snapshot(db_session)
    client = FakeClient()
    task = _task(db_session, {"snapshot_id": snap.id})
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, client)
            )
        },
    )
    result = runner.run_pending(limit=10)
    assert result["skipped"] == 1
    assert client.calls == 0
    assert task.result_json["error_type"] == "listing_detail_extraction_disabled"
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0


def test_dry_run_no_mutation(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_listing_detail_extraction_enabled",
        True,
    )
    snap = _snapshot(db_session)
    client = FakeClient()
    task = _task(db_session, {"snapshot_id": snap.id})
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, client)
            )
        },
    )
    result = runner.run_pending(limit=10, dry_run=True)
    assert result["pending"] == 1
    assert client.calls == 0
    assert task.status == "pending"
    assert task.result_json is None or task.result_json == {}
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0


def test_missing_snapshot_and_malformed_output_fail(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_listing_detail_extraction_enabled",
        True,
    )
    client = FakeClient()
    _task(db_session, {"snapshot_id": 999})
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, client)
            )
        },
    )
    runner.run_pending(limit=10)
    task = db_session.scalar(select(AgentTask))
    assert task.status == "failed"
    assert task.error_type == "listing_detail_snapshot_not_found"
    assert client.calls == 0
    snap = _snapshot(db_session, "2")
    bad_client = FakeClient(raw="```json\n{}\n```")
    _task(db_session, {"snapshot_id": snap.id}, external_id="2")
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, bad_client)
            )
        },
    )
    runner.run_pending(limit=10)
    assert bad_client.calls == 1
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0


def test_default_handlers_register_manual_handler_and_no_boundary_imports():
    handlers = build_default_agent_task_handlers(object())
    assert LISTING_DETAIL_EXTRACTION_TASK_TYPE in handlers
    for path in [
        "app/services/monitor_service.py",
        "app/workers/monitor.py",
        "app/agents/review_copilot.py",
        "app/services/knowledge_retrieval.py",
        "app/analysis/provider.py",
    ]:
        assert "listing_detail_extraction" not in open(path, encoding="utf-8").read()


def test_failed_attempt_does_not_block_later_retry(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_listing_detail_extraction_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_provider",
        "openai_compatible",
    )
    monkeypatch.setattr(
        "app.services.listing_detail_extraction.settings.llm_model", "fake-model"
    )
    snap = _snapshot(db_session)
    failing = FakeClient(fail=True)
    _task(db_session, {"snapshot_id": snap.id})
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, failing)
            )
        },
    )
    result = runner.run_pending(limit=10)
    assert result["failed"] == 1
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 0

    succeeding = FakeClient()
    _task(db_session, {"snapshot_id": snap.id})
    runner = AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={
            LISTING_DETAIL_EXTRACTION_TASK_TYPE: ListingDetailExtractionAgentTaskHandler(
                db_session, ListingDetailExtractionService(db_session, succeeding)
            )
        },
    )
    result = runner.run_pending(limit=10)
    assert result["succeeded"] == 1
    assert succeeding.calls == 1
    assert db_session.scalar(select(func.count()).select_from(ListingEnrichment)) == 1
