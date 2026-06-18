from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

AGENT_CONTRACT_VERSION = "agent-contract-v1"
AGENT_TASK_REGISTRY_VERSION = "agent-task-registry-v1"
AGENT_WORKFLOW_REGISTRY_VERSION = "agent-workflow-registry-v1"


class AgentTaskClass(StrEnum):
    DATA_COLLECTION = "data_collection"
    DATA_NORMALIZATION = "data_normalization"
    DATA_GAP_ANALYSIS = "data_gap_analysis"
    DECISION_WORDING = "decision_wording"
    CALL_PREPARATION = "call_preparation"
    GEOCODING = "geocoding"
    REPORT_COMPOSITION = "report_composition"
    OFFER_COMPOSITION = "offer_composition"
    CLAIM_GUARD = "claim_guard"
    PORTFOLIO_MEMORY = "portfolio_memory"


class AgentSafetyCategory(StrEnum):
    READ_ONLY_EXPLANATION = "read_only_explanation"
    READ_ONLY_RESEARCH = "read_only_research"
    READ_ONLY_EXTRACTION = "read_only_extraction"
    READ_ONLY_NORMALIZATION = "read_only_normalization"
    DRAFT_GENERATION = "draft_generation"
    SAFETY_REVIEW = "safety_review"
    GOVERNANCE_PROPOSAL = "governance_proposal"


class AgentSideEffect(StrEnum):
    NONE = "none"
    WRITE_AGENT_TASK_RESULT = "write_agent_task_result"
    WRITE_AGENT_ARTIFACT_FUTURE = "write_agent_artifact_future"
    EXTERNAL_LLM_CALL = "external_llm_call"
    EXTERNAL_HTTP_CALL = "external_http_call"
    RAG_READ = "rag_read"
    RAG_WRITE_FUTURE = "rag_write_future"
    ADMIN_DISPLAY_ONLY = "admin_display_only"


@dataclass(frozen=True)
class AgentTimeoutPolicy:
    timeout_sec: int
    source: str = "metadata_only"


@dataclass(frozen=True)
class AgentRetryPolicy:
    max_retries: int = 0
    retryable: bool = False
    source: str = "metadata_only"


@dataclass(frozen=True)
class AgentDedupePolicy:
    dedupe_required: bool = True
    scope: str = "existing_agent_task_dedupe_key"
    source: str = "metadata_only"


@dataclass(frozen=True)
class AgentRedactionPolicy:
    canonical_helper: str = "app.api.admin_v1.redaction.redact_api_response"
    redact_before_admin_display: bool = True
    source: str = "canonical_admin_api_helper"


@dataclass(frozen=True)
class AgentTaskContract:
    task_type: str
    task_class: AgentTaskClass
    schema_version: str
    agent_contract_version: str
    implemented: bool
    handler_required: bool
    handler_name: str | None
    safety_category: AgentSafetyCategory
    blocking: bool
    declared_side_effects: tuple[AgentSideEffect, ...]
    required_permission_refs: tuple[str, ...]
    timeout_policy: AgentTimeoutPolicy
    retry_policy: AgentRetryPolicy
    dedupe_policy: AgentDedupePolicy
    redaction_policy: AgentRedactionPolicy
    output_schema: dict[str, Any]
    description: str
    limitations: tuple[str, ...]
    legacy_compatibility: bool = False
    legacy_semantic_label: str | None = None


@dataclass(frozen=True)
class AgentWorkflowContract:
    workflow_id: str
    workflow_label: str
    description: str
    task_classes: tuple[AgentTaskClass, ...]
    implemented: bool
    max_chain_depth: int
    blocking_policy: str
    required_permission_refs: tuple[str, ...]
    limitations: tuple[str, ...]


ALLOWED_SOURCE_REF_KEYS = (
    "listing_id",
    "listing_external_id",
    "listing_analysis_id",
    "search_job_id",
    "agent_task_id",
    "human_review_id",
    "market_evidence_ids",
    "decision_card_input_hash",
    "risk_attention_input_hash",
    "readiness_checklist_input_hash",
    "price_position_input_hash",
    "knowledge_note_ids",
    "source_task_id_future",
    "artifact_ids_future",
)


def safe_output_schema(result_kind: str, *, legacy: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "metadata_only": True,
        "legacy_compatible": legacy,
        "additionalProperties": True,
        "recommended_envelope": {
            "required": [
                "schema_version",
                "agent_name",
                "agent_contract_version",
                "task_type",
                "input_hash",
                "source_refs",
                "limitations",
                "confidence",
                "result_kind",
                "recommendations_or_proposals_only",
            ],
            "result_kind": result_kind,
            "allowed_source_ref_keys": list(ALLOWED_SOURCE_REF_KEYS),
        },
    }
