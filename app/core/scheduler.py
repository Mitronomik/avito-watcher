from apscheduler.schedulers.background import BackgroundScheduler
from app.workers.monitor import _build_parser, run_monitor_cycle


class SchedulerService:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler()
        self._started = False
        self._parser = None

    def start(self) -> None:
        if self._started:
            return
        self._parser = _build_parser()
        self.scheduler.add_job(
            run_monitor_cycle,
            "interval",
            minutes=3,
            id="monitor_cycle",
            args=[self._parser],
        )
        self.scheduler.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.scheduler.shutdown(wait=False)
        self._started = False


scheduler_service = SchedulerService()
