from __future__ import annotations

from datetime import datetime

import json

import pytest
from sqlalchemy import select

from app.models.agent_artifact import AGENT_ARTIFACT_REDACTION_STATUSES, AGENT_ARTIFACT_TYPES, AgentArtifact
from app.models.agent_task import AgentTask
from app.services.agent_artifact_service import (
    AGENT_ARTIFACT_DTO_VERSION,
    AGENT_ARTIFACT_SCHEMA_VERSION,
    MAX_PREVIEW_CHARS,
    AgentArtifactValidationError,
    compute_agent_artifact_content_hash,
    compute_agent_artifact_input_hash,
    create_agent_artifact,
    find_duplicate_agent_artifact,
    get_latest_agent_artifact,
    serialize_agent_artifact,
    validate_agent_artifact_payload,
    validate_agent_artifact_source_refs,
)


def payload(**extra):
    base = {
        "schema_version": AGENT_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": "evidence_candidates",
        "result_kind": "artifact_payload",
        "summary": "safe summary",
        "items": [{"name": "item", "value": 1}],
        "limitations": ["not_investment_advice", "guaranteed_yield_claim_blocked"],
        "confidence": "medium",
        "notes": [],
    }
    base.update(extra)
    return base


def test_agent_artifact_model_metadata_constraints_indexes_and_fk():
    table = AgentArtifact.__table__
    assert table.name in AgentArtifact.metadata.tables
    assert {"id", "artifact_type", "schema_version", "listing_external_id", "listing_analysis_id", "search_job_id", "context_key", "source_task_id", "orchestration_run_id", "input_hash", "content_hash", "payload_json", "source_refs_json", "redaction_status", "created_at"} <= set(table.c.keys())
    constraints = {constraint.name for constraint in table.constraints}
    assert "ck_agent_artifacts_artifact_type" in constraints
    assert "ck_agent_artifacts_redaction_status" in constraints
    assert "ck_agent_artifacts_input_hash_not_empty" in constraints
    assert "ck_agent_artifacts_content_hash_not_empty" in constraints
    assert "ck_agent_artifacts_schema_version_not_empty" in constraints
    assert [fk.column.table.name for fk in table.c.source_task_id.foreign_keys] == ["agent_tasks"]
    assert all(fk.ondelete is None for fk in table.c.source_task_id.foreign_keys)
    indexes = {index.name for index in table.indexes}
    assert {"ix_agent_artifacts_artifact_type", "ix_agent_artifacts_listing_external_id", "ix_agent_artifacts_listing_analysis_id", "ix_agent_artifacts_search_job_id", "ix_agent_artifacts_context_key", "ix_agent_artifacts_source_task_id", "ix_agent_artifacts_orchestration_run_id", "ix_agent_artifacts_input_hash", "ix_agent_artifacts_content_hash", "ix_agent_artifacts_created_at"} <= indexes


def test_hashing_is_deterministic_and_separates_input_from_content():
    first = payload(items=[{"b": 2, "a": 1}])
    second = payload(items=[{"a": 1, "b": 2}])
    assert compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=first) == compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=second)
    changed = payload(summary="changed")
    assert compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=first) != compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=changed)
    other_type = dict(first, artifact_type="normalized_evidence")
    assert compute_agent_artifact_content_hash(artifact_type="normalized_evidence", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=other_type) != compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=first)
    other_schema = dict(first, schema_version="agent-artifact-schema-v2")
    assert compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version="agent-artifact-schema-v2", payload_json=other_schema) != compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=first)
    assert compute_agent_artifact_input_hash({"listing": "1", "task": "a"}) == compute_agent_artifact_input_hash({"task": "a", "listing": "1"})


@pytest.mark.parametrize("bad", ["raw_provider_payload", "debug_html", "headers", "cookies", "authorization", "guaranteed_yield", "final_score"])
def test_payload_forbidden_keys_rejected_and_safe_limitations_allowed(bad):
    with pytest.raises(AgentArtifactValidationError):
        validate_agent_artifact_payload(payload(metadata={bad: "x"}), artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION)
    validate_agent_artifact_payload(payload(), artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION)


