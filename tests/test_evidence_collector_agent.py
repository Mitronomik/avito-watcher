from __future__ import annotations

import json
import re

from sqlalchemy import select

from app.agents.contracts import AgentSideEffect
from app.agents.evidence_collector_agent import (
    EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
    EVIDENCE_CANDIDATES_SCHEMA_VERSION,
    EVIDENCE_COLLECTOR_TASK_TYPE,
    EvidenceCollectorAgentTaskHandler,
)
from app.agents.registry import get_agent_task_registry, get_agent_workflow_registry
from app.models.agent_artifact import AgentArtifact
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.market_evidence import MarketEvidenceItem
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_artifact_service import (
    compute_agent_artifact_content_hash,
    compute_agent_artifact_input_hash,
    serialize_agent_artifact,
    validate_agent_artifact_payload,
    validate_agent_artifact_source_refs,
)
from app.services.agent_orchestrator_service import AgentOrchestratorService
from app.services.agent_task_runner import AgentTaskRunner, get_registered_agent_task_handler_names

PAYLOAD_KEYS = {"schema_version", "artifact_type", "result_kind", "summary", "items", "limitations", "confidence", "notes", "metadata"}
FORBIDDEN_MARKERS = (
    "secret",
    "debug_html",
    "authorization",
    "cookie",
    "x-api-key",
    "bearer",
    "final_score",
    "final_verdict",
    "send_alert",
    "alert_sent",
    "investment_advice",
)


def _listing(external_id="ext-1", **kwargs):
    data = dict(external_id=external_id, url="https://example.test/listing", title="Safe title", price=1_000_000, area_m2=50, address="Safe area")
    data.update(kwargs)
    return Listing(**data)


def _task(db_session, **kwargs) -> AgentTask:
    data = dict(task_type=EVIDENCE_COLLECTOR_TASK_TYPE, dedupe_key=f"task-{len(db_session.scalars(select(AgentTask)).all())}")
    data.update(kwargs)
    return AgentTaskRepository(db_session).create_or_get_task(**data)


def _run(db_session, task: AgentTask):
    return AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={EVIDENCE_COLLECTOR_TASK_TYPE: EvidenceCollectorAgentTaskHandler(db_session)},
    ).run_pending(limit=10, task_type=task.task_type)


def test_registry_contract_marks_evidence_collector_implemented_internal_only():
    handlers = get_registered_agent_task_handler_names()
    contract = get_agent_task_registry()[EVIDENCE_COLLECTOR_TASK_TYPE]
    workflow = get_agent_workflow_registry()["listing_evidence_pipeline"]

    assert EVIDENCE_COLLECTOR_TASK_TYPE in handlers
    assert contract.implemented is True
    assert contract.handler_name == EVIDENCE_COLLECTOR_TASK_TYPE
    assert AgentSideEffect.WRITE_AGENT_TASK_RESULT in contract.declared_side_effects
    assert AgentSideEffect.WRITE_AGENT_ARTIFACT_FUTURE in contract.declared_side_effects
    assert AgentSideEffect.EXTERNAL_HTTP_CALL not in contract.declared_side_effects
    assert AgentSideEffect.EXTERNAL_LLM_CALL not in contract.declared_side_effects
    assert AgentSideEffect.RAG_WRITE_FUTURE not in contract.declared_side_effects
    assert workflow.implemented is False
    assert get_agent_task_registry()["evidence_normalizer_future"].implemented is False


def test_handler_success_creates_one_safe_artifact_payload(db_session):
    db_session.add(_listing())
    db_session.flush()
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx", search_job_id=7)

    result = _run(db_session, task)
    artifacts = db_session.scalars(select(AgentArtifact)).all()

    assert result["succeeded"] == 1
    assert task.status == "success"
    assert task.result_json["artifact_id"] == artifacts[0].id
    assert task.result_json["artifact_type"] == EVIDENCE_CANDIDATES_ARTIFACT_TYPE
    assert "payload_json" not in task.result_json
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.artifact_type == EVIDENCE_CANDIDATES_ARTIFACT_TYPE
    assert artifact.schema_version == EVIDENCE_CANDIDATES_SCHEMA_VERSION
    assert set(artifact.payload_json) == PAYLOAD_KEYS
    validate_agent_artifact_payload(
        artifact.payload_json,
        artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
        schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
    )
    validate_agent_artifact_source_refs(artifact.source_refs_json)
    assert artifact.payload_json["metadata"]["candidate_count"] <= 5
    assert len(artifact.payload_json["items"]) <= 5
    assert isinstance(artifact.source_refs_json, list)
    assert all(isinstance(ref, dict) for ref in artifact.source_refs_json)
    assert all(isinstance(item["source_refs"], list) for item in artifact.payload_json["items"])
    encoded = json.dumps(artifact.payload_json, ensure_ascii=False).lower()
    assert all(marker not in encoded for marker in FORBIDDEN_MARKERS)


