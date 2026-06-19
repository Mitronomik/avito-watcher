from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.agents.contracts import AgentSideEffect
from app.agents.evidence_collector_agent import EVIDENCE_CANDIDATES_ARTIFACT_TYPE, EVIDENCE_CANDIDATES_SCHEMA_VERSION
from app.agents.evidence_normalizer_agent import (
    EVIDENCE_NORMALIZER_TASK_TYPE,
    NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
    NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION,
    NORMALIZED_EVIDENCE_RESULT_KIND,
    EvidenceNormalizerAgentTaskHandler,
)
from app.agents.registry import get_agent_task_registry
from app.models.agent_artifact import AgentArtifact
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.market_evidence import MarketEvidenceItem
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_artifact_service import (
    AgentArtifactValidationError,
    compute_agent_artifact_content_hash,
    compute_agent_artifact_input_hash,
    create_agent_artifact,
    validate_agent_artifact_payload,
    validate_agent_artifact_source_refs,
)
from app.services.agent_orchestrator_service import AgentOrchestratorService
from app.services.agent_task_runner import AgentTaskRunner, build_default_agent_task_handlers, get_registered_agent_task_handler_names

FORBIDDEN_MARKERS = (
    "token",
    "api_key",
    "secret",
    "cookie",
    "authorization",
    "bearer",
    "password",
    "credential",
    "raw_payload",
    "debug_html",
    "provider_payload",
)


def _listing(external_id="ext-1", **kwargs):
    data = dict(external_id=external_id, url="https://example.test/listing", title="Safe", price=100, area_m2=10, address="Safe")
    data.update(kwargs)
    return Listing(**data)


def _task(db_session, **kwargs) -> AgentTask:
    data = dict(task_type=EVIDENCE_NORMALIZER_TASK_TYPE, dedupe_key=f"normalizer-{len(db_session.scalars(select(AgentTask)).all())}")
    data.update(kwargs)
    return AgentTaskRepository(db_session).create_or_get_task(**data)


def _collector_task(db_session, **kwargs) -> AgentTask:
    data = dict(task_type="evidence_collector_future", dedupe_key=f"collector-{len(db_session.scalars(select(AgentTask)).all())}")
    data.update(kwargs)
    return AgentTaskRepository(db_session).create_or_get_task(**data)


def _source_payload(items=None, **overrides):
    payload = {
        "schema_version": EVIDENCE_CANDIDATES_SCHEMA_VERSION,
        "artifact_type": EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
        "result_kind": EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
        "summary": "Collected internal evidence candidates for listing.",
        "items": [] if items is None else items,
        "limitations": [],
        "confidence": 0.0,
        "notes": [],
        "metadata": {"collector_version": "evidence-collector-v0", "candidate_count": len([] if items is None else items)},
    }
    payload.update(overrides)
    return payload


def _artifact(db_session, *, listing_external_id="ext-1", context_key="ctx", source_task_id=None, orchestration_run_id=None, payload=None, artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE, schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION):
    payload = payload or _source_payload()
    input_hash = compute_agent_artifact_input_hash({"source_task_id": source_task_id, "payload": payload, "n": len(db_session.scalars(select(AgentArtifact)).all())})
    content_hash = compute_agent_artifact_content_hash(artifact_type=artifact_type, schema_version=schema_version, payload_json=payload)
    return create_agent_artifact(
        db_session,
        artifact_type=artifact_type,
        schema_version=schema_version,
        input_hash=input_hash,
        content_hash=content_hash,
        payload_json=payload,
        source_refs_json=[{"agent_task_id": source_task_id or 1, "listing_external_id": listing_external_id}],
        redaction_status="not_required",
        listing_external_id=listing_external_id,
        context_key=context_key,
        source_task_id=source_task_id,
        orchestration_run_id=orchestration_run_id,
    )


def _run(db_session, task: AgentTask):
    return AgentTaskRunner(
        AgentTaskRepository(db_session),
        handlers={EVIDENCE_NORMALIZER_TASK_TYPE: EvidenceNormalizerAgentTaskHandler(db_session)},
    ).run_pending(limit=10, task_type=task.task_type)


