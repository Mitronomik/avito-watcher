import logging
import random
import time

from app.db.init_db import init_db
from app.services.monitor_service import MonitorService

logger = logging.getLogger(__name__)

WORKER_CADENCE_SEC = 20
WORKER_CADENCE_JITTER_SEC = 2


def run_monitor_cycle() -> list[dict]:
    service = MonitorService()
    results = service.run_all_searches()
    logger.info("monitor cycle completed", extra={"results": results})
    return results


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    while True:
        try:
            run_monitor_cycle()
        except Exception:
            logger.exception("worker cycle failed")
        sleep_for = WORKER_CADENCE_SEC + random.uniform(
            -WORKER_CADENCE_JITTER_SEC,
            WORKER_CADENCE_JITTER_SEC,
        )
        time.sleep(max(1, sleep_for))


if __name__ == "__main__":
    main()
