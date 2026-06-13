"""add listing detail snapshots

Revision ID: 0011_listing_detail_snapshots
Revises: 0010_knowledge_notes
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_listing_detail_snapshots"
down_revision = "0010_knowledge_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_detail_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), nullable=True),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("listing_url", sa.String(length=2048), nullable=True),
        sa.Column("canonical_url", sa.String(length=2048), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("source_host", sa.String(length=255), nullable=True),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("fetch_status", sa.String(length=32), nullable=False, server_default="not_applicable"),
        sa.Column("parse_status", sa.String(length=32), nullable=False, server_default="skipped"),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=False, server_default="listing-detail-v1"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("description_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("address_text", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("metro_text", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("price_text", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("area_text", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("published_label", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("seller_name", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("seller_type", sa.String(length=100), nullable=False, server_default="unknown"),
        sa.Column("category", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("attributes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("facts_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("photos_count", sa.Integer(), nullable=True),
        sa.Column("raw_text_excerpt", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("extracted_fields_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("truncated_fields_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("listing_external_id", "content_hash", name="uq_listing_detail_snapshots_external_hash"),
    )
    for col in ("listing_id", "listing_external_id", "fetch_status", "parse_status", "fetched_at", "parsed_at", "content_hash", "source_kind"):
        op.create_index(f"ix_listing_detail_snapshots_{col}", "listing_detail_snapshots", [col])


def downgrade() -> None:
    for col in ("source_kind", "content_hash", "parsed_at", "fetched_at", "parse_status", "fetch_status", "listing_external_id", "listing_id"):
        op.drop_index(f"ix_listing_detail_snapshots_{col}", table_name="listing_detail_snapshots")
    op.drop_table("listing_detail_snapshots")
