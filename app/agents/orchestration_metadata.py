from __future__ import annotations

from dataclasses import dataclass

from app.agents.registry import get_agent_workflow_registry
from app.core.config import settings
from app.models.agent_task import (
    AGENT_TASK_DEPENDENCY_STATUSES,
    AGENT_TASK_ORCHESTRATION_STATUSES,
    AgentTask,
)


@dataclass(frozen=True)
class AgentTaskOrchestrationMetadataValidation:
    valid: bool
    reason: str = "ok"


def validate_agent_task_orchestration_metadata(
    *,
    task: AgentTask | None = None,
    task_id: int | None = None,
    workflow_id: str | None = None,
    parent_task_id: int | None = None,
    depends_on_task_id: int | None = None,
    chain_depth: int | None = None,
    dependency_status: str | None = None,
    orchestration_status: str | None = None,
    max_chain_depth: int | None = None,
) -> AgentTaskOrchestrationMetadataValidation:
    """Validate PR38 metadata without changing task execution behavior."""
    if task is not None:
        task_id = task.id if task_id is None else task_id
        workflow_id = task.workflow_id if workflow_id is None else workflow_id
        parent_task_id = task.parent_task_id if parent_task_id is None else parent_task_id
        depends_on_task_id = task.depends_on_task_id if depends_on_task_id is None else depends_on_task_id
        chain_depth = task.chain_depth if chain_depth is None else chain_depth
        dependency_status = task.dependency_status if dependency_status is None else dependency_status
        orchestration_status = task.orchestration_status if orchestration_status is None else orchestration_status

    if dependency_status is not None and dependency_status not in AGENT_TASK_DEPENDENCY_STATUSES:
        return AgentTaskOrchestrationMetadataValidation(False, "invalid_dependency_status")
    if orchestration_status is not None and orchestration_status not in AGENT_TASK_ORCHESTRATION_STATUSES:
        return AgentTaskOrchestrationMetadataValidation(False, "invalid_orchestration_status")
    if chain_depth is not None:
        if chain_depth < 0:
            return AgentTaskOrchestrationMetadataValidation(False, "invalid_chain_depth")
        effective_max = settings.agent_orchestration_max_chain_depth if max_chain_depth is None else max_chain_depth
        if chain_depth > effective_max:
            return AgentTaskOrchestrationMetadataValidation(False, "chain_depth_exceeds_max")
    if workflow_id is not None and workflow_id not in get_agent_workflow_registry():
        return AgentTaskOrchestrationMetadataValidation(False, "unknown_workflow_id")
    if task_id is not None and parent_task_id == task_id:
        return AgentTaskOrchestrationMetadataValidation(False, "parent_task_self_reference")
    if task_id is not None and depends_on_task_id == task_id:
        return AgentTaskOrchestrationMetadataValidation(False, "dependency_task_self_reference")
    return AgentTaskOrchestrationMetadataValidation(True)
