from __future__ import annotations

import hashlib
import json
from numbers import Number
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.evidence_collector_agent import EVIDENCE_CANDIDATES_ARTIFACT_TYPE, EVIDENCE_CANDIDATES_SCHEMA_VERSION
from app.models.agent_artifact import AgentArtifact
from app.models.agent_task import AgentTask
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.services.agent_artifact_service import (
    ALLOWED_PAYLOAD_KEYS,
    compute_agent_artifact_content_hash,
    compute_agent_artifact_input_hash,
    create_agent_artifact,
    find_duplicate_agent_artifact,
    serialize_agent_artifact,
    validate_agent_artifact_payload,
    validate_agent_artifact_source_refs,
)
from app.services.agent_task_runner import AgentTaskHandlerResult

EVIDENCE_NORMALIZER_TASK_TYPE = "evidence_normalizer_future"
NORMALIZED_EVIDENCE_ARTIFACT_TYPE = "normalized_evidence"
NORMALIZED_EVIDENCE_RESULT_KIND = "normalized_evidence"
NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION = "normalized-evidence-candidates-v0"
EVIDENCE_NORMALIZER_VERSION = "evidence-normalizer-v0"
NORMALIZATION_KIND = "normalized_evidence_candidates"
_ALLOWED_SOURCE_PAYLOAD_KEYS = set(ALLOWED_PAYLOAD_KEYS)


def _skip(reason: str) -> AgentTaskHandlerResult:
    return AgentTaskHandlerResult(
        status="skipped",
        result_json={
            "ok": True,
            "status": "skipped",
            "reason": reason,
            "artifact_type": NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            "normalization_kind": NORMALIZATION_KIND,
        },
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, Number) and not isinstance(value, bool)


