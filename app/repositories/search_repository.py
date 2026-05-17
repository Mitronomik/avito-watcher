import random
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.search_job import SearchJob

DEFAULT_POLL_INTERVAL_SEC = 180
FAILED_CHECK_BACKOFF_CAP_SEC = 7200
NEXT_RUN_JITTER_SEC = 15


class SearchRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, search_job_id: int) -> SearchJob | None:
        return self.db.get(SearchJob, search_job_id)

    def list_all(self) -> list[SearchJob]:
        return list(self.db.scalars(select(SearchJob)).all())

    def list_active(self) -> list[SearchJob]:
        return list(self.db.scalars(select(SearchJob).where(SearchJob.is_active.is_(True))).all())

    def list_due_active(self, now: datetime) -> list[SearchJob]:
        return list(
            self.db.scalars(
                select(SearchJob).where(
                    SearchJob.is_active.is_(True),
                    or_(SearchJob.next_run_at.is_(None), SearchJob.next_run_at <= now),
                )
            ).all()
        )

    def create(self, name: str, source_url: str, filters_json: dict | None = None, poll_interval_sec: int = 180) -> SearchJob:
        item = SearchJob(
            name=name,
            source_url=source_url,
            filters_json=filters_json or {},
            poll_interval_sec=poll_interval_sec,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def deactivate(self, search: SearchJob) -> None:
        search.is_active = False

    def activate(self, search: SearchJob) -> None:
        search.is_active = True

    def pause(self, search: SearchJob) -> None:
        self.deactivate(search)

    def resume(self, search: SearchJob) -> None:
        self.activate(search)

    def update_filters(self, search: SearchJob, filters_json: dict) -> None:
        search.filters_json = dict(filters_json)

    def mark_baseline_initialized(self, search: SearchJob, checked_at: datetime) -> None:
        search.baseline_initialized = True
        search.baseline_initialized_at = checked_at

    def record_successful_check(self, search: SearchJob, checked_at: datetime) -> None:
        search.last_checked_at = checked_at
        search.last_success_at = checked_at
        search.last_error = ""
        search.fail_count = 0
        self.update_next_run_at(search, checked_at)

    def record_failed_check(self, search: SearchJob, checked_at: datetime, error: str) -> None:
        search.last_checked_at = checked_at
        search.last_error = error[:2048]
        search.fail_count = (search.fail_count or 0) + 1
        self.update_next_run_at(search, checked_at)

    def update_next_run_at(self, search: SearchJob, checked_at: datetime) -> None:
        interval_sec = self._next_interval_sec(search)
        jitter_sec = random.randint(-NEXT_RUN_JITTER_SEC, NEXT_RUN_JITTER_SEC)
        next_run_at = checked_at + timedelta(seconds=interval_sec + jitter_sec)
        search.next_run_at = max(next_run_at, checked_at)

    @staticmethod
    def _next_interval_sec(search: SearchJob) -> int:
        base_interval = search.poll_interval_sec or DEFAULT_POLL_INTERVAL_SEC
        fail_count = search.fail_count or 0
        if fail_count <= 0:
            return base_interval

        backoff = base_interval * (2 ** (fail_count - 1))
        return min(backoff, FAILED_CHECK_BACKOFF_CAP_SEC)
