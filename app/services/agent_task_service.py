from app.models.agent_task import AgentTask
from app.models.listing_analysis import ListingAnalysis
from app.repositories.agent_task_repository import AgentTaskRepository

_IDENTITY_PAYLOAD_FIELDS = {
    "listing_external_id",
    "listing_analysis_id",
    "search_job_id",
    "context_key",
    "analysis_input_hash",
}


def _recommended_next_action(verdict: str | None) -> str | None:
    if verdict == "strong":
        return "review_listing"
    if verdict in {"review", "medium"}:
        return "manual_review"
    if verdict == "weak":
        return "skip_or_low_priority"
    return None


class AgentTaskService:
    def __init__(self, repository: AgentTaskRepository) -> None:
        self.repository = repository

    def create_task_from_analysis(
        self,
        analysis: ListingAnalysis,
        *,
        task_type: str | None = None,
        priority: int | None = None,
        payload_extra: dict | None = None,
    ) -> AgentTask:
        if analysis.id is None:
            raise ValueError("ListingAnalysis must be flushed before creating an agent task")
        task_type = task_type or self._task_type_for_analysis(analysis)
        priority = priority if priority is not None else self._priority_for_analysis(analysis)
        dedupe_key = f"agent:{task_type}:analysis:{analysis.id}"
        payload_json = self._payload_for_analysis(analysis)
        if payload_extra:
            safe_extra = {
                key: value
                for key, value in payload_extra.items()
                if key not in _IDENTITY_PAYLOAD_FIELDS
            }
            payload_json.update(safe_extra)

        return self.repository.create_or_get_task(
            task_type=task_type,
            dedupe_key=dedupe_key,
            priority=priority,
            listing_external_id=analysis.listing_external_id,
            listing_analysis_id=analysis.id,
            search_job_id=analysis.search_job_id,
            context_key=analysis.context_key,
            payload_json=payload_json,
        )

    @staticmethod
    def _task_type_for_analysis(analysis: ListingAnalysis) -> str:
        if analysis.verdict == "strong":
            return "review_listing"
        if analysis.verdict in {"review", "medium"}:
            return "manual_review"
        if analysis.verdict == "weak":
            return "ignore_candidate"
        return "manual_review"

    @staticmethod
    def _priority_for_analysis(analysis: ListingAnalysis) -> int:
        score = analysis.score
        if analysis.verdict == "strong" or (score is not None and score >= 80):
            return 20
        if analysis.verdict in {"review", "medium"} or (score is not None and score >= 60):
            return 50
        if analysis.verdict == "weak" or (score is not None and score < 60):
            return 100
        return 80

    @staticmethod
    def _payload_for_analysis(analysis: ListingAnalysis) -> dict:
        facts_json = analysis.facts_json if isinstance(analysis.facts_json, dict) else {}
        risks_json = analysis.risks_json if isinstance(analysis.risks_json, dict) else {}
        questions_json = analysis.questions_json if isinstance(analysis.questions_json, dict) else {}
        risk_flags = risks_json.get("flags")
        if not isinstance(risk_flags, list):
            risk_flags = []
        questions = questions_json.get("items")
        if not isinstance(questions, list):
            questions = []
        analysis_config = facts_json.get("analysis_config")
        if not isinstance(analysis_config, dict):
            analysis_config = {}

        return {
            "listing_external_id": analysis.listing_external_id,
            "listing_analysis_id": analysis.id,
            "search_job_id": analysis.search_job_id,
            "context_key": analysis.context_key,
            "profile": analysis.profile,
            "analysis_version": analysis.analysis_version,
            "analysis_status": analysis.status,
            "analysis_score": analysis.score,
            "analysis_verdict": analysis.verdict,
            "analysis_input_hash": analysis.input_hash,
            "risk_flags": risk_flags,
            "questions": questions,
            "recommended_next_action": _recommended_next_action(analysis.verdict),
            "analysis_config_hash": analysis_config.get("hash"),
            "analysis_config": analysis_config,
            "report_md": analysis.report_md or "",
        }