def _fallback_candidate_id(item: Any) -> str:
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class EvidenceNormalizerAgentTaskHandler:
    """Normalizes PR41 evidence_candidates artifacts into PR39 normalized_evidence artifacts."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        existing = self._existing_result(task)
        if existing is not None:
            return AgentTaskHandlerResult(status="success", result_json=existing)

        if not task.listing_external_id:
            return _skip("missing_listing_context")
        listing = self.db.scalar(select(Listing).where(Listing.external_id == task.listing_external_id))
        if listing is None:
            return _skip("missing_listing_row")

        source = self._find_source_artifact(task)
        if source is None:
            return _skip("missing_source_artifact")
        if source == "ambiguous":
            return _skip("ambiguous_source_artifact")

        valid, reason = self._validate_source_artifact(source)
        if not valid:
            return _skip(reason)

        analysis = self._load_analysis(task)
        payload = self._build_payload(task, source, analysis)
        source_refs = self._source_refs(task, source)
        validate_agent_artifact_payload(
            payload,
            artifact_type=NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            schema_version=NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION,
        )
        validate_agent_artifact_source_refs(source_refs)
        input_hash = compute_agent_artifact_input_hash(
            {
                "normalizer_version": EVIDENCE_NORMALIZER_VERSION,
                "task_id": task.id,
                "source_artifact_id": source.id,
                "source_content_hash": source.content_hash,
                "listing_external_id": task.listing_external_id,
                "listing_analysis_id": task.listing_analysis_id,
                "search_job_id": task.search_job_id,
                "context_key": task.context_key,
            }
        )
        content_hash = compute_agent_artifact_content_hash(
            artifact_type=NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            schema_version=NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            payload_json=payload,
        )
        duplicate = find_duplicate_agent_artifact(
            self.db,
            artifact_type=NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            content_hash=content_hash,
            listing_external_id=task.listing_external_id,
            context_key=task.context_key,
            source_task_id=task.id,
        )
        artifact = duplicate or create_agent_artifact(
            self.db,
            artifact_type=NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            schema_version=NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            input_hash=input_hash,
            content_hash=content_hash,
            payload_json=payload,
            source_refs_json=source_refs,
            redaction_status="not_required",
            listing_external_id=task.listing_external_id,
            listing_analysis_id=task.listing_analysis_id,
            search_job_id=task.search_job_id,
            context_key=task.context_key,
            source_task_id=task.id,
            orchestration_run_id=task.orchestration_run_id,
        )
        serialize_agent_artifact(artifact, include_payload=False)
        return AgentTaskHandlerResult(status="success", result_json=self._success_result(artifact.id, payload, source.id))

    def _existing_result(self, task: AgentTask) -> dict[str, Any] | None:
        result = task.result_json if isinstance(task.result_json, dict) else {}
        artifact_id = result.get("artifact_id")
        if not artifact_id or result.get("artifact_type") != NORMALIZED_EVIDENCE_ARTIFACT_TYPE:
            return None
        artifact = self.db.get(AgentArtifact, artifact_id)
        if artifact is None or artifact.artifact_type != NORMALIZED_EVIDENCE_ARTIFACT_TYPE:
            return None
        return self._success_result(
            artifact.id,
            artifact.payload_json,
            int(result.get("source_artifact_id") or artifact.payload_json.get("metadata", {}).get("source_artifact_id") or 0),
        )

    def _load_analysis(self, task: AgentTask) -> ListingAnalysis | None:
        if task.listing_analysis_id is None:
            return None
        analysis = self.db.get(ListingAnalysis, task.listing_analysis_id)
        if analysis is None or analysis.listing_external_id != task.listing_external_id:
            return None
        return analysis

    def _find_source_artifact(self, task: AgentTask) -> AgentArtifact | str | None:
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        explicit_id = payload.get("source_artifact_id")
        if explicit_id is not None:
            artifact = self.db.get(AgentArtifact, explicit_id)
            return artifact if artifact is not None else None
        for source_task_id in (task.depends_on_task_id, task.parent_task_id):
            if source_task_id is None:
                continue
            artifacts = self._candidate_artifacts(source_task_id=source_task_id)
            if len(artifacts) == 1:
                return artifacts[0]
            if len(artifacts) > 1:
                return "ambiguous"
        if task.orchestration_run_id:
            artifacts = self._candidate_artifacts(
                listing_external_id=task.listing_external_id,
                context_key=task.context_key,
                orchestration_run_id=task.orchestration_run_id,
            )
            if len(artifacts) == 1:
                return artifacts[0]
            if len(artifacts) > 1:
                return "ambiguous"
        artifacts = self._candidate_artifacts(listing_external_id=task.listing_external_id, context_key=task.context_key)
        if len(artifacts) == 1:
            return artifacts[0]
        if len(artifacts) > 1:
            return "ambiguous"
        return None

    def _candidate_artifacts(self, **filters: Any) -> list[AgentArtifact]:
        stmt = select(AgentArtifact).where(AgentArtifact.artifact_type == EVIDENCE_CANDIDATES_ARTIFACT_TYPE)
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(AgentArtifact, key) == value)
        return list(self.db.scalars(stmt.order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc())))

    def _validate_source_artifact(self, artifact: AgentArtifact) -> tuple[bool, str]:
        if artifact.artifact_type != EVIDENCE_CANDIDATES_ARTIFACT_TYPE:
            return False, "wrong_source_artifact_type"
        if artifact.schema_version != EVIDENCE_CANDIDATES_SCHEMA_VERSION:
            return False, "wrong_source_schema_version"
        payload = artifact.payload_json
        if not isinstance(payload, dict):
            return False, "invalid_source_payload"
        if set(payload) - _ALLOWED_SOURCE_PAYLOAD_KEYS:
            return False, "invalid_source_payload_envelope"
        if payload.get("schema_version") != EVIDENCE_CANDIDATES_SCHEMA_VERSION:
            return False, "wrong_source_schema_version"
        if payload.get("artifact_type") != EVIDENCE_CANDIDATES_ARTIFACT_TYPE:
            return False, "wrong_source_artifact_type"
        if payload.get("result_kind") != EVIDENCE_CANDIDATES_ARTIFACT_TYPE:
            return False, "wrong_source_result_kind"
        if not isinstance(payload.get("items"), list):
            return False, "source_items_not_list"
        try:
            validate_agent_artifact_payload(payload, artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE, schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION)
        except Exception:
            return False, "invalid_source_payload"
        return True, "ok"

    def _build_payload(self, task: AgentTask, source: AgentArtifact, analysis: ListingAnalysis | None) -> dict[str, Any]:
        source_items = source.payload_json.get("items") or []
        items = [self._normalize_item(source, item) for item in source_items]
        limitations = ["internal_normalization_only", "not_market_evidence_item", "no_external_http", "no_llm", "no_rag_write"]
        if not source_items:
            limitations.append("no_source_candidates")
        missing_data = sorted({field for item in items for field in item.get("missing_fields", [])})
        return {
            "schema_version": NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            "artifact_type": NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            "result_kind": NORMALIZED_EVIDENCE_RESULT_KIND,
            "summary": "Normalized internal evidence candidates for listing.",
            "items": items,
            "limitations": limitations,
            "confidence": 0.0 if not items else 0.5,
            "notes": [],
            "metadata": {
                "normalizer_version": EVIDENCE_NORMALIZER_VERSION,
                "normalization_kind": NORMALIZATION_KIND,
                "listing_external_id": task.listing_external_id,
                "listing_analysis_id": analysis.id if analysis is not None else task.listing_analysis_id,
                "search_job_id": task.search_job_id,
                "context_key": task.context_key,
                "source_artifact_id": source.id,
                "source_artifact_type": source.artifact_type,
                "source_artifact_schema_version": source.schema_version,
                "source_result_kind": source.payload_json.get("result_kind"),
                "source_candidate_count": len(source_items),
                "normalized_count": len(items),
                "candidate_count": len(items),
                "missing_data": missing_data,
            },
        }

    def _normalize_item(self, source: AgentArtifact, item: Any) -> dict[str, Any]:
        candidate = item if isinstance(item, dict) else {}
        source_candidate_id = str(candidate.get("candidate_id") or _fallback_candidate_id(item))
        observed = candidate.get("observed_value") if isinstance(candidate.get("observed_value"), dict) else {}
        normalized_values: dict[str, Any] = {}
        missing_fields: list[str] = []
        if _is_number(observed.get("price")):
            normalized_values["price_rub"] = observed["price"]
        else:
            missing_fields.append("price")
        if _is_number(observed.get("area_m2")):
            normalized_values["area_m2"] = observed["area_m2"]
        else:
            missing_fields.append("area_m2")
        if _is_number(observed.get("price_per_m2")):
            normalized_values["price_per_m2_rub"] = observed["price_per_m2"]
        elif _is_number(observed.get("price")) and _is_number(observed.get("area_m2")) and observed["price"] > 0 and observed["area_m2"] > 0:
            normalized_values["price_per_m2_rub"] = round(observed["price"] / observed["area_m2"], 2)
        else:
            missing_fields.append("price_per_m2")
        quality_flags: list[str] = []
        status = "normalized"
        if not normalized_values:
            status = "insufficient"
            quality_flags.append("no_numeric_observed_values")
        elif missing_fields:
            status = "partial"
        return {
            "normalized_candidate_id": f"normalized:{source.id}:{source_candidate_id}",
            "source_candidate_id": source_candidate_id,
            "source_artifact_id": source.id,
            "evidence_kind": str(candidate.get("evidence_kind") or "unknown"),
            "source": "internal",
            "normalization_status": status,
            "normalized_values": normalized_values,
            "quality_flags": quality_flags,
            "missing_fields": missing_fields,
            "confidence": float(candidate.get("confidence") or 0.0) if _is_number(candidate.get("confidence")) else 0.0,
            "limitations": list(candidate.get("limitations") or []),
            "source_refs": self._item_source_refs(source),
        }

    def _source_refs(self, task: AgentTask, source: AgentArtifact) -> list[dict[str, Any]]:
        refs = [
            {
                "source_kind": "agent_artifact",
                "source_ref_id": str(source.id),
                "note": "source_evidence_candidates_artifact",
                "agent_task_id": task.id,
                "listing_external_id": task.listing_external_id,
            }
        ]
        if task.listing_analysis_id is not None:
            refs.append({"listing_analysis_id": task.listing_analysis_id})
        if task.search_job_id is not None:
            refs.append({"search_job_id": task.search_job_id})
        validate_agent_artifact_source_refs(refs)
        return refs

    def _item_source_refs(self, source: AgentArtifact) -> list[dict[str, Any]]:
        ref = {
            "source_kind": "agent_artifact",
            "source_ref_id": str(source.id),
            "note": "source_evidence_candidates_artifact",
            "listing_external_id": source.listing_external_id,
        }
        if source.source_task_id is not None:
            ref["agent_task_id"] = source.source_task_id
        validate_agent_artifact_source_refs([ref])
        return [ref]

    def _success_result(self, artifact_id: int, payload: dict[str, Any], source_artifact_id: int) -> dict[str, Any]:
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        return {
            "ok": True,
            "status": "success",
            "artifact_id": artifact_id,
            "artifact_type": NORMALIZED_EVIDENCE_ARTIFACT_TYPE,
            "schema_version": NORMALIZED_EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            "result_kind": NORMALIZED_EVIDENCE_RESULT_KIND,
            "normalization_kind": NORMALIZATION_KIND,
            "source_artifact_id": source_artifact_id,
            "normalized_count": int(metadata.get("normalized_count") or 0),
            "source_candidate_count": int(metadata.get("source_candidate_count") or 0),
            "limitations": list(payload.get("limitations") or []),
        }
