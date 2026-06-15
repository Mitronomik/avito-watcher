from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.monitor_cycle_run import (
    MONITOR_CYCLE_STATUSES,
    MONITOR_CYCLE_STATUS_FAILED,
    MONITOR_CYCLE_STATUS_PARTIAL,
    MONITOR_CYCLE_STATUS_RUNNING,
    MonitorCycleRun,
)
from app.services.alert_delivery_attempts import sanitize_alert_delivery_error

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@dataclass(slots=True)
class MonitorCycleRunMetrics:
    searches_total: int | None = None
    searches_processed: int | None = None
    searches_failed: int | None = None
    listings_seen: int | None = None
    listings_created: int | None = None
    listings_updated: int | None = None
    analysis_attempted: int | None = None
    analysis_succeeded: int | None = None
    analysis_failed: int | None = None
    alert_delivery_attempts_created: int | None = None
    alerts_sent_created: int | None = None
    alert_delivery_failed: int | None = None
    alert_delivery_unknown: int | None = None


class MonitorCycleRunService:
    def __init__(self, session_factory=SessionLocal) -> None:
        self.session_factory = session_factory

    def start_cycle(self, *, started_at: datetime | None = None, worker_status_file: str | None = None) -> int | None:
        try:
            with self.session_factory() as db:
                row = MonitorCycleRun(
                    started_at=_as_naive_utc(started_at or _now()),
                    status=MONITOR_CYCLE_STATUS_RUNNING,
                    worker_status_file=_safe_worker_status_file(worker_status_file or settings.monitor_worker_status_path),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                return row.id
        except Exception as exc:  # pragma: no cover - defensive isolation
            logger.warning("monitor cycle ledger start failed: %s", sanitize_alert_delivery_error(str(exc)))
            return None

    def finish_cycle(
        self,
        cycle_run_id: int | None,
        *,
        status: str,
        finished_at: datetime | None = None,
        metrics: MonitorCycleRunMetrics | None = None,
        error: BaseException | str | None = None,
    ) -> None:
        if cycle_run_id is None:
            return
        if status not in MONITOR_CYCLE_STATUSES:
            logger.warning("monitor cycle ledger invalid status: %s", sanitize_alert_delivery_error(status))
            status = MONITOR_CYCLE_STATUS_FAILED
        try:
            with self.session_factory() as db:
                row = db.get(MonitorCycleRun, cycle_run_id)
                if row is None:
                    return
                done_at = _as_naive_utc(finished_at or _now())
                row.finished_at = done_at
                row.duration_ms = max(0, int((done_at - row.started_at).total_seconds() * 1000))
                if status != MONITOR_CYCLE_STATUS_FAILED and metrics and metrics.searches_failed and metrics.searches_failed > 0:
                    status = MONITOR_CYCLE_STATUS_PARTIAL
                row.status = status
                if metrics is not None:
                    for key, value in asdict(metrics).items():
                        setattr(row, key, value)
                if error is not None:
                    row.error_type = type(error).__name__ if isinstance(error, BaseException) else "error"
                    row.last_error = sanitize_monitor_cycle_error(error)
                row.updated_at = _now()
                db.commit()
        except Exception as exc:  # pragma: no cover - defensive isolation
            logger.warning("monitor cycle ledger finish failed: %s", sanitize_alert_delivery_error(str(exc)))


def sanitize_monitor_cycle_error(error: BaseException | str, limit: int = 1500) -> str:
    text = str(error)
    text = sanitize_alert_delivery_error(text)
    text = " ".join(line for line in text.splitlines() if not line.lstrip().startswith("Traceback"))
    text = text[:limit]
    return text or "[redacted]"


def _safe_worker_status_file(path: str | None) -> str | None:
    if not path:
        return None
    name = Path(path).name
    return name[:255] if name else None


def collect_cycle_metrics(results: list[dict] | None, *, started_at: datetime) -> MonitorCycleRunMetrics:
    searches_processed = len(results) if results is not None else None
    searches_failed = sum(1 for item in (results or []) if isinstance(item, dict) and item.get("error")) if results is not None else None
    listings_seen = _sum_result_keys(results, ("seen", "listings_seen", "cards_seen", "parsed_count"))
    listings_created = _sum_result_keys(results, ("new", "created", "listings_created", "new_listings"))
    listings_updated = _sum_result_keys(results, ("updated", "listings_updated"))
    return MonitorCycleRunMetrics(
        searches_total=searches_processed,
        searches_processed=searches_processed,
        searches_failed=searches_failed,
        listings_seen=listings_seen,
        listings_created=listings_created,
        listings_updated=listings_updated,
    )


def _sum_result_keys(results: list[dict] | None, keys: tuple[str, ...]) -> int | None:
    if results is None:
        return None
    total = 0
    found = False
    for item in results:
        if not isinstance(item, dict):
            continue
        for key in keys:
            if isinstance(item.get(key), int):
                total += item[key]
                found = True
                break
    return total if found else None
