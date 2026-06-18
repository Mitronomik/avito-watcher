from __future__ import annotations

from functools import lru_cache

from app.agents.contracts import (
    AGENT_CONTRACT_VERSION,
    AgentDedupePolicy,
    AgentRedactionPolicy,
    AgentRetryPolicy,
    AgentSafetyCategory,
    AgentSideEffect,
    AgentTaskClass,
    AgentTaskContract,
    AgentTimeoutPolicy,
    AgentWorkflowContract,
    safe_output_schema,
)
from app.core.config import settings
from app.services.agent_task_runner import get_registered_agent_task_handler_names


def _implemented(task_type: str, handlers: set[str]) -> tuple[bool, str | None]:
    return (task_type in handlers, task_type if task_type in handlers else None)


def _contract(
    task_type: str,
    task_class: AgentTaskClass,
    safety_category: AgentSafetyCategory,
    result_kind: str,
    handlers: set[str],
    *,
    description: str,
    side_effects: tuple[AgentSideEffect, ...] = (AgentSideEffect.WRITE_AGENT_TASK_RESULT,),
    required_capabilities: tuple[str, ...] = ("agent_task.read", "agent_task.view_redacted_result"),
    limitations: tuple[str, ...] = (),
    legacy_compatibility: bool = False,
    legacy_semantic_label: str | None = None,
    handler_required: bool = True,
) -> AgentTaskContract:
    implemented, handler_name = _implemented(task_type, handlers)
    if not implemented:
        handler_required = False
        limitations = tuple(dict.fromkeys((*limitations, "handler_not_present_in_current_codebase")))
    return AgentTaskContract(
        task_type=task_type,
        task_class=task_class,
        schema_version=f"{task_type}-contract-v1",
        agent_contract_version=AGENT_CONTRACT_VERSION,
        implemented=implemented,
        handler_required=handler_required,
        handler_name=handler_name,
        safety_category=safety_category,
        blocking=False,
        declared_side_effects=side_effects,
        required_capabilities=required_capabilities,
        timeout_policy=AgentTimeoutPolicy(settings.agent_orchestration_default_timeout_sec),
        retry_policy=AgentRetryPolicy(max_retries=0, retryable=False),
        dedupe_policy=AgentDedupePolicy(),
        redaction_policy=AgentRedactionPolicy(),
        output_schema=safe_output_schema(result_kind, legacy=legacy_compatibility),
        legacy_compatibility=legacy_compatibility,
        legacy_semantic_label=legacy_semantic_label,
        description=description,
        limitations=limitations,
    )


