from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent_task import ALLOWED_AGENT_TASK_STATUSES, AgentTask
from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.listing_detail_snapshot import ListingDetailSnapshot
from app.models.listing_enrichment import ListingEnrichment
from app.models.monitor_cycle_run import MonitorCycleRun

RETENTION_DRY_RUN_REPORTING_WINDOWS_DAYS: dict[str, int] = {
    "alert_delivery_attempts": 90,
    "monitor_cycle_runs": 90,
    "agent_tasks": 180,
    "listing_detail_snapshots": 180,
    "listing_enrichments": 180,
}

AGENT_TASK_TERMINAL_STATUSES = frozenset(
    status for status in {"success", "failed", "skipped", "canceled"} if status in ALLOWED_AGENT_TASK_STATUSES
)


@dataclass(frozen=True)
class RetentionDryRunTableReport:
    table_name: str
    policy_label: str
    dry_run_candidate_after_days: int
    timestamp_column: str | None
    dry_run_candidate_count: int | None
    total_count: int | None
    oldest_candidate_at: datetime | None
    newest_candidate_at: datetime | None
    status: str
    notes: str


@dataclass(frozen=True)
class RetentionDryRunReport:
    generated_at: datetime
    rows: tuple[RetentionDryRunTableReport, ...]


def _unknown_row(table_name: str, days: int, notes: str, *, status: str = "unknown") -> RetentionDryRunTableReport:
    return RetentionDryRunTableReport(
        table_name=table_name,
        policy_label="reporting-only threshold",
        dry_run_candidate_after_days=days,
        timestamp_column=None,
        dry_run_candidate_count=None,
        total_count=None,
        oldest_candidate_at=None,
        newest_candidate_at=None,
        status=status,
        notes=notes,
    )


def _supported_row(
    db: Session,
    *,
    model: Any,
    table_name: str,
    timestamp_column: Any,
    timestamp_column_name: str,
    days: int,
    now: datetime,
    extra_filter: Any | None = None,
    notes: str = "Aggregate read-only count; no row IDs are returned.",
) -> RetentionDryRunTableReport:
    cutoff = now - timedelta(days=days)
    filters = [timestamp_column < cutoff]
    if extra_filter is not None:
        filters.append(extra_filter)
    try:
        total_count = db.scalar(select(func.count()).select_from(model)) or 0
        dry_run_candidate_count = db.scalar(select(func.count()).select_from(model).where(*filters)) or 0
        oldest_candidate_at = db.scalar(select(func.min(timestamp_column)).select_from(model).where(*filters))
        newest_candidate_at = db.scalar(select(func.max(timestamp_column)).select_from(model).where(*filters))
        return RetentionDryRunTableReport(
            table_name=table_name,
            policy_label="reporting-only threshold",
            dry_run_candidate_after_days=days,
            timestamp_column=timestamp_column_name,
            dry_run_candidate_count=int(dry_run_candidate_count),
            total_count=int(total_count),
            oldest_candidate_at=oldest_candidate_at,
            newest_candidate_at=newest_candidate_at,
            status="supported",
            notes=notes,
        )
    except Exception:
        return _unknown_row(table_name, days, "Aggregate dry-run query failed; metric is unknown.")


def get_retention_dry_run_report(db: Session, now: datetime) -> RetentionDryRunReport:
    rows: list[RetentionDryRunTableReport] = []
    windows = RETENTION_DRY_RUN_REPORTING_WINDOWS_DAYS
    rows.append(
        _supported_row(
            db,
            model=AlertDeliveryAttempt,
            table_name="alert_delivery_attempts",
            timestamp_column=AlertDeliveryAttempt.created_at,
            timestamp_column_name="created_at",
            days=windows["alert_delivery_attempts"],
            now=now,
        )
    )
    rows.append(
        _supported_row(
            db,
            model=MonitorCycleRun,
            table_name="monitor_cycle_runs",
            timestamp_column=MonitorCycleRun.started_at,
            timestamp_column_name="started_at",
            days=windows["monitor_cycle_runs"],
            now=now,
        )
    )
    if AGENT_TASK_TERMINAL_STATUSES:
        rows.append(
            _supported_row(
                db,
                model=AgentTask,
                table_name="agent_tasks",
                timestamp_column=AgentTask.created_at,
                timestamp_column_name="created_at",
                days=windows["agent_tasks"],
                now=now,
                extra_filter=AgentTask.status.in_(sorted(AGENT_TASK_TERMINAL_STATUSES)),
                notes="Terminal statuses only: " + ", ".join(sorted(AGENT_TASK_TERMINAL_STATUSES)) + ". Aggregate read-only count; no row IDs are returned.",
            )
        )
    else:
        rows.append(_unknown_row("agent_tasks", windows["agent_tasks"], "Terminal status set is not defined", status="not_supported"))
    rows.append(
        _supported_row(
            db,
            model=ListingDetailSnapshot,
            table_name="listing_detail_snapshots",
            timestamp_column=ListingDetailSnapshot.created_at,
            timestamp_column_name="created_at",
            days=windows["listing_detail_snapshots"],
            now=now,
        )
    )
    rows.append(
        _supported_row(
            db,
            model=ListingEnrichment,
            table_name="listing_enrichments",
            timestamp_column=ListingEnrichment.created_at,
            timestamp_column_name="created_at",
            days=windows["listing_enrichments"],
            now=now,
        )
    )
    return RetentionDryRunReport(generated_at=now, rows=tuple(rows))
