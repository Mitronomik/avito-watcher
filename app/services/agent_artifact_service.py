from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin_v1.redaction import redact_api_response
from app.models.agent_artifact import AGENT_ARTIFACT_REDACTION_STATUSES, AGENT_ARTIFACT_TYPES, AgentArtifact

AGENT_ARTIFACT_DTO_VERSION = "agent-artifact-v1"
AGENT_ARTIFACT_POLICY_VERSION = "agent-artifact-policy-v1"
AGENT_ARTIFACT_SCHEMA_VERSION = "agent-artifact-schema-v1"
AGENT_ARTIFACT_LABEL_VERSION = "agent-artifact-labels-v1"

ALLOWED_PAYLOAD_KEYS = {"schema_version", "artifact_type", "result_kind", "summary", "items", "limitations", "confidence", "notes", "metadata"}
FORBIDDEN_PAYLOAD_KEYS = {
    "final_score", "final_verdict", "mutate_score", "mutate_verdict", "mutate_filters", "mutate_formula",
    "send_alert", "alert_sent", "confirmed_market_value", "guaranteed_yield", "guaranteed_rent",
    "guaranteed_value", "legal_advice", "tax_advice", "investment_advice", "valuation_opinion",
    "certified_appraisal", "raw_provider_payload", "debug_html", "headers", "cookies", "authorization",
    "api_key", "token", "secret", "password", "webhook_url",
}
ALLOWED_SOURCE_REF_KEYS = {
    "listing_id", "listing_external_id", "listing_analysis_id", "search_job_id", "agent_task_id",
    "human_review_id", "market_evidence_ids", "decision_card_input_hash", "risk_attention_input_hash",
    "readiness_checklist_input_hash", "price_position_input_hash", "knowledge_note_ids",
    "source_task_id_future", "artifact_ids_future", "source_kind", "source_ref_id", "source_hash",
    "url_hash", "source_checked_at", "source_expires_at", "source_confidence", "note",
}
FORBIDDEN_SOURCE_REF_KEYS = {"table", "field", "url", "raw_url", "headers", "cookies", "authorization", "api_key", "token", "secret", "password", "provider_payload", "raw_provider_payload", "debug_html"}

MAX_PREVIEW_ITEMS = 5
MAX_PREVIEW_STRING = 300
MAX_PREVIEW_CHARS = 2000

class AgentArtifactValidationError(ValueError):
    pass


def _labels() -> tuple[dict[str, Any], dict[str, Any]]:
    from app.api.admin_v1.meta_contract import ENUM_LABELS
    return ENUM_LABELS["agent_artifact_type"], ENUM_LABELS["agent_artifact_redaction_status"]


