from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentWorkflowBlueprintNode:
    node_id: str
    task_type: str
    depends_on_node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentWorkflowBlueprint:
    workflow_id: str
    nodes: tuple[AgentWorkflowBlueprintNode, ...]


def get_agent_workflow_blueprints() -> dict[str, AgentWorkflowBlueprint]:
    blueprints = (
        AgentWorkflowBlueprint(
            workflow_id="listing_evidence_pipeline",
            nodes=(
                AgentWorkflowBlueprintNode("evidence_collector", "evidence_collector_future"),
                AgentWorkflowBlueprintNode("evidence_normalizer", "evidence_normalizer_future", ("evidence_collector",)),
            ),
        ),
        AgentWorkflowBlueprint(
            workflow_id="listing_decision_support_pipeline",
            nodes=(
                AgentWorkflowBlueprintNode("data_gap", "data_gap_agent_future"),
                AgentWorkflowBlueprintNode("owner_call_prep", "owner_call_prep_future", ("data_gap",)),
                AgentWorkflowBlueprintNode("decision_card_wording", "decision_card_wording_future", ("owner_call_prep",)),
            ),
        ),
        AgentWorkflowBlueprint(
            workflow_id="report_safety_pipeline",
            nodes=(AgentWorkflowBlueprintNode("claim_guard", "claim_guard_future"),),
        ),
    )
    return {blueprint.workflow_id: blueprint for blueprint in blueprints}
