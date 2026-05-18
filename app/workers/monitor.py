import logging
import os
import random
import time

from app.db.init_db import init_db
from app.parsers.avito_parser import AvitoParser
from app.parsers.proxy_manager import ProxyManager
from app.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)

WORKER_CADENCE_SEC = 60
WORKER_CADENCE_JITTER_SEC = 2


def _build_parser() -> AvitoParser:
    """Create AvitoParser with ProxyManager if PROXY_URLS env var is set."""
    raw = os.getenv("PROXY_URLS", "").strip()
    proxy_urls = [u.strip() for u in raw.split(",") if u.strip()]
    if proxy_urls:
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
    return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
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
