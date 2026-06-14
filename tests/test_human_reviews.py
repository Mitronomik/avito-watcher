import pytest
from sqlalchemy import inspect, select, func
from app.db.base import Base

from app.models.human_review import HumanReviewAction, InvestmentDecision
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.services.human_reviews import HumanReviewService, HumanReviewValidationError, build_review_context_key


def test_tables_exist(db_session):
    names = set(inspect(db_session.bind).get_table_names())
    assert {"human_reviews", "human_review_actions", "investment_decisions"} <= names


def test_create_review_generates_context_and_action(db_session):
    review = HumanReviewService(db_session).create_review(listing_external_id="752", review_status="needs_review", human_verdict="interesting", next_action="call_owner", reviewer="human", confirmed_purchase_price_rub="12000000")
    assert review.review_context_key == build_review_context_key("752")
    assert review.reviewed_at is not None
    assert str(review.confirmed_purchase_price_rub) == "12000000"
    assert db_session.scalar(select(func.count()).select_from(HumanReviewAction)) == 1
    assert db_session.scalars(select(HumanReviewAction)).one().action_type == "created"


def test_create_review_with_explicit_context_key_removes_context_type(db_session):
    review = HumanReviewService(db_session).create_review(
        listing_external_id="x",
        review_context_key="custom",
        context_type="expert",
    )

    assert review.review_context_key == "custom"


def test_create_review_linked_to_listing_and_analysis(db_session):
    listing = Listing(external_id="l1", url="https://example.test", title="t")
    db_session.add(listing)
    db_session.flush()
    analysis = ListingAnalysis(listing_external_id="l1", input_hash="h", status="success")
    db_session.add(analysis)
    db_session.flush()
    review = HumanReviewService(db_session).create_review(listing_external_id="l1", listing_id=listing.id, listing_analysis_id=analysis.id)
    assert review.listing_id == listing.id
    assert review.listing_analysis_id == analysis.id


def test_update_review_compact_diff_and_preserves_history(db_session):
    service = HumanReviewService(db_session)
    review = service.create_review(listing_external_id="u1")
    service.update_review(review.id, review_status="reviewed", outcome_status="sent_to_expert", next_action="send_to_expert", notes="Sent")
    actions = list(db_session.scalars(select(HumanReviewAction).order_by(HumanReviewAction.id)))
    assert [a.action_type for a in actions] == ["created", "outcome_updated"]
    assert actions[1].before_json == {"review_status": "new", "next_action": None, "outcome_status": None, "notes": None, "reviewed_at": None}
    assert set(actions[1].after_json) == {"review_status", "next_action", "outcome_status", "notes", "reviewed_at"}
    assert review.reviewed_at is not None


@pytest.mark.parametrize("field,value", [
    ("review_context_key", "other"),
    ("listing_external_id", "other"),
    ("listing_id", 1),
    ("search_job_id", 1),
    ("listing_analysis_id", 1),
])
def test_update_review_rejects_identity_context_mutation(db_session, field, value):
    service = HumanReviewService(db_session)
    review = service.create_review(listing_external_id="immutable")
    original_context_key = review.review_context_key
    original_external_id = review.listing_external_id

    with pytest.raises(HumanReviewValidationError):
        service.update_review(review.id, **{field: value})

    assert review.review_context_key == original_context_key
    assert review.listing_external_id == original_external_id


def test_record_investment_decision_appends_action(db_session):
    service = HumanReviewService(db_session)
    review = service.create_review(listing_external_id="d1")
    decision = service.record_investment_decision(review.id, decision_type="send_to_expert", decision_status="done", actor="human", amount_rub="10")
    assert decision.listing_external_id == "d1"
    assert db_session.scalar(select(func.count()).select_from(InvestmentDecision)) == 1
    assert [a.action_type for a in db_session.scalars(select(HumanReviewAction).order_by(HumanReviewAction.id))] == ["created", "investment_decision_recorded"]


@pytest.mark.parametrize("kwargs", [
    {"listing_external_id": ""},
    {"listing_external_id": "x", "review_status": "bad"},
    {"listing_external_id": "x", "human_verdict": "bad"},
    {"listing_external_id": "x", "next_action": "bad"},
    {"listing_external_id": "x", "rejected_reason": "bad"},
    {"listing_external_id": "x", "outcome_status": "bad"},
    {"listing_external_id": "x", "confirmed_purchase_price_rub": "-1"},
    {"listing_external_id": "x", "confirmed_opex_ratio": "1.1"},
    {"listing_external_id": "x", "confirmed_vacancy_rate": "1.1"},
    {"listing_external_id": "x", "false_positive": True, "false_negative": True},
    {"listing_external_id": "x", "notes": "x" * 5001},
])
def test_create_validation_rejects_invalid_input(db_session, kwargs):
    with pytest.raises(HumanReviewValidationError):
        HumanReviewService(db_session).create_review(**kwargs)


