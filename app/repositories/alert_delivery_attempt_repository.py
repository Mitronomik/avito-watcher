from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.alert_delivery_attempt import AlertDeliveryAttempt


class AlertDeliveryAttemptRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def next_attempt_count(self, *, dedupe_key: str, channel: str) -> int:
        previous = self.db.scalar(
            select(func.max(AlertDeliveryAttempt.attempt_count)).where(
                AlertDeliveryAttempt.dedupe_key == dedupe_key,
                AlertDeliveryAttempt.channel == channel,
            )
        )
        return int(previous or 0) + 1

    def create_attempt(
        self,
        *,
        listing_external_id: str,
        channel: str,
        dedupe_key: str,
        payload_hash: str,
        status: str,
        attempt_count: int,
        last_error: str | None = None,
        next_retry_at: datetime | None = None,
        sent_at: datetime | None = None,
        search_job_id: int | None = None,
        search_name: str | None = None,
        error_type: str | None = None,
    ) -> AlertDeliveryAttempt:
        item = AlertDeliveryAttempt(
            listing_external_id=listing_external_id,
            channel=channel,
            dedupe_key=dedupe_key,
            payload_hash=payload_hash,
            status=status,
            attempt_count=attempt_count,
            last_error=last_error,
            next_retry_at=next_retry_at,
            sent_at=sent_at,
            search_job_id=search_job_id,
            search_name=search_name,
            error_type=error_type,
        )
        self.db.add(item)
        self.db.flush()
        return item
