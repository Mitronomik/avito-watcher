from app.db.init_db import init_db
from app.services.monitor_service import MonitorService


def run_monitor_cycle() -> None:
    init_db()
    service = MonitorService()
    results = service.run_all_searches()
    print(results)
