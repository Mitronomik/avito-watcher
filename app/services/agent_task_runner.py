from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.models.agent_task import AgentTask
from app.repositories.agent_task_repository import AgentTaskRepository


@dataclass(frozen=True)
class AgentTaskHandlerResult:
    status: Literal["success", "skipped"] = "success"
    result_json: dict | None = None


class AgentTaskHandler(Protocol):
    def handle(self, task: AgentTask) -> AgentTaskHandlerResult: ...


class NoopAgentTaskHandler:
    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        return AgentTaskHandlerResult(
            status="success",
            result_json={
                "handler": "noop",
                "message": "No-op agent task handler executed.",
                "task_type": task.task_type,
            },
        )


class MissingAgentTaskHandler:
    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        return AgentTaskHandlerResult(
            status="skipped",
            result_json={
                "reason": "no_handler_registered",
                "task_type": task.task_type,
            },
        )


class AgentTaskRunner:
    def __init__(
        self,
        repository: AgentTaskRepository,
        handlers: dict[str, AgentTaskHandler] | None = None,
    ) -> None:
        self.repository = repository
        self.handlers = handlers or {}
        self.missing_handler = MissingAgentTaskHandler()

    def run_pending(
        self,
        limit: int,
        task_type: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        if limit <= 0:
            return {
                "ok": False,
                "error_type": "validation_error",
                "error": "limit must be a positive integer",
                "limit": limit,
                "task_type": task_type,
                "dry_run": dry_run,
            }

        tasks = self.repository.list_pending(limit=limit, task_type=task_type)
        if dry_run:
            return {
                "ok": True,
                "limit": limit,
                "task_type": task_type,
                "dry_run": True,
                "pending": len(tasks),
                "tasks": [self._task_to_json(task) for task in tasks],
            }

        result = {
            "ok": True,
            "limit": limit,
            "task_type": task_type,
            "dry_run": False,
            "processed": 0,
            "succeeded": 0,
            "skipped": 0,
            "failed": 0,
            "tasks": [],
        }

        for task in tasks:
            result["processed"] += 1
            try:
                self.repository.mark_running(task)
                handler = self.handlers.get(task.task_type, self.missing_handler)
                handler_result = handler.handle(task)
                if handler_result.status == "success":
                    self.repository.mark_success(task, handler_result.result_json)
                    result["succeeded"] += 1
                elif handler_result.status == "skipped":
                    self.repository.mark_skipped(task, handler_result.result_json)
                    result["skipped"] += 1
                else:
                    raise ValueError(f"Unsupported agent task handler status: {handler_result.status}")
            except Exception as exc:
                self.repository.mark_failed(task, exc.__class__.__name__, str(exc))
                result["failed"] += 1
            result["tasks"].append(self._task_to_json(task))

        return result

    @staticmethod
    def _task_to_json(task: AgentTask) -> dict:
        return {
            "id": task.id,
            "task_type": task.task_type,
            "status": task.status,
            "priority": task.priority,
            "listing_external_id": task.listing_external_id,
            "listing_analysis_id": task.listing_analysis_id,
            "search_job_id": task.search_job_id,
            "context_key": task.context_key,
            "dedupe_key": task.dedupe_key,
            "payload_json": task.payload_json,
            "result_json": task.result_json,
            "error_type": task.error_type,
            "error_message": task.error_message,
            "created_at": AgentTaskRunner._datetime_to_json(task.created_at),
            "updated_at": AgentTaskRunner._datetime_to_json(task.updated_at),
            "started_at": AgentTaskRunner._datetime_to_json(task.started_at),
            "finished_at": AgentTaskRunner._datetime_to_json(task.finished_at),
        }

    @staticmethod
    def _datetime_to_json(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()