@lru_cache(maxsize=1)
def get_agent_task_registry() -> dict[str, AgentTaskContract]:
    handlers = get_registered_agent_task_handler_names()
    contracts = [
        _contract(
            "review_copilot",
            AgentTaskClass.DECISION_WORDING,
            AgentSafetyCategory.READ_ONLY_EXPLANATION,
            "explanation",
            handlers,
            description="Legacy review copilot explanation support for manual review.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.EXTERNAL_LLM_CALL, AgentSideEffect.RAG_READ),
            required_capabilities=("agent_task.read", "agent_task.run_manual", "agent_task.view_redacted_result"),
            legacy_compatibility=True,
        ),
        _contract(
            "listing_detail_extraction",
            AgentTaskClass.DATA_COLLECTION,
            AgentSafetyCategory.READ_ONLY_EXTRACTION,
            "extraction",
            handlers,
            description="Extracts structured public listing details from existing listing detail snapshots.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.EXTERNAL_LLM_CALL),
            required_capabilities=("agent_task.read", "agent_task.run_extraction", "agent_task.view_redacted_result"),
            legacy_compatibility=True,
        ),
        _contract(
            "market_research",
            AgentTaskClass.DATA_COLLECTION,
            AgentSafetyCategory.READ_ONLY_RESEARCH,
            "research_candidates",
            handlers,
            description="Legacy market research task that writes only AgentTask result metadata.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.EXTERNAL_LLM_CALL, AgentSideEffect.EXTERNAL_HTTP_CALL),
            required_capabilities=("agent_task.read", "agent_task.run_research", "agent_task.view_redacted_result"),
            legacy_compatibility=True,
        ),
        _contract(
            "weekly_strategy_agent",
            AgentTaskClass.PORTFOLIO_MEMORY,
            AgentSafetyCategory.GOVERNANCE_PROPOSAL,
            "governance_proposal",
            handlers,
            description="Legacy weekly strategy summary task for portfolio memory style review.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.EXTERNAL_LLM_CALL),
            required_capabilities=("agent_task.read", "agent_task.run_governance", "agent_task.view_redacted_result"),
            legacy_compatibility=True,
        ),
        _contract(
            "data_quality_agent",
            AgentTaskClass.DATA_GAP_ANALYSIS,
            AgentSafetyCategory.SAFETY_REVIEW,
            "data_quality_review",
            handlers,
            description="Legacy data-quality review task for suspicious parser or listing data.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.EXTERNAL_LLM_CALL, AgentSideEffect.RAG_READ),
            required_capabilities=("agent_task.read", "agent_task.run_governance", "agent_task.view_redacted_result"),
            limitations=("legacy_data_quality_review_not_pr43_data_gap_agent", "must_not_produce_data_gap_report_artifact"),
            legacy_compatibility=True,
            legacy_semantic_label="data_quality_review",
        ),
        _contract(
            "evidence_collector_future",
            AgentTaskClass.DATA_COLLECTION,
            AgentSafetyCategory.READ_ONLY_RESEARCH,
            "research_candidates",
            handlers,
            description="Future PR41 evidence collection contract placeholder.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.WRITE_AGENT_ARTIFACT_FUTURE, AgentSideEffect.EXTERNAL_HTTP_CALL),
            limitations=("future_pr41_placeholder",),
        ),
        _contract(
            "evidence_normalizer_future",
            AgentTaskClass.DATA_NORMALIZATION,
            AgentSafetyCategory.READ_ONLY_NORMALIZATION,
            "normalization",
            handlers,
            description="Future PR42 evidence normalization contract placeholder.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.WRITE_AGENT_ARTIFACT_FUTURE),
            limitations=("future_pr42_placeholder",),
        ),
        _contract(
            "data_gap_agent_future",
            AgentTaskClass.DATA_GAP_ANALYSIS,
            AgentSafetyCategory.SAFETY_REVIEW,
            "safety_review",
            handlers,
            description="Future PR43 data gap contract placeholder; not the legacy data_quality_agent.",
            side_effects=(AgentSideEffect.WRITE_AGENT_TASK_RESULT, AgentSideEffect.WRITE_AGENT_ARTIFACT_FUTURE),
            limitations=("future_pr43_placeholder",),
        ),
        _contract(
            "owner_call_prep_future",
            AgentTaskClass.CALL_PREPARATION,
            AgentSafetyCategory.DRAFT_GENERATION,
            "draft",
            handlers,
            description="Future PR44 owner call preparation contract placeholder.",
            limitations=("future_pr44_placeholder",),
        ),
        _contract(
            "decision_card_wording_future",
            AgentTaskClass.DECISION_WORDING,
            AgentSafetyCategory.DRAFT_GENERATION,
            "draft",
            handlers,
            description="Future PR45 decision card wording contract placeholder.",
            limitations=("future_pr45_placeholder",),
        ),
        _contract(
            "claim_guard_future",
            AgentTaskClass.CLAIM_GUARD,
            AgentSafetyCategory.SAFETY_REVIEW,
            "safety_review",
            handlers,
            description="Future PR46 claim guard contract placeholder.",
            limitations=("future_pr46_placeholder",),
        ),
    ]
    return {contract.task_type: contract for contract in sorted(contracts, key=lambda item: item.task_type)}


@lru_cache(maxsize=1)
def get_agent_workflow_registry() -> dict[str, AgentWorkflowContract]:
    workflows = [
        AgentWorkflowContract(
            workflow_id="listing_evidence_pipeline",
            workflow_label="Listing evidence pipeline",
            description="Future metadata-only pipeline for listing evidence collection and normalization.",
            task_classes=(AgentTaskClass.DATA_COLLECTION, AgentTaskClass.DATA_NORMALIZATION),
            implemented=False,
            max_chain_depth=settings.agent_orchestration_max_chain_depth,
            blocking_policy="non_blocking_metadata_only",
            required_capabilities=("agent_task.read", "agent_task.run_research"),
            limitations=("metadata_only", "no_dependency_graph_until_pr38", "no_runtime_until_pr40"),
        ),
        AgentWorkflowContract(
            workflow_id="listing_decision_support_pipeline",
            workflow_label="Listing decision-support pipeline",
            description="Future metadata-only pipeline for data gaps, call prep, and wording drafts.",
            task_classes=(AgentTaskClass.DATA_GAP_ANALYSIS, AgentTaskClass.CALL_PREPARATION, AgentTaskClass.DECISION_WORDING),
            implemented=False,
            max_chain_depth=settings.agent_orchestration_max_chain_depth,
            blocking_policy="non_blocking_metadata_only",
            required_capabilities=("agent_task.read", "agent_task.run_governance"),
            limitations=("metadata_only", "no_dependency_graph_until_pr38", "no_runtime_until_pr40"),
        ),
        AgentWorkflowContract(
            workflow_id="report_safety_pipeline",
            workflow_label="Report safety pipeline",
            description="Future metadata-only pipeline for report wording and claim safety review.",
            task_classes=(AgentTaskClass.REPORT_COMPOSITION, AgentTaskClass.CLAIM_GUARD),
            implemented=False,
            max_chain_depth=settings.agent_orchestration_max_chain_depth,
            blocking_policy="non_blocking_metadata_only",
            required_capabilities=("agent_task.read", "agent_task.run_governance"),
            limitations=("metadata_only", "no_agent_artifacts_until_pr39", "no_runtime_until_pr40"),
        ),
    ]
    return {workflow.workflow_id: workflow for workflow in workflows}
