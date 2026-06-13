from app.models.agent_task import AgentTask
from app.services.agent_task_runner import AgentTaskHandlerResult
from app.services.data_quality_agent import (
    DEFAULT_PROFILE,
    ENRICHMENT_TYPE,
    SOURCE_TYPE,
    TASK_TYPE,
    DataQualityAgentError,
    DataQualityAgentService,
)

DATA_QUALITY_AGENT_TASK_TYPE = TASK_TYPE


class DataQualityAgentTaskHandler:
    def __init__(self, db, service: DataQualityAgentService | None = None) -> None:
        self.db = db
        self.service = service or DataQualityAgentService(db)

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        payload = task.payload_json or {}
        listing_external_id = (
            payload.get("listing_external_id") or task.listing_external_id
        )
        if not listing_external_id:
            return AgentTaskHandlerResult(
                status="failed",
                error_type="data_quality_agent_invalid_payload",
                error_message="listing_external_id is required",
            )
        try:
            result = self.service.assess(
                listing_external_id=str(listing_external_id),
                listing_analysis_id=payload.get("listing_analysis_id")
                or task.listing_analysis_id,
                snapshot_id=payload.get("snapshot_id"),
                extraction_enrichment_id=payload.get("extraction_enrichment_id"),
                quality_profile=str(payload.get("quality_profile") or DEFAULT_PROFILE),
            )
        except DataQualityAgentError as exc:
            if exc.error_type == "data_quality_agent_disabled":
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
        row = result.enrichment
        assessment = row.structured_facts_json or {}
        return AgentTaskHandlerResult(
            status="success",
            result_json={
                "listing_enrichment_id": row.id,
                "enrichment_type": ENRICHMENT_TYPE,
                "listing_external_id": row.listing_external_id,
                "source_type": SOURCE_TYPE,
                "source_id": row.source_id,
                "status": row.status,
                "validation_status": row.validation_status,
                "overall_status": assessment.get("overall_status"),
                "review_priority": assessment.get("review_priority"),
                "should_human_review": assessment.get("should_human_review"),
                "issues_count": len(assessment.get("issues") or []),
                "contradictions_count": len(assessment.get("contradictions") or []),
                "missing_evidence_count": len(assessment.get("missing_evidence") or []),
                "human_review_recommendations_count": len(
                    assessment.get("human_review_recommendations") or []
                ),
                "has_recommended_rule_patch": assessment.get("recommended_rule_patch")
                is not None,
                "rag_notes_used": [
                    r.get("note_id")
                    for r in assessment.get("rag_references") or []
                    if isinstance(r, dict)
                ],
                "prompt_version": row.prompt_version,
                "schema_version": row.schema_version,
                "model": row.model,
                "input_hash": row.input_hash,
                "output_hash": row.output_hash,
                "created": result.created,
            },
        )
