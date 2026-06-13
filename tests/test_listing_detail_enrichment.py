from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import func, select

from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.knowledge_note import KnowledgeNote
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_detail_snapshot import ListingDetailSnapshot
from app.models.listing_search_match import ListingSearchMatch
from app.models.search_job import SearchJob
from app.parsers.listing_detail_parser import compute_content_hash, parse_listing_detail_html
from app.services.listing_detail_enrichment import DetailEnrichmentResult, ListingDetailEnrichmentService

HTML = """
<html><head><meta property="og:title" content="fallback"></head><body>
<nav data-marker="breadcrumbs">Недвижимость / Коммерческая</nav>
<h1 data-marker="item-view/title-info">Помещение 42 м²</h1>
<div data-marker="item-view/item-price">10 000 000 ₽</div>
<div data-marker="item-view/item-address">Москва, Тверская улица</div>
<div data-marker="item-view/metro">м. Пушкинская</div>
<div data-marker="item-view/item-date">Размещено сегодня в 10:00</div>
<div data-marker="item-view/item-description">Видимое описание объекта.</div>
<ul data-marker="item-params"><li>Общая площадь: 42 м²</li><li>Этаж: 1</li><li>Телефон: +7 999 111 22 33</li></ul>
<div data-marker="seller-info/name">ООО Ромашка</div>
<div data-marker="phone-popup">+7 999 111 22 33</div>
<img itemprop="image" src="/1.jpg"><img itemprop="image" src="/2.jpg">
</body></html>
"""


def test_parser_extracts_bounded_public_fields_and_ignores_contact_attributes():
    parsed = parse_listing_detail_html(HTML, source_url="https://www.avito.ru/item?id=1&utm_source=x&context=abc")
    assert parsed.parse_status == "success"
    assert parsed.title == "Помещение 42 м²"
    assert parsed.price_text == "10 000 000 ₽"
    assert parsed.area_text == "42 м²"
    assert parsed.address_text == "Москва, Тверская улица"
    assert parsed.metro_text == "м. Пушкинская"
    assert parsed.published_label == "Размещено сегодня в 10:00"
    assert parsed.seller_name == "ООО Ромашка"
    assert parsed.seller_type == "agency"
    assert parsed.photos_count == 2
    assert parsed.attributes_json == {"Общая площадь": "42 м²", "Этаж": "1"}
    assert "phone" not in parsed.attributes_json
    assert "Телефон" not in parsed.attributes_json
    assert "utm_source" not in (parsed.canonical_url or "")
    assert "context" not in (parsed.canonical_url or "")


def test_parser_robustness_missing_malformed_and_long_text():
    partial = parse_listing_detail_html("<h1>Only title")
    assert partial.parse_status == "partial"
    failed = parse_listing_detail_html("<html><body></body></html>")
    assert failed.parse_status == "failed"
    long = parse_listing_detail_html(f"<h1>{'x' * 400}</h1><div itemprop='description'>{'y' * 12000}</div><div data-detail-attr='k'>v</div>")
    assert len(long.title) == 300
    assert len(long.description_text) == 10_000
    assert "title" in long.truncated_fields
    assert "description_text" in long.truncated_fields


def test_content_hash_is_deterministic_and_excludes_timestamps_and_volatile_url():
    first = parse_listing_detail_html(HTML, source_url="https://avito.ru/x?utm_source=a&keep=1")
    second = parse_listing_detail_html(HTML, source_url="https://avito.ru/x?utm_source=b&keep=1")
    first.parsed_at = datetime.utcnow() if hasattr(first, "parsed_at") else None
    second.parsed_at = datetime.utcnow() + timedelta(days=1) if hasattr(second, "parsed_at") else None
    assert first.content_hash == second.content_hash
    first.attributes_json = {"b": "2", "a": "1"}
    second.attributes_json = {"a": "1", "b": "2"}
    assert compute_content_hash(first) == compute_content_hash(second)
    second.title = "changed"
    assert compute_content_hash(first) != compute_content_hash(second)


