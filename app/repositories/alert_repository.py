from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.alert_sent import AlertSent


class AlertRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def exists_by_dedupe_key(self, dedupe_key: str) -> bool:
        return self.db.scalar(select(AlertSent).where(AlertSent.dedupe_key == dedupe_key)) is not None

    def create(self, listing_external_id: str, dedupe_key: str, channel: str = "telegram") -> AlertSent:
        item = AlertSent(
            listing_external_id=listing_external_id,
            dedupe_key=dedupe_key,
            channel=channel,
        )
        self.db.add(item)
        self.db.flush()
        return item
