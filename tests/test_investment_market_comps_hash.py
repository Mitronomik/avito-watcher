from datetime import UTC, datetime, timedelta
from app.analysis.config import AnalysisConfig
from app.analysis.market_comps import (
    select_market_evidence,
    market_evidence_fingerprint_hash,
)
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun

AS_OF = datetime(2026, 6, 14, tzinfo=UTC)


def _item(db, **kw):
    run = db.query(MarketResearchRun).first() or MarketResearchRun(
        agent_task_id=444,
        status="success",
        schema_version="research-agent-result-v1",
        checked_at=AS_OF.replace(tzinfo=None),
    )
    db.add(run)
    db.flush()
    base = dict(
        run_id=run.id,
        evidence_type="comparable_candidate",
        listing_external_id="l1",
        asset_type="commercial",
        deal_type="rent",
        source_url="https://e",
        source_url_normalized="https://e",
        confidence=0.8,
        is_reusable=True,
        checked_at=(AS_OF - timedelta(days=1)).replace(tzinfo=None),
        expires_at=(AS_OF + timedelta(days=1)).replace(tzinfo=None),
        content_hash=f"h{db.query(MarketEvidenceItem).count()}",
        rent_rub_per_month=1,
    )
    base.update(kw)
    obj = MarketEvidenceItem(**base)
    db.add(obj)
    db.flush()
    return obj


def _ctx(db):
    cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment", {"use_market_evidence": True}
    )
    return select_market_evidence(
        candidates=db.query(MarketEvidenceItem)
        .filter_by(listing_external_id="l1")
        .all(),
        config=cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
    )


def test_fingerprint_stable_and_changes_only_for_selected_evidence(db_session):
    _item(db_session, content_hash="a")
    h1 = market_evidence_fingerprint_hash(_ctx(db_session))
    assert h1 == market_evidence_fingerprint_hash(_ctx(db_session))
    _item(db_session, listing_external_id="other", content_hash="irrelevant")
    assert h1 == market_evidence_fingerprint_hash(_ctx(db_session))
    _item(db_session, content_hash="b")
    assert h1 != market_evidence_fingerprint_hash(_ctx(db_session))


def test_max_age_and_expiration_use_explicit_as_of(db_session):
    _item(
        db_session,
        content_hash="old",
        checked_at=(AS_OF - timedelta(days=31)).replace(tzinfo=None),
    )
    _item(
        db_session,
        content_hash="expired",
        expires_at=(AS_OF - timedelta(seconds=1)).replace(tzinfo=None),
    )
    ctx = _ctx(db_session)
    assert ctx.items == []
    assert ctx.excluded_counts_by_reason["too_old"] == 1
    assert ctx.excluded_counts_by_reason["expired"] == 1


def test_matching_policy_and_selected_cross_listing_evidence_affect_hash(db_session):
    _item(db_session, listing_external_id="l1", location_key="loc", content_hash="same")
    _item(
        db_session,
        listing_external_id="other",
        location_key="loc",
        content_hash="cross",
    )
    same_cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "market_evidence_matching_policy": "same_listing",
            "market_evidence_location_key": "loc",
        },
    )
    cross_cfg = AnalysisConfig.from_search_filters(
        "commercial_sale_investment",
        {
            "use_market_evidence": True,
            "market_evidence_matching_policy": "same_location_key",
            "market_evidence_location_key": "loc",
        },
    )
    same_ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=same_cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="l1",
    )
    cross_ctx = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cross_cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="l1",
    )
    assert market_evidence_fingerprint_hash(
        same_ctx
    ) != market_evidence_fingerprint_hash(cross_ctx)
    h1 = market_evidence_fingerprint_hash(cross_ctx)
    _item(
        db_session,
        listing_external_id="other2",
        location_key="wrong",
        content_hash="irrelevant",
    )
    cross_ctx_2 = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cross_cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF.replace(hour=12),
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="l1",
    )
    assert h1 == market_evidence_fingerprint_hash(cross_ctx_2)
    _item(
        db_session,
        listing_external_id="other3",
        location_key="loc",
        content_hash="selected-new",
    )
    cross_ctx_3 = select_market_evidence(
        candidates=db_session.query(MarketEvidenceItem).all(),
        config=cross_cfg,
        expected_asset_type="commercial",
        evidence_retrieval_as_of_datetime=AS_OF,
        evidence_retrieval_as_of_date=AS_OF.date(),
        target_listing_external_id="l1",
    )
    assert h1 != market_evidence_fingerprint_hash(cross_ctx_3)