def test_payload_envelope_and_source_ref_validation():
    with pytest.raises(AgentArtifactValidationError):
        validate_agent_artifact_payload(["not-object"], artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION)
    validate_agent_artifact_payload(payload(result_kind="evidence_candidates"), artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION)
    with pytest.raises(AgentArtifactValidationError):
        validate_agent_artifact_payload(payload(extra="nope"), artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION)
    validate_agent_artifact_source_refs([])
    validate_agent_artifact_source_refs([{"listing_external_id": "x", "source_kind": "market", "url_hash": "sha256:abc"}])
    for refs in ([{"table": "agent_tasks"}], [{"url": "https://example.test/raw"}], [{"token": "x"}], [{"debug_html": "<html>"}]):
        with pytest.raises(AgentArtifactValidationError):
            validate_agent_artifact_source_refs(refs)


def test_create_append_only_duplicate_detection_and_latest(db_session):
    content_hash = compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=payload())
    task = AgentTask(task_type="manual_review", dedupe_key="dedupe", payload_json={}, result_json={})
    db_session.add(task)
    db_session.flush()
    first = create_agent_artifact(db_session, artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, input_hash="input-a", content_hash=content_hash, payload_json=payload(), source_refs_json=[{"agent_task_id": task.id}], redaction_status="not_required", listing_external_id="ext-1", context_key="ctx", source_task_id=task.id)
    db_session.flush()
    second = create_agent_artifact(db_session, artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, input_hash="input-b", content_hash=content_hash, payload_json=payload(), source_refs_json=[], redaction_status="not_required", listing_external_id="ext-1", context_key="ctx", source_task_id=task.id)
    assert second.id != first.id
    duplicate = find_duplicate_agent_artifact(db_session, artifact_type="evidence_candidates", content_hash=content_hash, listing_external_id="ext-1", context_key="ctx", source_task_id=task.id)
    assert duplicate.id in {first.id, second.id}
    assert len(db_session.scalars(select(AgentArtifact)).all()) == 2
    first.created_at = datetime(2026, 1, 1)
    second.created_at = datetime(2026, 1, 2)
    db_session.flush()
    assert get_latest_agent_artifact(db_session, artifact_type="evidence_candidates", listing_external_id="ext-1", context_key="ctx").id == second.id


def test_validation_required_fields_and_enum_values(db_session):
    good = payload()
    content_hash = compute_agent_artifact_content_hash(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, payload_json=good)
    for kwargs in [
        {"artifact_type": "bad"},
        {"redaction_status": "bad"},
        {"input_hash": ""},
        {"content_hash": ""},
        {"schema_version": ""},
    ]:
        args = dict(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, input_hash="input", content_hash=content_hash, payload_json=good, source_refs_json=[], redaction_status="not_required")
        args.update(kwargs)
        with pytest.raises(AgentArtifactValidationError):
            create_agent_artifact(db_session, **args)
    assert tuple(AGENT_ARTIFACT_TYPES) == ("evidence_candidates", "normalized_evidence", "data_gap_report", "call_questions", "decision_wording", "claim_review", "report_draft", "offer_draft", "presentation_outline", "geo_context", "portfolio_memory_finding")
    assert tuple(AGENT_ARTIFACT_REDACTION_STATUSES) == ("not_required", "redacted", "blocked", "unknown")