def _validate_non_empty(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AgentArtifactValidationError(f"{field} is required")


def _walk_keys(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_PAYLOAD_KEYS:
                raise AgentArtifactValidationError(f"forbidden payload key: {'.'.join(path + (str(key),))}")
            _walk_keys(item, path + (str(key),))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _walk_keys(item, path + (str(idx),))


def validate_agent_artifact_payload(payload_json: Any, *, artifact_type: str, schema_version: str) -> dict[str, Any]:
    if artifact_type not in AGENT_ARTIFACT_TYPES:
        raise AgentArtifactValidationError("unknown artifact_type")
    _validate_non_empty(schema_version, "schema_version")
    if not isinstance(payload_json, dict):
        raise AgentArtifactValidationError("payload_json must be a JSON object")
    unknown = set(payload_json) - ALLOWED_PAYLOAD_KEYS
    if unknown:
        raise AgentArtifactValidationError(f"unknown payload key: {sorted(unknown)[0]}")
    if payload_json.get("schema_version") != schema_version:
        raise AgentArtifactValidationError("payload schema_version mismatch")
    if payload_json.get("artifact_type") != artifact_type:
        raise AgentArtifactValidationError("payload artifact_type mismatch")
    if payload_json.get("result_kind") != "artifact_payload":
        raise AgentArtifactValidationError("payload result_kind must be artifact_payload")
    _walk_keys(payload_json)
    return payload_json


def validate_agent_artifact_source_refs(source_refs_json: Any) -> list[dict[str, Any]] | dict[str, Any]:
    if not isinstance(source_refs_json, (list, dict)):
        raise AgentArtifactValidationError("source_refs_json must be a JSON list or object")
    refs = source_refs_json if isinstance(source_refs_json, list) else [source_refs_json]
    for ref in refs:
        if not isinstance(ref, dict):
            raise AgentArtifactValidationError("source refs must be objects")
        for key in ref:
            normalized = str(key).lower()
            if normalized in FORBIDDEN_SOURCE_REF_KEYS or normalized not in ALLOWED_SOURCE_REF_KEYS:
                raise AgentArtifactValidationError(f"unknown or unsafe source ref key: {key}")
    return source_refs_json


def canonicalize_agent_artifact_payload(payload_json: Mapping[str, Any]) -> str:
    return json.dumps(payload_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_agent_artifact_input_hash(input_envelope: Mapping[str, Any]) -> str:
    canonical = json.dumps(input_envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_agent_artifact_content_hash(*, artifact_type: str, schema_version: str, payload_json: Mapping[str, Any]) -> str:
    validate_agent_artifact_payload(payload_json, artifact_type=artifact_type, schema_version=schema_version)
    envelope = {"artifact_type": artifact_type, "schema_version": schema_version, "payload_json": payload_json}
    return hashlib.sha256(canonicalize_agent_artifact_payload(envelope).encode("utf-8")).hexdigest()


def create_agent_artifact(db: Session, *, artifact_type: str, schema_version: str, input_hash: str, content_hash: str, payload_json: dict[str, Any], source_refs_json: list[dict[str, Any]] | dict[str, Any], redaction_status: str, listing_external_id: str | None = None, listing_analysis_id: int | None = None, search_job_id: int | None = None, context_key: str | None = None, source_task_id: int | None = None, orchestration_run_id: str | None = None, dedupe: bool = False) -> AgentArtifact:
    if redaction_status not in AGENT_ARTIFACT_REDACTION_STATUSES:
        raise AgentArtifactValidationError("unknown redaction_status")
    _validate_non_empty(input_hash, "input_hash")
    _validate_non_empty(content_hash, "content_hash")
    validate_agent_artifact_payload(payload_json, artifact_type=artifact_type, schema_version=schema_version)
    validate_agent_artifact_source_refs(source_refs_json)
    if dedupe:
        existing = find_duplicate_agent_artifact(db, artifact_type=artifact_type, content_hash=content_hash, listing_external_id=listing_external_id, context_key=context_key, source_task_id=source_task_id)
        if existing is not None:
            return existing
    artifact = AgentArtifact(artifact_type=artifact_type, schema_version=schema_version, input_hash=input_hash, content_hash=content_hash, payload_json=payload_json, source_refs_json=source_refs_json, redaction_status=redaction_status, listing_external_id=listing_external_id, listing_analysis_id=listing_analysis_id, search_job_id=search_job_id, context_key=context_key, source_task_id=source_task_id, orchestration_run_id=orchestration_run_id)
    db.add(artifact)
    db.flush()
    return artifact


def find_duplicate_agent_artifact(db: Session, *, artifact_type: str, content_hash: str, listing_external_id: str | None, context_key: str | None, source_task_id: int | None) -> AgentArtifact | None:
    return db.scalar(select(AgentArtifact).where(AgentArtifact.artifact_type == artifact_type, AgentArtifact.content_hash == content_hash, AgentArtifact.listing_external_id == listing_external_id, AgentArtifact.context_key == context_key, AgentArtifact.source_task_id == source_task_id).order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc()))


def get_agent_artifact_by_id(db: Session, artifact_id: int) -> AgentArtifact | None:
    return db.get(AgentArtifact, artifact_id)


def list_agent_artifacts(db: Session, *, artifact_type: str | None = None, listing_external_id: str | None = None, listing_analysis_id: int | None = None, search_job_id: int | None = None, source_task_id: int | None = None, orchestration_run_id: str | None = None, context_key: str | None = None, limit: int = 50, offset: int = 0) -> list[AgentArtifact]:
    stmt = select(AgentArtifact)
    for field, value in ((AgentArtifact.artifact_type, artifact_type), (AgentArtifact.listing_external_id, listing_external_id), (AgentArtifact.listing_analysis_id, listing_analysis_id), (AgentArtifact.search_job_id, search_job_id), (AgentArtifact.source_task_id, source_task_id), (AgentArtifact.orchestration_run_id, orchestration_run_id), (AgentArtifact.context_key, context_key)):
        if value is not None:
            stmt = stmt.where(field == value)
    return list(db.scalars(stmt.order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc()).offset(offset).limit(limit)))


def get_latest_agent_artifact(db: Session, *, artifact_type: str, listing_external_id: str | None = None, context_key: str | None = None) -> AgentArtifact | None:
    stmt = select(AgentArtifact).where(AgentArtifact.artifact_type == artifact_type)
    if listing_external_id is not None:
        stmt = stmt.where(AgentArtifact.listing_external_id == listing_external_id)
    if context_key is not None:
        stmt = stmt.where(AgentArtifact.context_key == context_key)
    return db.scalar(stmt.order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc()))


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, str):
        return value[:MAX_PREVIEW_STRING]
    if isinstance(value, list):
        return [_bounded(item, depth=depth + 1) for item in value[:MAX_PREVIEW_ITEMS]]
    if isinstance(value, dict):
        return {key: _bounded(item, depth=depth + 1) for key, item in list(value.items())[:MAX_PREVIEW_ITEMS] if str(key).lower() not in FORBIDDEN_PAYLOAD_KEYS}
    return value


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_api_response(payload)
    if not isinstance(redacted, dict):
        return {}
    return _bounded(redacted)


