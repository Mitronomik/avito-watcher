"""listing enrichments

Revision ID: 0012_listing_enrichments
Revises: 0011_listing_detail_snapshots
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_listing_enrichments"
down_revision = "0011_listing_detail_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_enrichments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=True),
        sa.Column("enrichment_type", sa.String(length=100), nullable=False),
        sa.Column("source_type", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("extraction_profile", sa.String(length=100), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "structured_facts_json", sa.JSON(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "field_confidence_json", sa.JSON(), nullable=False, server_default="{}"
        ),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "missing_fields_json", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "uncertain_fields_json", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "contradictions_json", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column("warnings_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "enrichment_type",
            "source_type",
            "source_id",
            "model",
            "prompt_version",
            "schema_version",
            "extraction_profile",
            "input_hash",
            name="uq_listing_enrichments_success_identity",
        ),
    )
    for col in [
        "listing_external_id",
        "listing_id",
        "enrichment_type",
        "source_type",
        "source_id",
        "status",
        "validation_status",
        "prompt_version",
        "schema_version",
        "extraction_profile",
        "input_hash",
        "source_content_hash",
        "output_hash",
    ]:
        op.create_index(f"ix_listing_enrichments_{col}", "listing_enrichments", [col])


def downgrade() -> None:
    for col in [
        "output_hash",
        "source_content_hash",
        "input_hash",
        "extraction_profile",
        "schema_version",
        "prompt_version",
        "validation_status",
        "status",
        "source_id",
        "source_type",
        "enrichment_type",
        "listing_id",
        "listing_external_id",
    ]:
        op.drop_index(f"ix_listing_enrichments_{col}", table_name="listing_enrichments")
    op.drop_table("listing_enrichments")
