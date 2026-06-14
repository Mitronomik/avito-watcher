from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun


class MarketEvidenceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_run_by_agent_task_id(self, agent_task_id: int) -> MarketResearchRun | None:
        return self.db.scalar(
            select(MarketResearchRun).where(
                MarketResearchRun.agent_task_id == agent_task_id
            )
        )

    def create_run(self, **values) -> MarketResearchRun:
        run = MarketResearchRun(**values)
        self.db.add(run)
        self.db.flush()
        return run

    def get_item_by_run_type_hash(
        self, run_id: int, evidence_type: str, content_hash: str
    ) -> MarketEvidenceItem | None:
        return self.db.scalar(
            select(MarketEvidenceItem).where(
                MarketEvidenceItem.run_id == run_id,
                MarketEvidenceItem.evidence_type == evidence_type,
                MarketEvidenceItem.content_hash == content_hash,
            )
        )

    def create_item(self, **values) -> MarketEvidenceItem:
        item = MarketEvidenceItem(**values)
        self.db.add(item)
        self.db.flush()
        return item

    def retrieve_items(
        self,
        *,
        listing_external_id: str | None = None,
        research_profile: str | None = None,
        asset_type: str | None = None,
        deal_type: str | None = None,
        location_text: str | None = None,
        location_key: str | None = None,
        evidence_types: list[str] | None = None,
        include_expired: bool = False,
        include_non_reusable: bool = False,
        min_confidence: float | None = None,
        limit: int = 10,
        now: datetime | None = None,
    ) -> list[MarketEvidenceItem]:
        stmt = select(MarketEvidenceItem)
        if listing_external_id:
            stmt = stmt.where(
                MarketEvidenceItem.listing_external_id == str(listing_external_id)
            )
        if research_profile:
            stmt = stmt.where(MarketEvidenceItem.research_profile == research_profile)
        if asset_type:
            stmt = stmt.where(MarketEvidenceItem.asset_type == asset_type)
        if deal_type:
            stmt = stmt.where(MarketEvidenceItem.deal_type == deal_type)
        if location_key:
            stmt = stmt.where(MarketEvidenceItem.location_key == location_key)
        elif location_text:
            stmt = stmt.where(
                MarketEvidenceItem.location_key
                == " ".join(location_text.lower().split())
            )
        if evidence_types:
            stmt = stmt.where(MarketEvidenceItem.evidence_type.in_(evidence_types))
        if not include_non_reusable:
            stmt = stmt.where(MarketEvidenceItem.is_reusable.is_(True))
        if min_confidence is not None:
            stmt = stmt.where(MarketEvidenceItem.confidence >= min_confidence)
        if not include_expired and now is not None:
            stmt = stmt.where(
                (MarketEvidenceItem.expires_at.is_(None))
                | (MarketEvidenceItem.expires_at > now)
            )
        stmt = stmt.order_by(
            MarketEvidenceItem.is_reusable.desc(),
            MarketEvidenceItem.expires_at.desc().nullsfirst(),
            MarketEvidenceItem.confidence.desc().nullslast(),
            MarketEvidenceItem.checked_at.desc(),
            MarketEvidenceItem.id.desc(),
        ).limit(max(0, min(limit, 100)))
        return list(self.db.scalars(stmt).all())