def _preview(payload: dict[str, Any]) -> dict[str, Any]:
    preview = _safe_payload(payload)
    encoded = json.dumps(preview, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_PREVIEW_CHARS:
        preview["limitations"] = list(preview.get("limitations") or [])[:MAX_PREVIEW_ITEMS] + ["payload_preview_truncated"]
    return preview


def serialize_agent_artifact(artifact: AgentArtifact, *, include_payload: bool = False) -> dict[str, Any]:
    type_labels, redaction_labels = _labels()
    blocked = artifact.redaction_status in {"blocked", "unknown"}
    payload_preview = None if blocked else _preview(artifact.payload_json)
    dto = {
        "schema_version": AGENT_ARTIFACT_DTO_VERSION,
        "artifact_id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "artifact_type_label": type_labels.get(artifact.artifact_type),
        "artifact_schema_version": artifact.schema_version,
        "listing_external_id": artifact.listing_external_id,
        "listing_analysis_id": artifact.listing_analysis_id,
        "search_job_id": artifact.search_job_id,
        "context_key": artifact.context_key,
        "source_task_id": artifact.source_task_id,
        "orchestration_run_id": artifact.orchestration_run_id,
        "input_hash": artifact.input_hash,
        "content_hash": artifact.content_hash,
        "redaction_status": artifact.redaction_status,
        "redaction_status_label": redaction_labels.get(artifact.redaction_status),
        "payload_preview": payload_preview,
        "payload_available": not blocked and payload_preview is not None,
        "source_refs": redact_api_response(artifact.source_refs_json),
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "limitations": list(artifact.payload_json.get("limitations") or []),
    }
    if include_payload and not blocked:
        dto["safe_payload_json"] = _safe_payload(artifact.payload_json)
    return dto
