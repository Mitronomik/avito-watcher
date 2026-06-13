from app.models.agent_task import AgentTask
from app.services.agent_task_runner import AgentTaskHandlerResult
from app.services.listing_detail_extraction import (
    DEFAULT_PROFILE,
    ENRICHMENT_TYPE,
    SOURCE_TYPE,
    ListingDetailExtractionError,
    ListingDetailExtractionService,
    TASK_TYPE,
)

LISTING_DETAIL_EXTRACTION_TASK_TYPE = TASK_TYPE


class ListingDetailExtractionAgentTaskHandler:
    def __init__(
        self, db, service: ListingDetailExtractionService | None = None
    ) -> None:
        self.db = db
        self.service = service or ListingDetailExtractionService(db)

    def handle(self, task: AgentTask) -> AgentTaskHandlerResult:
        payload = task.payload_json or {}
        snapshot_id = payload.get("snapshot_id")
        listing_external_id = (
            payload.get("listing_external_id") or task.listing_external_id
        )
        if snapshot_id is None and not listing_external_id:
            return AgentTaskHandlerResult(
                status="failed",
                error_type="listing_detail_extraction_invalid_payload",
                error_message="snapshot_id or listing_external_id is required",
            )
        try:
            result = self.service.extract(
                snapshot_id=int(snapshot_id) if snapshot_id is not None else None,
                listing_external_id=str(listing_external_id)
                if listing_external_id
                else None,
                extraction_profile=str(
                    payload.get("extraction_profile") or DEFAULT_PROFILE
                ),
            )
        except ListingDetailExtractionError as exc:
            if exc.error_type == "listing_detail_extraction_disabled":
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
        return AgentTaskHandlerResult(
            status="success",
            result_json={
                "listing_enrichment_id": row.id,
                "enrichment_type": ENRICHMENT_TYPE,
                "listing_external_id": row.listing_external_id,
                "snapshot_id": row.source_id,
                "source_type": SOURCE_TYPE,
                "status": row.status,
                "validation_status": row.validation_status,
                "prompt_version": row.prompt_version,
                "schema_version": row.schema_version,
                "model": row.model,
                "input_hash": row.input_hash,
                "output_hash": row.output_hash,
                "created": result.created,
            },
        )
