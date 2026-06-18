from __future__ import annotations

from dataclasses import asdict

from app.agents.contracts import AgentSafetyCategory, AgentSideEffect, AgentTaskClass
from app.agents.registry import get_agent_task_registry, get_agent_workflow_registry
from app.core.config import settings
from app.services.agent_task_runner import get_registered_agent_task_handler_names

FORBIDDEN_TEXT = (
    "execution_endpoint",
    "http_method",
    "absolute_url",
    "auth_param",
    "token",
    "raw result_json",
    "score_mutation",
    "verdict_mutation",
    "filter_mutation",
    "workflow_mutation",
    "action_execution",
)


def test_agent_task_registry_loads_and_is_deterministic():
    first = get_agent_task_registry()
    second = get_agent_task_registry()
    assert first == second
    assert first
    assert list(first) == sorted(first)

    for task_type, contract in first.items():
        assert contract.task_type == task_type
        assert contract.task_class in AgentTaskClass
        assert contract.schema_version
        assert contract.agent_contract_version == "agent-contract-v1"
        assert contract.safety_category in AgentSafetyCategory
        assert contract.timeout_policy.timeout_sec > 0
        assert contract.retry_policy.max_retries >= 0
        assert contract.dedupe_policy.dedupe_required is True
        assert contract.redaction_policy.canonical_helper == "app.api.admin_v1.redaction.redact_api_response"
        assert contract.required_capabilities
        assert contract.declared_side_effects
        assert all(side_effect in AgentSideEffect for side_effect in contract.declared_side_effects)
        assert contract.output_schema["metadata_only"] is True
        assert contract.output_schema["recommended_envelope"]["required"]
        serialized = str(asdict(contract)).lower()
        for forbidden in FORBIDDEN_TEXT:
            assert forbidden not in serialized


def test_implemented_flags_match_actual_registered_handlers():
    handlers = get_registered_agent_task_handler_names()
    registry = get_agent_task_registry()
    implemented = {task_type for task_type, contract in registry.items() if contract.implemented}
    assert implemented == handlers
    for task_type, contract in registry.items():
        if task_type in handlers:
            assert contract.handler_name == task_type
            assert contract.handler_required is True
        else:
            assert contract.implemented is False
            assert contract.handler_name is None
            assert contract.handler_required is False
            assert "handler_not_present_in_current_codebase" in contract.limitations


def test_data_quality_agent_legacy_mapping_not_pr43_data_gap_agent():
    contract = get_agent_task_registry()["data_quality_agent"]
    assert contract.legacy_compatibility is True
    assert contract.legacy_semantic_label == "data_quality_review"
    assert contract.task_class == AgentTaskClass.DATA_GAP_ANALYSIS
    assert "legacy_data_quality_review_not_pr43_data_gap_agent" in contract.limitations
    assert "must_not_produce_data_gap_report_artifact" in contract.limitations
    assert "data_gap_report" not in str(contract.output_schema)


def test_workflow_registry_is_skeleton_only_and_safe_flags_default_off():
    workflows = get_agent_workflow_registry()
    assert set(workflows) == {
        "listing_evidence_pipeline",
        "listing_decision_support_pipeline",
        "report_safety_pipeline",
    }
    for workflow in workflows.values():
        assert workflow.implemented is False
        assert workflow.max_chain_depth == settings.agent_orchestration_max_chain_depth
        assert workflow.blocking_policy == "non_blocking_metadata_only"
        serialized = str(asdict(workflow)).lower()
        assert "depends_on_task_id" not in serialized
        assert "parent_task_id" not in serialized
        assert "orchestration_run_id" not in serialized

    assert settings.agent_orchestration_enabled is False
    assert settings.agent_orchestration_allow_monitor_trigger is False
    assert settings.agent_orchestration_max_chain_depth == 4
    assert settings.agent_orchestration_max_tasks_per_listing == 10
    assert settings.agent_orchestration_default_timeout_sec == 120


def test_declared_side_effects_are_metadata_only_not_permission_grants():
    registry = get_agent_task_registry()
    for contract in registry.values():
        assert not hasattr(contract, "allowed_side_effects")
        assert "permission" not in contract.redaction_policy.source
        assert "authorization" not in contract.dedupe_policy.source