def test_service_persists_idempotently_returns_result_and_no_side_effects(db_session):
    analysis = ListingAnalysis(listing_external_id="ext-2", input_hash="h", score=7.0, verdict="watch")
    search = SearchJob(name="s", source_url="https://avito.ru", filters_json={"a": 1})
    match = ListingSearchMatch(search_job_id=1, listing_external_id="ext-2")
    db_session.add_all([analysis, search, match])
    db_session.commit()

    service = ListingDetailEnrichmentService(db_session)
    with patch("app.agents.llm_providers.make_provider") as provider:
        result = service.persist_from_html(
            listing_external_id="ext-2",
            html=HTML,
            source_kind="fixture",
            listing_url="https://avito.ru/item?utm_campaign=x",
            source_url="https://avito.ru/item?utm_campaign=x",
        )
        duplicate = service.persist_from_html(listing_external_id="ext-2", html=HTML, source_kind="fixture")
    db_session.commit()

    assert isinstance(result, DetailEnrichmentResult)
    assert result.status == "created"
    assert duplicate.status == "existing"
    assert result.fetch_status == "not_applicable"
    assert result.parse_status == "success"
    assert result.parser_version == "listing-detail-v1"
    assert result.content_hash
    assert result.extracted_fields_count > 0
    snapshot = db_session.get(ListingDetailSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.source_kind == "fixture"
    assert "<html" not in snapshot.raw_text_excerpt.lower()
    assert db_session.scalar(select(func.count()).select_from(ListingDetailSnapshot)) == 1
    assert db_session.get(ListingAnalysis, analysis.id).score == 7.0
    assert db_session.get(ListingAnalysis, analysis.id).verdict == "watch"
    assert db_session.scalar(select(func.count()).select_from(AlertSent)) == 0
    assert db_session.scalar(select(func.count()).select_from(AgentTask)) == 0
    assert db_session.scalar(select(func.count()).select_from(KnowledgeNote)) == 0
    assert db_session.get(SearchJob, search.id).filters_json == {"a": 1}
    assert db_session.scalar(select(func.count()).select_from(ListingSearchMatch)) == 1
    provider.assert_not_called()


def test_service_redacts_contact_like_free_text_before_persistence(db_session):
    html = """
    <html><body>
    <h1>Safe title</h1>
    <div data-marker="item-view/item-description">
      Phone +7 900 123 45 67, email synthetic@example.test, Telegram @synthetic_handle.
    </div>
    <div data-marker="seller-info/name">Seller +7 900 123 45 67</div>
    </body></html>
    """
    result = ListingDetailEnrichmentService(db_session).persist_from_html(
        listing_external_id="ext-redact",
        html=html,
        source_kind="fixture",
    )
    db_session.commit()

    snapshot = db_session.get(ListingDetailSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert "[redacted_contact]" in snapshot.description_text
    for raw in ("+7 900 123 45 67", "synthetic@example.test", "@synthetic_handle"):
        assert raw not in snapshot.description_text
        assert raw not in snapshot.raw_text_excerpt
        assert raw not in snapshot.seller_name


def test_service_changed_content_creates_new_snapshot(db_session):
    service = ListingDetailEnrichmentService(db_session)
    one = service.persist_from_html(listing_external_id="ext-3", html=HTML, source_kind="fixture")
    two = service.persist_from_html(listing_external_id="ext-3", html=HTML.replace("42 м²", "43 м²"), source_kind="fixture")
    db_session.commit()
    assert one.snapshot_id != two.snapshot_id
    assert db_session.scalar(select(func.count()).select_from(ListingDetailSnapshot)) == 2


def test_runtime_boundaries_do_not_reference_detail_enrichment_service():
    for path in ("app/services/monitor_service.py", "app/workers/monitor.py", "app/analysis/service.py", "app/analysis/provider.py", "app/notifiers/telegram.py"):
        try:
            text = open(path, encoding="utf-8").read()
        except FileNotFoundError:
            continue
        assert "ListingDetailEnrichmentService" not in text
        assert "listing_detail" not in text