def test_registry_and_workflow_planning_marks_normalizer_implemented(db_session):
    contract = get_agent_task_registry()[EVIDENCE_NORMALIZER_TASK_TYPE]
    handlers = get_registered_agent_task_handler_names()
    plan = AgentOrchestratorService(db_session).build_plan(workflow_id="listing_evidence_pipeline")

    assert EVIDENCE_NORMALIZER_TASK_TYPE in handlers
    assert contract.implemented is True
    assert contract.handler_name == EVIDENCE_NORMALIZER_TASK_TYPE
    assert contract.task_class == "data_normalization"
    assert contract.safety_category == "read_only_normalization"
    assert contract.required_permission_refs == ("api.meta.read",)
    assert contract.output_schema["recommended_envelope"]["result_kind"] == NORMALIZED_EVIDENCE_RESULT_KIND
    assert set(contract.declared_side_effects) == {AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.WRITE_AGENT_ARTIFACT_FUTURE}
    assert AgentSideEffect.EXTERNAL_HTTP_CALL not in contract.declared_side_effects
    assert AgentSideEffect.EXTERNAL_LLM_CALL not in contract.declared_side_effects
    assert AgentSideEffect.RAG_READ not in contract.declared_side_effects
    assert AgentSideEffect.RAG_WRITE_FUTURE not in contract.declared_side_effects
    assert {effect.value for effect in AgentSideEffect} == {"none", "write_agent_task_result", "write_agent_artifact_future", "external_llm_call", "external_http_call", "rag_read", "rag_write_future", "admin_display_only"}
    nodes = {node.task_type: node for node in plan.nodes}
    assert nodes["evidence_collector_future"].handler_implemented is True
    assert nodes[EVIDENCE_NORMALIZER_TASK_TYPE].handler_implemented is True
    assert nodes[EVIDENCE_NORMALIZER_TASK_TYPE].can_enqueue is False
    assert nodes[EVIDENCE_NORMALIZER_TASK_TYPE].blocked_reason == "non_root_node"


def test_handler_registration_instantiates(db_session):
    handlers = build_default_agent_task_handlers(db_session)
    assert EVIDENCE_NORMALIZER_TASK_TYPE in handlers
    assert isinstance(handlers[EVIDENCE_NORMALIZER_TASK_TYPE], EvidenceNormalizerAgentTaskHandler)


@pytest.mark.parametrize("mode", ["payload", "depends", "parent", "orch", "listing_ctx"])
def test_source_lookup_modes(db_session, mode):
    db_session.add(_listing())
    db_session.flush()
    collector = _collector_task(db_session, listing_external_id="ext-1")
    source = _artifact(db_session, source_task_id=collector.id, orchestration_run_id="run-1")
    kwargs = {"listing_external_id": "ext-1", "context_key": "ctx"}
    if mode == "payload":
        kwargs["payload_json"] = {"source_artifact_id": source.id}
    elif mode == "depends":
        kwargs["depends_on_task_id"] = collector.id
    elif mode == "parent":
        kwargs["parent_task_id"] = collector.id
    elif mode == "orch":
        kwargs["orchestration_run_id"] = "run-1"
    task = _task(db_session, **kwargs)

    _run(db_session, task)

    assert task.status == "success"
    assert task.result_json["source_artifact_id"] == source.id


def test_ambiguous_lookup_skips_without_artifact(db_session):
    db_session.add(_listing())
    db_session.flush()
    _artifact(db_session)
    _artifact(db_session)
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx")

    _run(db_session, task)

    assert task.status == "skipped"
    assert task.result_json["reason"] == "ambiguous_source_artifact"
    assert db_session.scalars(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE)).all() == []


