from app.models.agent_task import AgentTask
from app.services.agent_task_runner import AgentTaskHandlerResult
from app.services.research_agent import (
    DEFAULT_RESEARCH_PROFILE,
    MARKET_RESEARCH_TASK_TYPE as _MARKET_RESEARCH_TASK_TYPE,
    ResearchAgentError,
    ResearchAgentService,
)

MARKET_RESEARCH_TASK_TYPE = _MARKET_RESEARCH_TASK_TYPE


class ResearchAgentTaskHandler:
    def __init__(self, db, service: ResearchAgentService | None = None) -> None:
        self.db = db
        self.service = service or ResearchAgentService(db)

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        payload = task.payload_json or {}
        if not isinstance(payload, dict):
            return AgentTaskHandlerResult(
                status="failed",
                error_type="research_agent_invalid_payload",
                error_message="payload_json must be an object",
            )
        listing_external_id = (
            payload.get("listing_external_id") or task.listing_external_id
        )
        if not listing_external_id:
            return AgentTaskHandlerResult(
                status="failed",
                error_type="research_agent_invalid_payload",
                error_message="listing_external_id is required",
            )
        questions = payload.get("research_questions") or []
        if not isinstance(questions, list) or not all(
            isinstance(q, str) for q in questions
        ):
            return AgentTaskHandlerResult(
                status="failed",
                error_type="research_agent_invalid_payload",
                error_message="research_questions must be strings",
            )
        try:
            result = self.service.run(
                listing_external_id=str(listing_external_id),
                listing_analysis_id=payload.get("listing_analysis_id")
                or task.listing_analysis_id,
                research_profile=str(
                    payload.get("research_profile") or DEFAULT_RESEARCH_PROFILE
                ),
                research_questions=questions,
                max_queries=payload.get("max_queries"),
            )
        except ResearchAgentError as exc:
            if exc.error_type == "research_agent_disabled":
                return AgentTaskHandlerResult(
                    status="skipped",
                    result_json={
                        "status": "skipped",
                        "error_type": exc.error_type,
                        "message": str(exc),
                    },
                )
            return AgentTaskHandlerResult(
                status="failed", error_type=exc.error_type, error_message=str(exc)
            )
        return AgentTaskHandlerResult(status="success", result_json=result)
