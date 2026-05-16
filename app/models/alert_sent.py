from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class AlertSent(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_external_id: Mapped[str] = mapped_column(String(128), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
