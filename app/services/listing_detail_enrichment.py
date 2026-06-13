from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.parsers.listing_detail_parser import parse_listing_detail_html
from app.repositories.listing_detail_snapshots import ListingDetailSnapshotRepository


@dataclass
class DetailEnrichmentResult:
    status: str
    fetch_status: str
    parse_status: str
    parser_version: str
    content_hash: str | None
    extracted_fields_count: int
    truncated_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    snapshot_id: int | None = None


class ListingDetailEnrichmentService:
    def __init__(self, db: Session) -> None:
        self.repository = ListingDetailSnapshotRepository(db)

    def persist_from_html(
        self,
        *,
        listing_external_id: str,
        html: str,
        source_kind: str,
        listing_id: int | None = None,
        listing_url: str | None = None,
        source_url: str | None = None,
        fetch_status: str = "not_applicable",
        fetched_at: datetime | None = None,
        parsed_at: datetime | None = None,
    ) -> DetailEnrichmentResult:
        parsed_at = parsed_at or datetime.now(UTC).replace(tzinfo=None)
        parsed = parse_listing_detail_html(html, source_url=source_url or listing_url)
        if parsed.content_hash is None:
            return DetailEnrichmentResult(
                status="failed",
                fetch_status=fetch_status,
                parse_status=parsed.parse_status,
                parser_version=parsed.parser_version,
                content_hash=None,
                extracted_fields_count=parsed.extracted_fields_count,
                truncated_fields=parsed.truncated_fields[:50],
                warnings=parsed.warnings[:50],
                error_type=parsed.error_type,
                error_message=parsed.error_message,
            )
        snapshot, created = self.repository.create_or_get_snapshot(
            listing_id=listing_id,
            listing_external_id=listing_external_id,
            listing_url=listing_url,
            source_url=source_url,
            canonical_url=parsed.canonical_url,
            source_host=parsed.source_host,
            source_kind=source_kind,
            fetch_status=fetch_status,
            parse_status=parsed.parse_status,
            fetched_at=fetched_at,
            parsed_at=parsed_at,
            parser_version=parsed.parser_version,
            content_hash=parsed.content_hash,
            title=parsed.title,
            description_text=parsed.description_text,
            address_text=parsed.address_text,
            metro_text=parsed.metro_text,
            price_text=parsed.price_text,
            area_text=parsed.area_text,
            published_label=parsed.published_label,
            published_at=parsed.published_at,
            seller_name=parsed.seller_name,
            seller_type=parsed.seller_type,
            category=parsed.category,
            attributes_json=parsed.attributes_json,
            facts_json=parsed.facts_json,
            photos_count=parsed.photos_count,
            raw_text_excerpt=parsed.raw_text_excerpt,
            extracted_fields_count=parsed.extracted_fields_count,
            truncated_fields_json=parsed.truncated_fields[:50],
            warnings_json=parsed.warnings[:50],
            error_type=parsed.error_type,
            error_message=parsed.error_message,
        )
        return DetailEnrichmentResult(
            status="created" if created else "existing",
            fetch_status=fetch_status,
            parse_status=parsed.parse_status,
            parser_version=parsed.parser_version,
            content_hash=parsed.content_hash,
            extracted_fields_count=parsed.extracted_fields_count,
            truncated_fields=parsed.truncated_fields[:50],
            warnings=parsed.warnings[:50],
            error_type=parsed.error_type,
            error_message=parsed.error_message,
            snapshot_id=snapshot.id,
        )