@pytest.mark.parametrize(
    ("case", "task_kwargs", "artifact_kwargs", "payload", "reason"),
    [
        ("missing_listing_external_id", {}, None, None, "missing_listing_context"),
        ("missing_listing_row", {"listing_external_id": "missing"}, None, None, "missing_listing_row"),
        ("missing_source", {"listing_external_id": "ext-1"}, None, None, "missing_source_artifact"),
        ("wrong_payload_type", {"listing_external_id": "ext-1"}, {}, [], "invalid_source_payload"),
        ("wrong_payload_schema", {"listing_external_id": "ext-1"}, {}, _source_payload(schema_version="wrong"), "wrong_source_schema_version"),
        ("wrong_payload_artifact", {"listing_external_id": "ext-1"}, {}, _source_payload(artifact_type="wrong"), "wrong_source_artifact_type"),
        ("wrong_result", {"listing_external_id": "ext-1"}, {}, _source_payload(result_kind="artifact_payload"), "wrong_source_result_kind"),
        ("unknown_key", {"listing_external_id": "ext-1"}, {}, {**_source_payload(), "extra": True}, "invalid_source_payload_envelope"),
        ("items_not_list", {"listing_external_id": "ext-1"}, {}, _source_payload(items="bad"), "source_items_not_list"),
    ],
)
def test_skip_policy(db_session, case, task_kwargs, artifact_kwargs, payload, reason):
    if case != "missing_listing_row":
        db_session.add(_listing())
        db_session.flush()
    if artifact_kwargs is not None:
        source = AgentArtifact(
            artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            input_hash=f"input-{case}",
            content_hash=f"content-{case}",
            payload_json=payload,
            source_refs_json=[],
            redaction_status="not_required",
            listing_external_id="ext-1",
            context_key="ctx",
        )
        db_session.add(source)
        db_session.flush()
        task_kwargs.setdefault("payload_json", {"source_artifact_id": source.id})
        task_kwargs.setdefault("context_key", "ctx")
    task = _task(db_session, **task_kwargs)

    _run(db_session, task)

    assert task.status == "skipped"
    assert task.result_json["reason"] == reason
    assert db_session.scalars(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE)).all() == []


def test_empty_source_candidates_creates_empty_normalized_artifact(db_session):
    db_session.add(_listing())
    db_session.flush()
    source = _artifact(db_session, payload=_source_payload(items=[]))
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx", payload_json={"source_artifact_id": source.id})

    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE))

    assert task.status == "success"
    assert artifact.schema_version == NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION
    assert artifact.payload_json["result_kind"] == NORMALIZED_EVIDENCE_RESULT_KIND
    assert artifact.payload_json["metadata"]["normalization_kind"] == "normalized_evidence_candidates"
    assert artifact.payload_json["items"] == []
    assert artifact.payload_json["metadata"]["normalized_count"] == 0
    assert artifact.payload_json["metadata"]["candidate_count"] == 0
    assert artifact.payload_json["metadata"]["source_candidate_count"] == 0
    assert "no_source_candidates" in artifact.payload_json["limitations"]


def test_success_normalizes_listing_snapshot_and_source_refs(db_session):
    db_session.add(_listing())
    db_session.flush()
    item = {"candidate_id": "listing_snapshot:123", "evidence_kind": "listing_snapshot", "source": "internal", "observed_value": {"price": 84900, "area_m2": 52, "price_per_m2": 1632.69}}
    source = _artifact(db_session, payload=_source_payload(items=[item]))
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx", payload_json={"source_artifact_id": source.id})

    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE))
    out = artifact.payload_json["items"][0]

    assert out["normalized_candidate_id"] == f"normalized:{source.id}:listing_snapshot:123"
    assert out["source_candidate_id"] == "listing_snapshot:123"
    assert out["source_artifact_id"] == source.id
    assert out["evidence_kind"] == "listing_snapshot"
    assert out["source"] == "internal"
    assert out["normalization_status"] == "normalized"
    assert out["normalized_values"] == {"price_rub": 84900, "area_m2": 52, "price_per_m2_rub": 1632.69}
    assert artifact.payload_json["metadata"]["source_artifact_id"] == source.id
    validate_agent_artifact_source_refs(artifact.source_refs_json)
    assert all(isinstance(ref, dict) for ref in artifact.source_refs_json)
    encoded_refs = json.dumps(artifact.source_refs_json)
    assert "agent_task:" not in encoded_refs and "artifact:" not in encoded_refs
    assert "source_ref_id" in artifact.source_refs_json[0]
    assert not ({"artifact_id", "source_artifact_id", "source_task_id"} & set(artifact.source_refs_json[0]))


