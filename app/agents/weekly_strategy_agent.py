from app.models.agent_task import AgentTask
from app.services.agent_task_runner import AgentTaskHandlerResult
from app.services.weekly_strategy_agent import WeeklyStrategyAgentService

WEEKLY_STRATEGY_AGENT_TASK_TYPE = "weekly_strategy_agent"


class WeeklyStrategyAgentTaskHandler:
    def __init__(self, db, service: WeeklyStrategyAgentService | None = None) -> None:
        self.db = db
        self.service = service or WeeklyStrategyAgentService(db)

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        result = self.service.run(task)
        if result.get("status") == "skipped":
            return AgentTaskHandlerResult(status="skipped", result_json=result)
        if result.get("status") == "failed":
            return AgentTaskHandlerResult(
                status="failed",
                error_type=result.get("error_type") or "weekly_strategy_agent_failed",
                error_message=result.get("error_message") or result.get("error_type") or "Weekly StrategyAgent failed",
            )
        return AgentTaskHandlerResult(status="success", result_json=result)
