from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


ALLOWED_KNOWLEDGE_NOTE_TYPES = {"rulebook", "false_positive", "domain_note"}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class KnowledgeNote(Base):
    __tablename__ = "knowledge_notes"
    __table_args__ = (
        CheckConstraint(
            "note_type IN ('rulebook', 'false_positive', 'domain_note')",
            name="ck_knowledge_notes_note_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_type: Mapped[str] = mapped_column(String(32), index=True)
    profile: Mapped[str] = mapped_column(String(128), default="global", index=True)
    title: Mapped[str] = mapped_column(String(200))
    body_md: Mapped[str] = mapped_column(Text)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
