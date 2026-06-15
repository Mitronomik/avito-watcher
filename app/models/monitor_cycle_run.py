from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MONITOR_CYCLE_STATUS_RUNNING = "running"
MONITOR_CYCLE_STATUS_SUCCESS = "success"
MONITOR_CYCLE_STATUS_FAILED = "failed"
MONITOR_CYCLE_STATUS_PARTIAL = "partial"
MONITOR_CYCLE_STATUS_SKIPPED = "skipped"
MONITOR_CYCLE_STATUS_UNKNOWN = "unknown"
MONITOR_CYCLE_STATUSES = {
    MONITOR_CYCLE_STATUS_RUNNING,
    MONITOR_CYCLE_STATUS_SUCCESS,
    MONITOR_CYCLE_STATUS_FAILED,
    MONITOR_CYCLE_STATUS_PARTIAL,
    MONITOR_CYCLE_STATUS_SKIPPED,
    MONITOR_CYCLE_STATUS_UNKNOWN,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MonitorCycleRun(Base):
    __tablename__ = "monitor_cycle_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'success', 'failed', 'partial', 'skipped', 'unknown')",
            name="ck_monitor_cycle_runs_status",
        ),
        Index("ix_monitor_cycle_runs_started_at", "started_at"),
        Index("ix_monitor_cycle_runs_status_started_at", "status", "started_at"),
        Index("ix_monitor_cycle_runs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    searches_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    searches_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    searches_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    listings_seen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listings_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listings_updated: Mapped[int | None] = mapped_column(Integer, nullable=True)

    analysis_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_succeeded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    alert_delivery_attempts_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alerts_sent_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alert_delivery_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alert_delivery_unknown: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_status_file: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)
