import fcntl
import logging
from pathlib import Path
import random
import time
from datetime import datetime, timezone

from app.db.init_db import init_db
from app.parsers.avito_parser import AvitoParser
from app.parsers.proxy_manager import ProxyManager
from app.parsers.proxy_url import validate_proxy_urls
from app.core.config import settings
from app.services.monitor_service import MonitorService, runtime_diagnostics
from app.services.monitor_cycle_runs import (
    MonitorCycleRunService,
    collect_cycle_metrics,
    count_alert_delivery_attempts,
    count_alerts_sent,
)
from app.models.monitor_cycle_run import MONITOR_CYCLE_STATUS_FAILED, MONITOR_CYCLE_STATUS_SUCCESS
from app.workers.status import build_worker_status, write_worker_status_atomic

logger = logging.getLogger(__name__)

WORKER_CADENCE_SEC = 60
WORKER_CADENCE_JITTER_SEC = 2


class MonitorWorkerLock:
    def __init__(self, lock_path: str):
        self._path = Path(lock_path)
        self._lock_file = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._path.open("a+")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            logger.error(
                "monitor worker already running; lock_path=%s", self._path
            )
            raise SystemExit(1)
        self._lock_file = lock_file
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None


def _build_parser() -> AvitoParser:
    """Create AvitoParser with ProxyManager if PROXY_URLS env var is set."""
    raw = settings.proxy_urls.strip()
    proxy_urls = [u.strip() for u in raw.split(",") if u.strip()]
    if proxy_urls:
        proxy_urls = validate_proxy_urls(proxy_urls)
        manager = ProxyManager(
            proxy_urls,
            quarantine_seconds=settings.proxy_quarantine_seconds,
        )
        logger.info(
            "proxy_manager initialized proxies=%d quarantine_seconds=%d",
            manager.total,
            settings.proxy_quarantine_seconds,
        )
    else:
        manager = None
        logger.warning(
            "PROXY_URLS not set — running without proxies (likely blocked by Avito)"
        )
    return AvitoParser(proxy_manager=manager)


def run_monitor_cycle(parser: AvitoParser) -> list[dict]:
    service = MonitorService(parser=parser)
    results = service.run_all_searches()
    logger.info("monitor cycle completed", extra={"results": results})
    if parser._proxy_manager is not None:
        logger.info("proxy pool stats: %s", parser._proxy_manager.stats())
    return results


def _parser_cycle_stats(parser: AvitoParser) -> dict:
    cycle_stats = getattr(parser, "cycle_stats", None)
    if not callable(cycle_stats):
        return {}
    try:
        stats = cycle_stats()
    except Exception as exc:  # pragma: no cover - defensive observability only
        logger.warning("failed to collect parser cycle stats for worker status: %s", exc)
        return {}
    return stats if isinstance(stats, dict) else {}


def _write_cycle_status(
    parser: AvitoParser,
    *,
    cycle_started_at: datetime,
    cycle_finished_at: datetime,
    cycle_ok: bool,
    results: list[dict] | None = None,
    error: BaseException | None = None,
) -> None:
    result_count = len(results) if results is not None else 0
    payload = build_worker_status(
        cycle_started_at=cycle_started_at,
        cycle_finished_at=cycle_finished_at,
        cycle_ok=cycle_ok,
        searches_processed=result_count,
        result_count=result_count,
        parser_stats=_parser_cycle_stats(parser),
        error=error,
    )
    try:
        write_worker_status_atomic(settings.monitor_worker_status_path, payload)
    except Exception as exc:
        logger.warning("failed to write worker status file: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with MonitorWorkerLock(settings.monitor_worker_lock_path):
        init_db()
        parser = _build_parser()
        logger.info("monitor worker runtime diagnostics: %s", runtime_diagnostics())
        while True:
            cycle_started_at = datetime.now(timezone.utc)
            ledger = MonitorCycleRunService()
            cycle_run_id = ledger.start_cycle(started_at=cycle_started_at)
            attempts_before = count_alert_delivery_attempts()
            alerts_before = count_alerts_sent()
            results = None
            try:
                results = run_monitor_cycle(parser)
            except Exception as exc:
                logger.exception("worker cycle failed")
                cycle_finished_at = datetime.now(timezone.utc)
                _write_cycle_status(
                    parser,
                    cycle_started_at=cycle_started_at,
                    cycle_finished_at=cycle_finished_at,
                    cycle_ok=False,
                    results=results,
                    error=exc,
                )
                metrics = collect_cycle_metrics(results, started_at=cycle_started_at)
                attempts_after = count_alert_delivery_attempts()
                alerts_after = count_alerts_sent()
                metrics.alert_delivery_attempts_created = (attempts_after - attempts_before) if attempts_before is not None and attempts_after is not None else None
                metrics.alerts_sent_created = (alerts_after - alerts_before) if alerts_before is not None and alerts_after is not None else None
                ledger.finish_cycle(cycle_run_id, status=MONITOR_CYCLE_STATUS_FAILED, finished_at=cycle_finished_at, metrics=metrics, error=exc)
            else:
                cycle_finished_at = datetime.now(timezone.utc)
                _write_cycle_status(
                    parser,
                    cycle_started_at=cycle_started_at,
                    cycle_finished_at=cycle_finished_at,
                    cycle_ok=True,
                    results=results,
                )
                metrics = collect_cycle_metrics(results, started_at=cycle_started_at)
                attempts_after = count_alert_delivery_attempts()
                alerts_after = count_alerts_sent()
                metrics.alert_delivery_attempts_created = (attempts_after - attempts_before) if attempts_before is not None and attempts_after is not None else None
                metrics.alerts_sent_created = (alerts_after - alerts_before) if alerts_before is not None and alerts_after is not None else None
                ledger.finish_cycle(cycle_run_id, status=MONITOR_CYCLE_STATUS_SUCCESS, finished_at=cycle_finished_at, metrics=metrics)
            sleep_for = WORKER_CADENCE_SEC + random.uniform(
                -WORKER_CADENCE_JITTER_SEC,
                WORKER_CADENCE_JITTER_SEC,
            )
            time.sleep(max(1, sleep_for))


if __name__ == "__main__":
    main()
