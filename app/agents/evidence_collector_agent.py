from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_task import AgentTask
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.services.agent_artifact_service import (
    compute_agent_artifact_content_hash,
    compute_agent_artifact_input_hash,
    create_agent_artifact,
    find_duplicate_agent_artifact,
    serialize_agent_artifact,
    validate_agent_artifact_payload,
    validate_agent_artifact_source_refs,
)
from app.services.agent_task_runner import AgentTaskHandlerResult

EVIDENCE_COLLECTOR_TASK_TYPE = "evidence_collector_future"
EVIDENCE_CANDIDATES_ARTIFACT_TYPE = "evidence_candidates"
EVIDENCE_CANDIDATES_SCHEMA_VERSION = "evidence-candidates-v0"
COLLECTOR_VERSION = "evidence-collector-v0"
_ALLOWED_PAYLOAD_KEYS = {
    "schema_version",
    "artifact_type",
    "result_kind",
    "summary",
    "items",
    "limitations",
    "confidence",
    "notes",
    "metadata",
}
_SENSITIVE_FACT_KEY_PARTS = (
    "token",
    "api_key",
    "apikey",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
    "cookie",
    "header",
    "raw",
    "payload",
    "provider",
    "debug",
    "html",
)


def _short_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text[:limit]


def _safe_refs(task: AgentTask) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [{"agent_task_id": task.id, "listing_external_id": task.listing_external_id}]
    if task.listing_analysis_id is not None:
        refs.append({"listing_analysis_id": task.listing_analysis_id})
    if task.search_job_id is not None:
        refs.append({"search_job_id": task.search_job_id})
    validate_agent_artifact_source_refs(refs)
    return refs


def _candidate_fallback_id(candidate: dict[str, Any]) -> str:
    raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _safe_analysis_fact_keys(facts_json: dict) -> list[str]:
    if not isinstance(facts_json, dict):
        return []
    safe_keys: list[str] = []
    for key in facts_json:
        text = str(key)
        lowered = text.lower()
        if any(part in lowered for part in _SENSITIVE_FACT_KEY_PARTS):
            continue
        safe_keys.append(text)
    return sorted(safe_keys)[:5]


