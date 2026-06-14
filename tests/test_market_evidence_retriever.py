from datetime import UTC, datetime, timedelta

from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun
from app.services.market_evidence import MarketEvidenceRetriever


def add_item(db, **kw):
    run = db.query(MarketResearchRun).first() or MarketResearchRun(
        agent_task_id=999,
        status="success",
        schema_version="research-agent-result-v1",
        checked_at=datetime.now(UTC).replace(tzinfo=None),
        research_profile="default",
    )
    db.add(run)
    db.flush()
    base = dict(
        run_id=run.id,
        evidence_type="finding",
        research_profile="default",
        listing_external_id="ext",
        asset_type="commercial",
        deal_type="rent",
        location_text="SPb",
        location_key="spb",
        claim="claim",
        source_url="https://example.com",
        source_url_normalized="https://example.com/",
        source_indexes_json=[0],
        evidence_json={},
        confidence=0.8,
        is_reusable=True,
        checked_at=datetime.now(UTC).replace(tzinfo=None),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
        content_hash=str(id(kw)),
    )
    base.update(kw)
    item = MarketEvidenceItem(**base)
    db.add(item)
    db.commit()
    return item


def test_retrieval_filters_and_limit(db_session):
    add_item(db_session, listing_external_id="a", evidence_type="finding")
    add_item(db_session, listing_external_id="b", evidence_type="comparable_candidate")
    out = MarketEvidenceRetriever(db_session).retrieve(
        listing_external_id="a", evidence_types=["finding"], limit=1
    )
    assert len(out) == 1
    assert out[0].listing_external_id == "a"


def test_retrieval_excludes_expired_low_confidence_non_reusable_by_default(db_session):
    add_item(db_session, content_hash="good")
    add_item(
        db_session,
        content_hash="expired",
        expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
    )
    add_item(db_session, content_hash="low", confidence=0.2)
    add_item(db_session, content_hash="no", is_reusable=False)
    out = MarketEvidenceRetriever(db_session).retrieve()
    assert [i.content_hash for i in out] == ["good"]
    all_items = MarketEvidenceRetriever(db_session).retrieve(
        include_expired=True, include_non_reusable=True, min_confidence=0, limit=10
    )
    assert {i.content_hash for i in all_items} >= {"good", "expired", "low", "no"}


def test_retrieval_by_profile_asset_deal_location_and_order(db_session):
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    add_item(db_session, content_hash="older", confidence=0.9, checked_at=old)
    add_item(db_session, content_hash="newer", confidence=0.9)
    add_item(db_session, content_hash="best", confidence=0.95)
    out = MarketEvidenceRetriever(db_session).retrieve(
        research_profile="default",
        asset_type="commercial",
        deal_type="rent",
        location_text=" SPb ",
    )
    assert [i.content_hash for i in out][:3] == ["best", "newer", "older"]
