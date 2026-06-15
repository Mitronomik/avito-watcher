from datetime import datetime

from sqlalchemy import func, select

from app.models.admin_audit_event import AdminAuditEvent
from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from app.models.human_review import HumanReview, InvestmentDecision
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.services.human_review_queue import get_human_review_queue_rows
from tests.test_admin_ui import _add_alert_sent, _add_attempt, create_listing, create_listing_analysis, make_raw_client


def _count_audit(Session):
    with Session() as s:
        return s.scalar(select(func.count()).select_from(AdminAuditEvent))


def test_review_queue_access_control_and_no_audit(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(Session, external_id="ext-q", title="Queue")

    assert client.get("/admin/review-queue").status_code == 403
    assert client.get("/admin/review-queue", headers={"X-API-Key": "bad"}).status_code == 403
    assert client.get("/admin/review-queue?admin_technical_write_key=tech", headers={"X-API-Key": "read"}).status_code == 200
    before = _count_audit(Session)
    assert client.get("/admin/review-queue", headers={"X-API-Key": "read"}).status_code == 200
    assert _count_audit(Session) == before
    assert client.post("/admin/review-queue", headers={"X-API-Key": "read"}).status_code in {404, 405}


def test_review_queue_page_is_read_only_and_escapes(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(
        Session,
        external_id="ext-html",
        title="<b>unsafe title</b> " + "x" * 180,
        address="<script>alert(1)</script>",
        url="https://www.avito.ru/item?x=1",
    )
    create_listing_analysis(
        Session,
        listing_external_id="ext-html",
        score=7.5,
        verdict="strong",
        risks_json={"flags": ["missing_area"]},
    )

    html = client.get("/admin/review-queue", headers={"X-API-Key": "read"}).text
    assert "<form" not in html.lower()
    assert "admin_technical_write_key" not in html
    assert "confirm_action" not in html
    for forbidden in ["mark reviewed", "approve", "reject", "assign", "comment", "shortlist", "run agent", "retry alert"]:
        assert forbidden not in html.lower()
    assert "&lt;b&gt;unsafe title&lt;/b&gt;" in html
    assert "<script>" not in html
    assert "payload_json" not in html
    assert "result_json" not in html
    assert "webhook" not in html.lower()
    assert "script.google.com" not in html.lower()


def test_review_queue_read_model_one_row_latest_aggregates_and_unknowns(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    listing_id = create_listing(Session, external_id="ext-one", title="One", last_seen_at=datetime(2026, 1, 3))
    create_listing(Session, external_id="ext-empty", title="No analysis", last_seen_at=datetime(2026, 1, 2))
    old = create_listing_analysis(Session, listing_external_id="ext-one", profile="old", score=1.0, verdict="reject", input_hash="old", created_at=datetime(2026, 1, 1))
    latest = create_listing_analysis(Session, listing_external_id="ext-one", profile="new", score=9.0, verdict="strong", input_hash="new", created_at=datetime(2026, 1, 4), risks_json={"flags": ["missing_area"]})
    _add_alert_sent(Session, listing_external_id="ext-one", dedupe_key="a")
    _add_alert_sent(Session, listing_external_id="ext-one", dedupe_key="b")
    _add_attempt(Session, listing_external_id="ext-one", dedupe_key="c", status="failed", created_at=datetime(2026, 1, 1))
    _add_attempt(Session, listing_external_id="ext-one", dedupe_key="d", status="success", created_at=datetime(2026, 1, 5))
    with Session() as s:
        review1 = HumanReview(listing_id=listing_id, listing_external_id="ext-one", listing_analysis_id=old, review_context_key="ctx1", review_status="new", created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
        review2 = HumanReview(listing_id=listing_id, listing_external_id="ext-one", listing_analysis_id=latest, review_context_key="ctx2", review_status="reviewed", human_verdict="interesting", outcome_status="watchlist", created_at=datetime(2026, 1, 2), updated_at=datetime(2026, 1, 6))
        s.add_all([review1, review2])
        s.flush()
        s.add(InvestmentDecision(human_review_id=review2.id, listing_external_id="ext-one", decision_type="watchlist", decision_status="approved", created_at=datetime(2026, 1, 7)))
        s.commit()

    rows = get_human_review_queue_rows(Session(), limit=10)
    by_ext = {row.external_id: row for row in rows}
    assert len([row for row in rows if row.external_id == "ext-one"]) == 1
    row = by_ext["ext-one"]
    assert row.analysis_id == latest
    assert row.analysis_profile == "new"
    assert row.alert_sent_count == 2
    assert row.latest_attempt_status == "success"
    assert row.human_review_count == 2
    assert row.latest_review_status == "reviewed"
    assert row.latest_decision_status == "approved"
    assert by_ext["ext-empty"].analysis_status is None
    assert by_ext["ext-empty"].alert_sent_count == 0

    page = client.get("/admin/review-queue", headers={"X-API-Key": "read"}).text
    assert page.count("ext-one") == 1
    assert "profile=new" in page
    assert "latest_attempt=success" in page
    assert "unknown" in page



def test_review_queue_profile_filter_requires_analysis_for_profile(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(Session, external_id="commercial", title="Commercial match")
    create_listing(Session, external_id="flat", title="Flat only")
    create_listing(Session, external_id="missing", title="No analysis")
    create_listing_analysis(
        Session,
        listing_external_id="commercial",
        profile="commercial_rent",
        score=8.0,
        verdict="strong",
        input_hash="commercial-rent",
        created_at=datetime(2026, 1, 3),
    )
    create_listing_analysis(
        Session,
        listing_external_id="flat",
        profile="flat_sale",
        score=9.0,
        verdict="strong",
        input_hash="flat-sale",
        created_at=datetime(2026, 1, 4),
    )

    filtered = get_human_review_queue_rows(Session(), profile="commercial_rent")
    assert [row.external_id for row in filtered] == ["commercial"]

    html = client.get("/admin/review-queue?profile=commercial_rent", headers={"X-API-Key": "read"}).text
    assert "commercial" in html
    assert "Commercial match" in html
    assert "flat" not in html
    assert "Flat only" not in html
    assert "missing" not in html
    assert "No analysis" not in html


def test_review_queue_without_profile_keeps_listings_without_analysis(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(Session, external_id="analyzed", title="Analyzed")
    create_listing(Session, external_id="no-analysis", title="No analysis")
    create_listing_analysis(
        Session,
        listing_external_id="analyzed",
        profile="commercial_rent",
        score=8.0,
        verdict="strong",
        input_hash="analyzed-commercial-rent",
        created_at=datetime(2026, 1, 3),
    )

    rows = get_human_review_queue_rows(Session(), profile=None)
    by_ext = {row.external_id: row for row in rows}
    assert set(by_ext) == {"analyzed", "no-analysis"}
    assert by_ext["no-analysis"].analysis_id is None
    assert by_ext["no-analysis"].analysis_status is None

    html = client.get("/admin/review-queue", headers={"X-API-Key": "read"}).text
    assert "analyzed" in html
    assert "no-analysis" in html
    assert "unknown" in html


def test_review_queue_filters_limit_and_deterministic_order(monkeypatch):
    _, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(Session, external_id="reviewed", last_seen_at=datetime(2026, 1, 4))
    create_listing(Session, external_id="top", last_seen_at=datetime(2026, 1, 3))
    create_listing(Session, external_id="low", last_seen_at=datetime(2026, 1, 2))
    create_listing_analysis(Session, listing_external_id="reviewed", score=99, verdict="strong", input_hash="reviewed")
    create_listing_analysis(Session, listing_external_id="top", score=10, verdict="strong", input_hash="top")
    create_listing_analysis(Session, listing_external_id="low", score=1, verdict="reject", input_hash="low")
    with Session() as s:
        s.add(HumanReview(listing_external_id="reviewed", review_context_key="reviewed", review_status="reviewed"))
        s.commit()

    rows = get_human_review_queue_rows(Session(), limit=999, unreviewed_only=False)
    assert len(rows) == 3
    assert [row.external_id for row in rows] == ["top", "low", "reviewed"]
    assert [row.external_id for row in get_human_review_queue_rows(Session(), limit=2)] == ["top", "low"]
    assert [row.external_id for row in get_human_review_queue_rows(Session(), unreviewed_only="true")] == ["top", "low"]


def test_review_queue_get_does_not_commit(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(Session, external_id="no-commit")
    calls = {"commit": 0}
    original_commit = Session.class_.commit

    def counted_commit(self):
        calls["commit"] += 1
        return original_commit(self)

    monkeypatch.setattr(Session.class_, "commit", counted_commit)
    assert client.get("/admin/review-queue", headers={"X-API-Key": "read"}).status_code == 200
    assert calls["commit"] == 0


def test_review_queue_does_not_mutate_source_tables(monkeypatch):
    client, Session = make_raw_client(monkeypatch, allow_query_api_key=False)
    create_listing(Session, external_id="stable")
    create_listing_analysis(Session, listing_external_id="stable")
    models = [Listing, ListingAnalysis, AlertSent, AlertDeliveryAttempt, HumanReview, InvestmentDecision, AdminAuditEvent]
    with Session() as s:
        before = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert client.get("/admin/review-queue", headers={"X-API-Key": "read"}).status_code == 200
    with Session() as s:
        after = {m.__tablename__: s.scalar(select(func.count()).select_from(m)) for m in models}
    assert after == before