class EvidenceCollectorAgentTaskHandler:
    """Collects internal-only evidence candidates and writes one PR39 artifact per task.

    Success means an evidence_candidates artifact exists or an existing duplicate was reused.
    Skipped means minimum listing context was missing and no artifact was created.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        existing = self._existing_result(task)
        if existing is not None:
            return AgentTaskHandlerResult(status="success", result_json=existing)

        if not task.listing_external_id:
            return self._skip("missing_listing_context")

        listing = self.db.scalar(select(Listing).where(Listing.external_id == task.listing_external_id))
        if listing is None:
            return self._skip("missing_listing_row")

        analysis = self._load_analysis(task)
        payload = self._build_payload(task, listing, analysis)
        source_refs = _safe_refs(task)
        validate_agent_artifact_payload(
            payload,
            artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
        )
        input_hash = compute_agent_artifact_input_hash(
            {
                "collector_version": COLLECTOR_VERSION,
                "task_id": task.id,
                "listing_external_id": task.listing_external_id,
                "listing_analysis_id": task.listing_analysis_id,
                "search_job_id": task.search_job_id,
                "context_key": task.context_key,
            }
        )
        content_hash = compute_agent_artifact_content_hash(
            artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            payload_json=payload,
        )
        duplicate = find_duplicate_agent_artifact(
            self.db,
            artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            content_hash=content_hash,
            listing_external_id=task.listing_external_id,
            context_key=task.context_key,
            source_task_id=task.id,
        )
        artifact = duplicate or create_agent_artifact(
            self.db,
            artifact_type=EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            schema_version=EVIDENCE_CANDIDATES_SCHEMA_VERSION,
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
        return AgentTaskHandlerResult(status="success", result_json=self._success_result(artifact.id, payload, source_refs))

    def _existing_result(self, task: AgentTask) -> dict[str, Any] | None:
        result = task.result_json if isinstance(task.result_json, dict) else {}
        artifact_id = result.get("artifact_id")
        if artifact_id and result.get("artifact_type") == EVIDENCE_CANDIDATES_ARTIFACT_TYPE:
            return {
                "ok": True,
                "status": "success",
                "artifact_id": artifact_id,
                "artifact_type": EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
                "schema_version": EVIDENCE_CANDIDATES_SCHEMA_VERSION,
                "candidate_count": int(result.get("candidate_count") or 0),
                "source_refs_count": int(result.get("source_refs_count") or 0),
                "limitations": list(result.get("limitations") or []),
            }
        return None

    def _load_analysis(self, task: AgentTask) -> ListingAnalysis | None:
        if task.listing_analysis_id is None:
            return None
        analysis = self.db.get(ListingAnalysis, task.listing_analysis_id)
        if analysis is None or analysis.listing_external_id != task.listing_external_id:
            return None
        return analysis

    def _build_payload(self, task: AgentTask, listing: Listing, analysis: ListingAnalysis | None) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        missing: list[str] = []
        limitations = ["internal_evidence_candidates_only", "not_normalized_evidence", "not_market_evidence_item", "no_external_http", "no_llm", "no_rag_write"]

        if not listing.title:
            missing.append("missing_listing_title")
        if listing.price is None:
            missing.append("missing_listing_price")
        if listing.area_m2 is None:
            missing.append("missing_listing_area_m2")
        if not listing.address:
            missing.append("missing_listing_location_hint")

        if listing.title or listing.price is not None or listing.area_m2 is not None or listing.address:
            observed = {"price": listing.price, "area_m2": listing.area_m2, "price_per_m2": None}
            if listing.price is not None and listing.area_m2:
                observed["price_per_m2"] = round(float(listing.price) / float(listing.area_m2), 2)
            items.append({
                "candidate_id": f"listing_snapshot:{task.listing_external_id}",
                "evidence_kind": "listing_snapshot",
                "source": "internal",
                "title": _short_text(listing.title or "Internal listing snapshot", 200),
                "summary": _short_text(f"Internal listing snapshot for {task.listing_external_id}.", 500),
                "observed_value": observed,
                "location_hint": _short_text(listing.address, 200),
                "confidence": 0.5,
                "limitations": ["candidate_only", "not_verified_external_evidence"],
                "source_refs": [{"agent_task_id": task.id, "listing_external_id": task.listing_external_id}],
            })

        if analysis is not None and analysis.status == "success" and isinstance(analysis.facts_json, dict) and analysis.facts_json:
            safe_keys = _safe_analysis_fact_keys(analysis.facts_json)
            if not safe_keys and items:
                return self._payload(task, items, limitations, missing)
            candidate = {
                "candidate_id": f"listing_analysis:{analysis.id}",
                "evidence_kind": "listing_analysis_summary",
                "source": "internal",
                "title": "Internal listing analysis summary",
                "summary": _short_text("Internal analysis facts available: " + ", ".join(safe_keys), 500),
                "observed_value": {"fact_keys": safe_keys},
                "location_hint": None,
                "confidence": 0.4,
                "limitations": ["candidate_only", "analysis_facts_not_reproduced"],
                "source_refs": [{"listing_analysis_id": analysis.id}],
            }
            candidate["candidate_id"] = candidate["candidate_id"] or _candidate_fallback_id(candidate)
            items.append(candidate)

        return self._payload(task, items, limitations, missing)

    def _payload(self, task: AgentTask, items: list[dict[str, Any]], limitations: list[str], missing: list[str]) -> dict[str, Any]:
        items = items[:5]
        if not items:
            limitations.append("insufficient_internal_listing_evidence")
        payload = {
            "schema_version": EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            "artifact_type": EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            "result_kind": EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            "summary": "Collected internal evidence candidates for listing.",
            "items": items,
            "limitations": limitations,
            "confidence": 0.0 if not items else 0.5,
            "notes": [],
            "metadata": {
                "collector_version": COLLECTOR_VERSION,
                "listing_external_id": task.listing_external_id,
                "listing_analysis_id": task.listing_analysis_id,
                "search_job_id": task.search_job_id,
                "context_key": task.context_key,
                "candidate_count": len(items),
                "missing_data": missing,
            },
        }
        assert set(payload) <= _ALLOWED_PAYLOAD_KEYS
        return payload

    def _success_result(self, artifact_id: int, payload: dict[str, Any], source_refs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "success",
            "artifact_id": artifact_id,
            "artifact_type": EVIDENCE_CANDIDATES_ARTIFACT_TYPE,
            "schema_version": EVIDENCE_CANDIDATES_SCHEMA_VERSION,
            "candidate_count": payload["metadata"]["candidate_count"],
            "source_refs_count": len(source_refs),
            "limitations": list(payload.get("limitations") or []),
        }

    def _skip(self, reason: str) -> AgentTaskHandlerResult:
        return AgentTaskHandlerResult(
            status="skipped",
            result_json={
                "ok": True,
                "status": "skipped",
                "skip_reason": reason,
                "candidate_count": 0,
                "limitations": ["minimum_listing_context_missing"],
            },
        )
