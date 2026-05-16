from sqlalchemy import String, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class SearchJob(Base):
    __tablename__ = "search_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    poll_interval_sec: Mapped[int] = mapped_column(Integer, default=180)
