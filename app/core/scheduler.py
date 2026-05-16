from apscheduler.schedulers.background import BackgroundScheduler
from app.workers.monitor import run_monitor_cycle


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.scheduler.add_job(run_monitor_cycle, "interval", minutes=3, id="monitor_cycle")
        self.scheduler.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False


scheduler_service = SchedulerService()
