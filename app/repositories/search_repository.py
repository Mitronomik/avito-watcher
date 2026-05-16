from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.search_job import SearchJob


class SearchRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_all(self) -> list[SearchJob]:
        return list(self.db.scalars(select(SearchJob)).all())

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