def test_handler_skip_missing_listing_context_and_missing_row(db_session):
    no_external_id = _task(db_session)
    missing_row = _task(db_session, listing_external_id="missing")

    result = _run(db_session, no_external_id)
    assert result["skipped"] == 2
    assert no_external_id.result_json["skip_reason"] == "missing_listing_context"
    assert missing_row.result_json["skip_reason"] == "missing_listing_row"
    assert db_session.scalars(select(AgentArtifact)).all() == []


def test_existing_listing_with_insufficient_evidence_creates_empty_artifact(db_session):
    db_session.add(_listing("empty", title="", price=None, area_m2=None, address=""))
    db_session.flush()
    task = _task(db_session, listing_external_id="empty")

    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact))

    assert task.status == "success"
    assert artifact.payload_json["items"] == []
    assert artifact.payload_json["metadata"]["candidate_count"] == 0
    assert "insufficient_internal_listing_evidence" in artifact.payload_json["limitations"]
    assert artifact.payload_json["metadata"]["missing_data"]


def test_idempotency_per_task_reuses_existing_result_and_duplicate(db_session):
    db_session.add(_listing())
    db_session.flush()
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx")

    _run(db_session, task)
    first_id = task.result_json["artifact_id"]
    task.status = "pending"
    db_session.flush()
    _run(db_session, task)

    assert task.result_json["artifact_id"] == first_id
    assert len(db_session.scalars(select(AgentArtifact)).all()) == 1


def test_analysis_candidate_safe_and_no_side_effect_tables_or_mutations(db_session):
    listing = _listing()
    db_session.add(listing)
    db_session.flush()
    analysis = ListingAnalysis(
        listing_external_id="ext-1",
        context_key="ctx",
        status="success",
        input_hash="hash",
        score=99,
        verdict="buy",
        facts_json={"area": 50, "token": "must_not_be_copied"},
        risks_json={"risk": "x"},
    )
    db_session.add(analysis)
    db_session.flush()
    before_score = analysis.score
    before_verdict = analysis.verdict
    task = _task(db_session, listing_external_id="ext-1", listing_analysis_id=analysis.id, context_key="ctx")

    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact))
    encoded = json.dumps(artifact.payload_json, ensure_ascii=False).lower()

    assert "listing_analysis:" + str(analysis.id) in encoded
    assert "must_not_be_copied" not in encoded
    assert analysis.score == before_score
    assert analysis.verdict == before_verdict
    assert db_session.scalars(select(AlertSent)).all() == []
    assert db_session.scalars(select(HumanReview)).all() == []
    assert db_session.scalars(select(MarketEvidenceItem)).all() == []
    assert task.orchestration_status is None


def test_source_serialization_and_hash_candidate_determinism(db_session):
    db_session.add(_listing())
    db_session.flush()
    task = _task(db_session, listing_external_id="ext-1")
    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact))
    payload = artifact.payload_json
    dto = serialize_agent_artifact(artifact, include_payload=True)

    assert "payload_json" not in dto
    assert "raw_payload_json" not in json.dumps(dto).lower()
    assert "debug_html" not in json.dumps(dto).lower()
    assert payload["items"][0]["candidate_id"] == "listing_snapshot:ext-1"
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", payload["items"][0]["candidate_id"])
    assert compute_agent_artifact_input_hash({"b": 2, "a": 1}) == compute_agent_artifact_input_hash({"a": 1, "b": 2})
    content = compute_agent_artifact_content_hash(
        artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
        schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
        payload_json=payload,
    )
    changed = dict(payload, summary="changed")
    assert content != compute_agent_artifact_content_hash(
        artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
        schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
        payload_json=changed,
    )


def test_orchestrator_enabled_enqueues_only_root_then_runner_creates_artifact(db_session, monkeypatch):
    monkeypatch.setattr("app.services.agent_orchestrator_service.settings.agent_orchestration_enabled", True)
    db_session.add(_listing("orch-1"))
    db_session.flush()

    result = AgentOrchestratorService(db_session).enqueue_workflow(
        workflow_id="listing_evidence_pipeline",
        listing_external_id="orch-1",
        context_key="ctx",
        dry_run=False,
    )
    tasks = db_session.scalars(select(AgentTask)).all()

    assert result.ok is True
    assert len(result.enqueued_task_ids) == 1
    assert len(tasks) == 1
    assert tasks[0].task_type == EVIDENCE_COLLECTOR_TASK_TYPE
    assert tasks[0].workflow_id == "listing_evidence_pipeline"
    assert tasks[0].chain_depth == 0
    assert tasks[0].dependency_status == "ready"
    assert tasks[0].orchestration_status == "queued"
    assert db_session.scalars(select(AgentArtifact)).all() == []

    _run(db_session, tasks[0])
    assert len(db_session.scalars(select(AgentArtifact)).all()) == 1
    assert not db_session.scalars(select(AgentTask).where(AgentTask.task_type == "evidence_normalizer_future")).all()