def test_false_positive_negative_semantics_do_not_mutate_analysis(db_session):
    analysis = ListingAnalysis(listing_external_id="fn1", input_hash="h", status="success", score=10, verdict="weak")
    db_session.add(analysis)
    db_session.flush()
    service = HumanReviewService(db_session)
    fp = service.create_review(listing_external_id="fp1", human_verdict="false_positive")
    fn = service.create_review(listing_external_id="fn1", listing_analysis_id=analysis.id, human_verdict="false_negative")
    assert fp.false_positive is True
    assert fn.false_negative is True
    assert analysis.score == 10
    assert analysis.verdict == "weak"


def test_context_and_query_helpers(db_session):
    service = HumanReviewService(db_session)
    first = service.create_review(listing_external_id="same", search_job_id=None, context_type="listing")
    second = service.create_review(listing_external_id="same", review_context_key=build_review_context_key("same", None, None, "expert"), review_status="needs_review", watchlist=True)
    assert service.get_latest_review_for_listing("same").id == second.id
    assert service.get_latest_review_for_listing("same", first.review_context_key).id == first.id
    assert service.get_reviews_by_context(second.review_context_key) == [second]
    assert service.list_reviews(review_status="needs_review") == [second]
    assert service.list_reviews(watchlist=True) == [second]
    with pytest.raises(HumanReviewValidationError):
        service.create_review(listing_external_id="same", review_context_key=first.review_context_key)


def test_decision_validation_and_no_side_effect_counts(db_session):
    counts_before = {name: db_session.scalar(select(func.count()).select_from(table)) for name, table in Base.metadata.tables.items() if name not in {"human_reviews", "human_review_actions", "investment_decisions"}}
    service = HumanReviewService(db_session)
    review = service.create_review(listing_external_id="safe")
    service.update_review(review.id, human_verdict="interesting")
    with pytest.raises(HumanReviewValidationError, match="decision_type is required"):
        service.record_investment_decision(review.id, decision_status="done")
    with pytest.raises(HumanReviewValidationError, match="decision_status is required"):
        service.record_investment_decision(review.id, decision_type="offer")
    with pytest.raises(HumanReviewValidationError):
        service.record_investment_decision(review.id, decision_type="bad", decision_status="done")
    with pytest.raises(HumanReviewValidationError):
        service.record_investment_decision(review.id, decision_type="offer", decision_status="bad")
    with pytest.raises(HumanReviewValidationError):
        service.record_investment_decision(review.id, decision_type="offer", decision_status="proposed", amount_rub="-1")
    service.record_investment_decision(review.id, decision_type="offer", decision_status="proposed")
    counts_after = {name: db_session.scalar(select(func.count()).select_from(table)) for name, table in Base.metadata.tables.items() if name not in {"human_reviews", "human_review_actions", "investment_decisions"}}
    assert counts_after == counts_before


def test_watchlist_sent_to_expert_and_deal_candidate_are_outcomes_not_review_statuses(db_session):
    service = HumanReviewService(db_session)
    watchlist_review = service.create_review(
        listing_external_id="watch",
        review_status="reviewed",
        watchlist=True,
        outcome_status="watchlist",
    )
    expert_review = service.create_review(
        listing_external_id="expert",
        review_status="reviewed",
        outcome_status="sent_to_expert",
    )
    deal_review = service.create_review(
        listing_external_id="deal",
        review_status="reviewed",
        outcome_status="deal_candidate",
    )
    expert_decision = service.record_investment_decision(
        expert_review.id,
        decision_type="send_to_expert",
        decision_status="done",
    )
    deal_decision = service.record_investment_decision(
        deal_review.id,
        decision_type="deal_candidate",
        decision_status="approved",
    )

    assert watchlist_review.review_status == "reviewed"
    assert watchlist_review.watchlist is True
    assert watchlist_review.outcome_status == "watchlist"
    assert expert_review.review_status == "reviewed"
    assert expert_review.outcome_status == "sent_to_expert"
    assert expert_decision.decision_type == "send_to_expert"
    assert deal_review.review_status == "reviewed"
    assert deal_review.outcome_status == "deal_candidate"
    assert deal_decision.decision_type == "deal_candidate"

    for invalid_status in ("watchlist", "sent_to_expert", "deal_candidate"):
        with pytest.raises(HumanReviewValidationError):
            service.create_review(
                listing_external_id=f"invalid-{invalid_status}",
                review_status=invalid_status,
            )
