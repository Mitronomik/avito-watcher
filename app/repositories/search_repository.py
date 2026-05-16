from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.search_job import SearchJob


class SearchRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, search_job_id: int) -> SearchJob | None:
        return self.db.get(SearchJob, search_job_id)

    def list_all(self) -> list[SearchJob]:
        return list(self.db.scalars(select(SearchJob)).all())

    def list_active(self) -> list[SearchJob]:
        if hasattr(SearchJob, "is_active"):
            return list(self.db.scalars(select(SearchJob).where(SearchJob.is_active.is_(True))).all())
        return self.list_all()

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
        search.next_run_at = checked_at + timedelta(seconds=search.poll_interval_sec or 180)
