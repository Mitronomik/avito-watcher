from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        Index("ix_admin_audit_events_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False, default=_now)
    actor_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="admin_technical_key")
    actor_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    request_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
