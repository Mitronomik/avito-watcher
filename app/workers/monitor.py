import time

from app.db.init_db import init_db
from app.services.monitor_service import MonitorService


def run_monitor_cycle() -> None:
    init_db()
    service = MonitorService()
    results = service.run_all_searches()
    print(results)


def main() -> None:
    init_db()
    while True:
        try:
            run_monitor_cycle()
        except Exception as exc:
            print({"worker_error": str(exc)})
        time.sleep(20)


if __name__ == "__main__":
    main()
