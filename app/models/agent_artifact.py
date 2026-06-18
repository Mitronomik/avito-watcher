from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


AGENT_ARTIFACT_TYPES = (
    "evidence_candidates",
    "normalized_evidence",
    "data_gap_report",
    "call_questions",
    "decision_wording",
    "claim_review",
    "report_draft",
    "offer_draft",
    "presentation_outline",
    "geo_context",
    "portfolio_memory_finding",
)

AGENT_ARTIFACT_REDACTION_STATUSES = (
    "not_required",
    "redacted",
    "blocked",
    "unknown",
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        CheckConstraint(
            "artifact_type IN ('evidence_candidates', 'normalized_evidence', 'data_gap_report', 'call_questions', 'decision_wording', 'claim_review', 'report_draft', 'offer_draft', 'presentation_outline', 'geo_context', 'portfolio_memory_finding')",
            name="ck_agent_artifacts_artifact_type",
        ),
        CheckConstraint(
            "redaction_status IN ('not_required', 'redacted', 'blocked', 'unknown')",
            name="ck_agent_artifacts_redaction_status",
        ),
        CheckConstraint("length(trim(input_hash)) > 0", name="ck_agent_artifacts_input_hash_not_empty"),
        CheckConstraint("length(trim(content_hash)) > 0", name="ck_agent_artifacts_content_hash_not_empty"),
        CheckConstraint("length(trim(schema_version)) > 0", name="ck_agent_artifacts_schema_version_not_empty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    listing_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    listing_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    search_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    context_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"), nullable=True, index=True)
    orchestration_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_refs_json: Mapped[list | dict] = mapped_column(JSON, nullable=False)
    redaction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, server_default=func.now(), nullable=False, index=True)
