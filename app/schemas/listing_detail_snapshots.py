from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DetailEnrichmentResultRead(BaseModel):
    status: str
    fetch_status: str
    parse_status: str
    parser_version: str
    content_hash: str | None = None
    extracted_fields_count: int
    truncated_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    snapshot_id: int | None = None


class ListingDetailSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int | None = None
    listing_external_id: str
    listing_url: str | None = None
    canonical_url: str | None = None
    source_url: str | None = None
    source_host: str | None = None
    source_kind: str
    fetch_status: str
    parse_status: str
    fetched_at: datetime | None = None
    parsed_at: datetime | None = None
    parser_version: str
    content_hash: str
    title: str
    description_text: str
    address_text: str
    metro_text: str
    price_text: str
    area_text: str
    published_label: str
    published_at: datetime | None = None
    seller_name: str
    seller_type: str
    category: str
    attributes_json: dict
    facts_json: dict
    photos_count: int | None = None
    raw_text_excerpt: str
    extracted_fields_count: int
    truncated_fields_json: list[str]
    warnings_json: list[str]
    error_type: str | None = None
    error_message: str | None = None
