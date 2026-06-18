from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.contracts import AgentSideEffect
from app.agents.registry import get_agent_task_registry, get_agent_workflow_registry
from app.agents.workflow_blueprints import AgentWorkflowBlueprint, get_agent_workflow_blueprints
from app.core.config import settings
from app.models.agent_task import AgentTask
from app.repositories.agent_task_repository import AgentTaskRepository
from app.services.agent_artifact_service import list_agent_artifacts, serialize_agent_artifact

ORCHESTRATOR_POLICY_VERSION = "agent-orchestrator-v0"
PR40_UNSAFE_ENQUEUE_SIDE_EFFECTS = {
    AgentSideEffect.EXTERNAL_LLM_CALL,
    AgentSideEffect.EXTERNAL_HTTP_CALL,
    AgentSideEffect.RAG_WRITE_FUTURE,
}


@dataclass(frozen=True)
class AgentOrchestrationPlanNode:
    node_id: str
    task_type: str
    handler_implemented: bool
    workflow_id: str
    chain_depth: int
    blocking: bool
    depends_on_node_ids: tuple[str, ...]
    required_permission_refs: tuple[str, ...]
    declared_side_effects: tuple[str, ...]
    expected_artifact_types: tuple[str, ...]
    can_enqueue: bool
    blocked_reason: str | None = None


@dataclass(frozen=True)
class AgentOrchestrationPlanEdge:
    from_node_id: str
    to_node_id: str


@dataclass(frozen=True)
class AgentOrchestrationValidationResult:
    valid: bool
    reason: str = "ok"
    blocked_nodes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentOrchestrationPlan:
    workflow_id: str
    valid: bool
    reason: str
    nodes: tuple[AgentOrchestrationPlanNode, ...] = ()
    edges: tuple[AgentOrchestrationPlanEdge, ...] = ()
    warnings: tuple[str, ...] = ()
    planning_supported: bool = False
    enqueue_supported: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AgentOrchestrationEnqueueResult:
    ok: bool
    dry_run: bool
    workflow_id: str
    orchestration_run_id: str | None = None
    enqueued_task_ids: tuple[int, ...] = ()
    existing_task_ids: tuple[int, ...] = ()
    blocked_reason: str | None = None
    plan: dict | None = None


@dataclass(frozen=True)
class AgentOrchestrationRunSummary:
    orchestration_run_id: str
    task_count: int
    task_status_counts: dict[str, int]
    artifact_count: int
    artifacts: tuple[dict, ...]