def test_serialization_redaction_preview_bounds_and_forbidden_keys_absent(db_session):
    long_payload = payload(items=[{"text": "x" * 500} for _ in range(10)], metadata={"webhook_url": "https://example.test/?token=secret"})
    with pytest.raises(AgentArtifactValidationError):
        validate_agent_artifact_payload(long_payload, artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION)
    safe = payload(items=[{"text": "x" * 500} for _ in range(10)])
    artifact = AgentArtifact(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, input_hash="i", content_hash="c", payload_json=safe, source_refs_json=[{"listing_external_id": "ext"}], redaction_status="not_required")
    db_session.add(artifact)
    db_session.flush()
    dto = serialize_agent_artifact(artifact, include_payload=True)
    assert dto["schema_version"] == AGENT_ARTIFACT_DTO_VERSION
    assert dto["payload_available"] is True
    assert len(dto["payload_preview"]["items"]) == 5
    assert len(dto["payload_preview"]["items"][0]["text"]) == 300
    encoded = json.dumps(dto["payload_preview"], ensure_ascii=False, sort_keys=True)
    assert len(encoded) <= MAX_PREVIEW_CHARS
    forbidden = {"execution_endpoint", "http_method", "absolute_url", "auth_param", "raw_result_json", "raw_payload_json", "provider_payload", "debug_html", "payload_json"}
    assert forbidden.isdisjoint(dto)
    artifact.redaction_status = "blocked"
    blocked = serialize_agent_artifact(artifact, include_payload=True)
    assert blocked["payload_preview"] is None
    assert blocked["payload_available"] is False
    assert "safe_payload_json" not in blocked


def test_oversized_payload_preview_compacts_to_total_char_limit(db_session):
    safe = payload(
        summary="s" * 500,
        items=[{f"key_{idx}": "x" * 500 for idx in range(8)} for _ in range(20)],
        limitations=["not_investment_advice", "not_certified_appraisal", "not_valuation_report"],
        notes=["n" * 500 for _ in range(20)],
        metadata={f"meta_{idx}": "m" * 500 for idx in range(20)},
    )
    artifact = AgentArtifact(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, input_hash="i", content_hash="c", payload_json=safe, source_refs_json=[], redaction_status="not_required")
    db_session.add(artifact)
    db_session.flush()

    dto = serialize_agent_artifact(artifact)
    encoded = json.dumps(dto["payload_preview"], ensure_ascii=False, sort_keys=True)

    assert len(encoded) <= MAX_PREVIEW_CHARS
    assert "payload_preview_truncated" in dto["payload_preview"].get("limitations", [])
    assert "payload_json" not in dto


def test_admin_agent_artifacts_read_endpoints_auth_redaction_and_no_mutation(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.config import settings
    from app.db import session as db_session_module
    from app.db.base import Base
    from app.main import create_app

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        with Session() as s:
            yield s

    monkeypatch.setattr(settings, "admin_ui_read_key", "read")
    monkeypatch.setattr(settings, "admin_ui_technical_write_key", "tech")
    monkeypatch.setattr(settings, "admin_ui_allow_query_api_key", True)
    app = create_app(admin_ui_enabled=True)
    app.dependency_overrides[db_session_module.get_db] = override_db
    client = TestClient(app)

    safe = payload()
    with Session() as s:
        artifact = AgentArtifact(artifact_type="evidence_candidates", schema_version=AGENT_ARTIFACT_SCHEMA_VERSION, input_hash="input", content_hash="content", payload_json=safe, source_refs_json=[], redaction_status="not_required", listing_external_id="ext")
        s.add(artifact)
        s.commit()
        artifact_id = artifact.id
        before = s.scalar(select(func.count()).select_from(AgentArtifact))

    assert client.get("/api/admin/v1/agent-artifacts").status_code == 403
    assert client.get("/api/admin/v1/agent-artifacts?api_key=read").status_code == 403
    list_body = client.get("/api/admin/v1/agent-artifacts?limit=10&artifact_type=evidence_candidates", headers={"X-API-Key": "read"}).json()
    assert list_body["ok"] is True
    assert list_body["data"]["items"][0]["artifact_id"] == artifact_id
    assert "payload_json" not in list_body["data"]["items"][0]
    assert "execution_endpoint" not in str(list_body)
    detail_body = client.get(f"/api/admin/v1/agent-artifacts/{artifact_id}", headers={"X-API-Key": "read"}).json()
    assert detail_body["ok"] is True
    assert detail_body["data"]["safe_payload_json"]["summary"] == "safe summary"
    missing = client.get("/api/admin/v1/agent-artifacts/999", headers={"X-API-Key": "read"})
    assert missing.status_code == 404
    missing_body = missing.json()
    assert missing_body["ok"] is False
    assert missing_body["error"]["code"] == "not_found"

    with Session() as s:
        after = s.scalar(select(func.count()).select_from(AgentArtifact))
    assert before == after
