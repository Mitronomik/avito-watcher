from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_task import ALLOWED_AGENT_TASK_STATUSES, AgentTask


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentTaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_dedupe_key(self, dedupe_key: str) -> AgentTask | None:
        return self.db.scalar(select(AgentTask).where(AgentTask.dedupe_key == dedupe_key))

    def create_or_get_task(
        self,
        *,
        task_type: str,
        dedupe_key: str,
        status: str = "pending",
        priority: int = 100,
        listing_external_id: str | None = None,
        listing_analysis_id: int | None = None,
        search_job_id: int | None = None,
        context_key: str | None = None,
        payload_json: dict | None = None,
        result_json: dict | None = None,
    ) -> AgentTask:
        self._validate_status(status)
        existing = self.get_by_dedupe_key(dedupe_key)
        if existing is not None:
            return existing

        task = AgentTask(
            task_type=task_type,
            status=status,
            priority=priority,
            listing_external_id=listing_external_id,
            listing_analysis_id=listing_analysis_id,
            search_job_id=search_job_id,
            context_key=context_key,
            dedupe_key=dedupe_key,
            payload_json=payload_json or {},
            result_json=result_json or {},
        )
        self.db.add(task)
        self.db.flush()
        return task

    def list_pending(self, limit: int, task_type: str | None = None) -> list[AgentTask]:
        if limit <= 0:
            return []
        stmt = select(AgentTask).where(AgentTask.status == "pending")
        if task_type is not None:
            stmt = stmt.where(AgentTask.task_type == task_type)
        stmt = stmt.order_by(AgentTask.priority.asc(), AgentTask.created_at.asc(), AgentTask.id.asc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def mark_running(self, task: AgentTask) -> AgentTask:
        self._ensure_transition(task, {"pending", "failed"})
        task.status = "running"
        task.started_at = task.started_at or _now()
        task.finished_at = None
        task.error_type = None
        task.error_message = None
        task.updated_at = _now()
        self.db.flush()
        return task

    def mark_success(self, task: AgentTask, result_json: dict | None = None) -> AgentTask:
        self._ensure_transition(task, {"pending", "running"})
        task.status = "success"
        if result_json is not None:
            task.result_json = result_json
        task.error_type = None
        task.error_message = None
        task.finished_at = _now()
        task.updated_at = _now()
        self.db.flush()
        return task

    def mark_failed(self, task: AgentTask, error_type: str, error_message: str) -> AgentTask:
        self._ensure_transition(task, {"pending", "running"})
        task.status = "failed"
        task.error_type = error_type[:128]
        task.error_message = error_message
        task.finished_at = _now()
        task.updated_at = _now()
        self.db.flush()
        return task

    def mark_canceled(self, task: AgentTask) -> AgentTask:
        self._ensure_transition(task, {"pending", "running"})
        task.status = "canceled"
        task.finished_at = _now()
        task.updated_at = _now()
        self.db.flush()
        return task

    def mark_skipped(self, task: AgentTask, result_json: dict | None = None) -> AgentTask:
        self._ensure_transition(task, {"pending", "running"})
        task.status = "skipped"
        if result_json is not None:
            task.result_json = result_json
        task.finished_at = _now()
        task.updated_at = _now()
        self.db.flush()
        return task

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in ALLOWED_AGENT_TASK_STATUSES:
            raise ValueError(f"Unsupported agent task status: {status}")

    @staticmethod
    def _ensure_transition(task: AgentTask, allowed_from: set[str]) -> None:
        if task.status not in allowed_from:
            raise ValueError(f"Cannot transition agent task from {task.status}")
