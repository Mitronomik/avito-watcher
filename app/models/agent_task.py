from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


ALLOWED_AGENT_TASK_STATUSES = {
    "pending",
    "running",
    "success",
    "failed",
    "canceled",
    "skipped",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'canceled', 'skipped')",
            name="ck_agent_tasks_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    listing_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    listing_analysis_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    search_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    context_key: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
