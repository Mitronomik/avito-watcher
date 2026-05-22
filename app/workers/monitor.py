import fcntl
import logging
from pathlib import Path
import random
import time

from app.db.init_db import init_db
from app.parsers.avito_parser import AvitoParser
from app.parsers.proxy_manager import ProxyManager
from app.parsers.proxy_url import validate_proxy_urls
from app.core.config import settings
from app.services.monitor_service import MonitorService

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
        manager = ProxyManager(proxy_urls, quarantine_seconds=7200)
        logger.info(
            "proxy_manager initialized proxies=%d", manager.total
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with MonitorWorkerLock(settings.monitor_worker_lock_path):
        init_db()
        parser = _build_parser()
        while True:
            try:
                run_monitor_cycle(parser)
            except Exception:
                logger.exception("worker cycle failed")
            sleep_for = WORKER_CADENCE_SEC + random.uniform(
                -WORKER_CADENCE_JITTER_SEC,
                WORKER_CADENCE_JITTER_SEC,
            )
            time.sleep(max(1, sleep_for))


if __name__ == "__main__":
    main()