def test_computes_price_per_m2_and_insufficient_item(db_session):
    db_session.add(_listing())
    db_session.flush()
    items = [
        {"candidate_id": "calc", "evidence_kind": "listing_snapshot", "source": "internal", "observed_value": {"price": 1000, "area_m2": 3}},
        {"candidate_id": "none", "evidence_kind": "listing_analysis_summary", "source": "internal", "observed_value": {"fact_keys": ["x"]}},
    ]
    source = _artifact(db_session, payload=_source_payload(items=items))
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx", payload_json={"source_artifact_id": source.id})

    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE))
    calc, none = artifact.payload_json["items"]

    assert calc["normalized_values"]["price_per_m2_rub"] == round(1000 / 3, 2)
    assert none["normalization_status"] == "insufficient"
    assert none["normalized_values"] == {}
    assert "no_numeric_observed_values" in none["quality_flags"]


def test_idempotency_reuses_result_and_duplicate(db_session):
    db_session.add(_listing())
    db_session.flush()
    source = _artifact(db_session, payload=_source_payload(items=[{"candidate_id": "x", "observed_value": {"price": 1}}]))
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx", payload_json={"source_artifact_id": source.id})

    _run(db_session, task)
    first_id = task.result_json["artifact_id"]
    task.status = "pending"
    db_session.flush()
    _run(db_session, task)
    assert task.result_json["artifact_id"] == first_id
    assert len(db_session.scalars(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE)).all()) == 1

    task2 = _task(db_session, listing_external_id="ext-1", context_key="ctx", payload_json={"source_artifact_id": source.id})
    _run(db_session, task2)
    assert task2.result_json["artifact_id"] != first_id
    task2.status = "pending"
    task2.result_json = {}
    db_session.flush()
    _run(db_session, task2)
    assert len(db_session.scalars(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE)).all()) == 2


def test_sensitive_data_rejection_and_output_safety(db_session):
    with pytest.raises(AgentArtifactValidationError):
        validate_agent_artifact_payload(
            {**_source_payload(), "metadata": {"token": "blocked"}},
            artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
        )
    db_session.add(_listing())
    db_session.flush()
    source = _artifact(db_session, payload=_source_payload(items=[{"candidate_id": "safe", "observed_value": {"price": 1}}]))
    task = _task(db_session, listing_external_id="ext-1", context_key="ctx", payload_json={"source_artifact_id": source.id})

    _run(db_session, task)
    artifact = db_session.scalar(select(AgentArtifact).where(AgentArtifact.artifact_type == NORMALIZED_EVIDENCE_ARTIFACT_TYPE))
    encoded = json.dumps(artifact.payload_json, ensure_ascii=False).lower()

    assert all(marker not in encoded for marker in FORBIDDEN_MARKERS)


def test_no_side_effect_tables_or_analysis_mutations(db_session):
    db_session.add(_listing())
    db_session.flush()
    analysis = ListingAnalysis(listing_external_id="ext-1", context_key="ctx", status="success", input_hash="h", score=10, verdict="buy", facts_json={})
    db_session.add(analysis)
    db_session.flush()
    source = _artifact(db_session, payload=_source_payload(items=[{"candidate_id": "safe", "observed_value": {"price": 1}}]))
    task = _task(db_session, listing_external_id="ext-1", listing_analysis_id=analysis.id, context_key="ctx", payload_json={"source_artifact_id": source.id})

    _run(db_session, task)

    assert analysis.score == 10
    assert analysis.verdict == "buy"
    assert task.orchestration_status is None
    assert db_session.scalars(select(AlertSent)).all() == []
    assert db_session.scalars(select(HumanReview)).all() == []
    assert db_session.scalars(select(MarketEvidenceItem)).all() == []