class AgentOrchestratorService:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def build_plan(self, *, workflow_id: str) -> AgentOrchestrationPlan:
        workflow_registry = get_agent_workflow_registry()
        task_registry = get_agent_task_registry()
        blueprints = get_agent_workflow_blueprints()
        validation = self.validate_blueprint(workflow_id=workflow_id)
        if not validation.valid:
            return AgentOrchestrationPlan(workflow_id=workflow_id, valid=False, reason=validation.reason, warnings=validation.warnings)
        blueprint = blueprints[workflow_id]
        depths = self._node_depths(blueprint)
        nodes = []
        for node in blueprint.nodes:
            contract = task_registry[node.task_type]
            blocked_reason = None
            if not contract.implemented or not contract.handler_name:
                blocked_reason = "handler_unimplemented"
            elif set(contract.declared_side_effects) & PR40_UNSAFE_ENQUEUE_SIDE_EFFECTS:
                blocked_reason = "unsafe_side_effects_for_pr40_enqueue"
            elif node.depends_on_node_ids:
                blocked_reason = "non_root_node"
            nodes.append(AgentOrchestrationPlanNode(
                node_id=node.node_id,
                task_type=node.task_type,
                handler_implemented=contract.implemented and contract.handler_name is not None,
                workflow_id=workflow_id,
                chain_depth=depths[node.node_id],
                blocking=contract.blocking,
                depends_on_node_ids=node.depends_on_node_ids,
                required_permission_refs=contract.required_permission_refs,
                declared_side_effects=tuple(str(x) for x in contract.declared_side_effects),
                expected_artifact_types=(),
                can_enqueue=blocked_reason is None,
                blocked_reason=blocked_reason,
            ))
        edges = tuple(AgentOrchestrationPlanEdge(dep, node.node_id) for node in blueprint.nodes for dep in node.depends_on_node_ids)
        enqueue_supported = any(node.can_enqueue for node in nodes)
        return AgentOrchestrationPlan(workflow_id=workflow_id, valid=True, reason="ok", nodes=tuple(nodes), edges=edges, warnings=validation.warnings, planning_supported=workflow_id in workflow_registry, enqueue_supported=enqueue_supported)

    def validate_blueprint(self, *, workflow_id: str) -> AgentOrchestrationValidationResult:
        workflow_registry = get_agent_workflow_registry()
        task_registry = get_agent_task_registry()
        blueprints = get_agent_workflow_blueprints()
        if workflow_id not in workflow_registry:
            return AgentOrchestrationValidationResult(False, "unknown_workflow_id")
        if workflow_id not in blueprints:
            return AgentOrchestrationValidationResult(False, "missing_blueprint")
        blueprint = blueprints[workflow_id]
        if len(blueprint.nodes) > settings.agent_orchestration_max_tasks_per_listing:
            return AgentOrchestrationValidationResult(False, "max_tasks_per_listing_exceeded")
        node_ids = {node.node_id for node in blueprint.nodes}
        warnings = []
        for node in blueprint.nodes:
            if node.task_type not in task_registry:
                return AgentOrchestrationValidationResult(False, "unknown_task_type", (node.node_id,))
            for dep in node.depends_on_node_ids:
                if dep not in node_ids:
                    return AgentOrchestrationValidationResult(False, "unknown_dependency_node", (node.node_id,))
        if self._has_cycle(blueprint):
            return AgentOrchestrationValidationResult(False, "dependency_cycle")
        if max(self._node_depths(blueprint).values(), default=0) > settings.agent_orchestration_max_chain_depth:
            return AgentOrchestrationValidationResult(False, "max_chain_depth_exceeded")
        roots = [node.node_id for node in blueprint.nodes if not node.depends_on_node_ids]
        if not roots:
            return AgentOrchestrationValidationResult(False, "no_root_nodes")
        for node in blueprint.nodes:
            contract = task_registry[node.task_type]
            if set(contract.declared_side_effects) & PR40_UNSAFE_ENQUEUE_SIDE_EFFECTS:
                warnings.append(f"{node.node_id}:unsafe_side_effects_for_pr40_enqueue")
        return AgentOrchestrationValidationResult(True, warnings=tuple(warnings))

    def enqueue_workflow(self, *, workflow_id: str, listing_external_id: str | None = None, listing_analysis_id: int | None = None, search_job_id: int | None = None, context_key: str | None = None, payload_seed: dict | None = None, dry_run: bool = True) -> AgentOrchestrationEnqueueResult:
        plan = self.build_plan(workflow_id=workflow_id)
        if not plan.valid:
            return AgentOrchestrationEnqueueResult(False, dry_run, workflow_id, blocked_reason=plan.reason, plan=plan.to_dict())
        if dry_run:
            return AgentOrchestrationEnqueueResult(True, True, workflow_id, blocked_reason=None if plan.enqueue_supported else "no_implemented_root_nodes", plan=plan.to_dict())
        if not settings.agent_orchestration_enabled:
            return AgentOrchestrationEnqueueResult(False, False, workflow_id, blocked_reason="orchestration_disabled", plan=plan.to_dict())
        if self.db is None:
            return AgentOrchestrationEnqueueResult(False, False, workflow_id, blocked_reason="db_session_required", plan=plan.to_dict())
        repo = AgentTaskRepository(self.db)
        enqueued: list[int] = []
        existing: list[int] = []
        run_id: str | None = None
        for node in plan.nodes:
            if not node.can_enqueue:
                continue
            payload = self._payload(workflow_id, node.node_id, node.task_type, listing_external_id, listing_analysis_id, search_job_id, context_key, payload_seed)
            dedupe_key = self._dedupe_key(payload)
            before = repo.get_by_dedupe_key(dedupe_key)
            if before is not None:
                existing.append(before.id)
                run_id = run_id or before.orchestration_run_id
                continue
            run_id = run_id or f"orch_{uuid4().hex}"
            task = repo.create_or_get_task(task_type=node.task_type, dedupe_key=dedupe_key, priority=100, listing_external_id=listing_external_id, listing_analysis_id=listing_analysis_id, search_job_id=search_job_id, context_key=context_key, payload_json=payload, orchestration_run_id=run_id, workflow_id=workflow_id, chain_depth=0, blocking=node.blocking, dependency_status="ready", orchestration_status="queued")
            enqueued.append(task.id)
        return AgentOrchestrationEnqueueResult(True, False, workflow_id, run_id, tuple(enqueued), tuple(existing), None if enqueued or existing else "no_implemented_root_nodes", plan.to_dict())

    def summarize_run(self, *, orchestration_run_id: str) -> AgentOrchestrationRunSummary:
        if self.db is None:
            raise ValueError("db_session_required")
        tasks = list(self.db.scalars(select(AgentTask).where(AgentTask.orchestration_run_id == orchestration_run_id)))
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        artifacts = tuple({k: v for k, v in serialize_agent_artifact(a, include_payload=False).items() if k in {"artifact_id", "artifact_type", "redaction_status", "source_task_id", "orchestration_run_id", "created_at"}} for a in list_agent_artifacts(self.db, orchestration_run_id=orchestration_run_id, limit=100))
        return AgentOrchestrationRunSummary(orchestration_run_id, len(tasks), counts, len(artifacts), artifacts)

    @staticmethod
    def _payload(workflow_id, node_id, task_type, listing_external_id, listing_analysis_id, search_job_id, context_key, payload_seed):
        return {"schema_version": "agent-orchestrator-input-v0", "workflow_id": workflow_id, "node_id": node_id, "task_type": task_type, "listing_external_id": listing_external_id, "listing_analysis_id": listing_analysis_id, "search_job_id": search_job_id, "context_key": context_key, "payload_seed": payload_seed or {}, "orchestrator_policy_version": ORCHESTRATOR_POLICY_VERSION}

    @staticmethod
    def _dedupe_key(payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = sha256(raw.encode()).hexdigest()[:32]
        return f"orchestration:{payload['workflow_id']}:{payload['listing_external_id'] or 'none'}:{payload['context_key'] or 'none'}:{payload['node_id']}:{digest}"

    @staticmethod
    def _node_depths(blueprint: AgentWorkflowBlueprint) -> dict[str, int]:
        depths = {node.node_id: 0 for node in blueprint.nodes}
        changed = True
        while changed:
            changed = False
            for node in blueprint.nodes:
                depth = 0 if not node.depends_on_node_ids else 1 + max(depths[dep] for dep in node.depends_on_node_ids)
                if depths[node.node_id] != depth:
                    depths[node.node_id] = depth
                    changed = True
        return depths

    @staticmethod
    def _has_cycle(blueprint: AgentWorkflowBlueprint) -> bool:
        deps = {node.node_id: set(node.depends_on_node_ids) for node in blueprint.nodes}
        temp: set[str] = set()
        done: set[str] = set()

        def visit(node_id: str) -> bool:
            if node_id in temp:
                return True
            if node_id in done:
                return False
            temp.add(node_id)
            if any(visit(dep) for dep in deps[node_id]):
                return True
            temp.remove(node_id)
            done.add(node_id)
            return False

        return any(visit(node.node_id) for node in blueprint.nodes)
