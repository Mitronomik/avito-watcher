"""market evidence storage

Revision ID: 0013_market_evidence_storage
Revises: 0012_listing_enrichments
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_market_evidence_storage"
down_revision = "0012_listing_enrichments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_research_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_task_id", sa.Integer(), nullable=False),
        sa.Column("listing_external_id", sa.String(length=128), nullable=True),
        sa.Column("listing_analysis_id", sa.Integer(), nullable=True),
        sa.Column(
            "research_profile",
            sa.String(length=128),
            nullable=False,
            server_default="default",
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("query_plan_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sources_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("limitations_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "checked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_market_research_runs_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'partial', 'failed', 'skipped', 'invalid')",
            name="ck_market_research_runs_status",
        ),
        sa.UniqueConstraint(
            "agent_task_id", name="uq_market_research_runs_agent_task_id"
        ),
    )
    for col in [
        "agent_task_id",
        "listing_external_id",
        "listing_analysis_id",
        "research_profile",
        "checked_at",
        "expires_at",
        "confidence",
        "status",
    ]:
        op.create_index(f"ix_market_research_runs_{col}", "market_research_runs", [col])

    op.create_table(
        "market_evidence_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("market_research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("listing_external_id", sa.String(length=128), nullable=True),
        sa.Column("listing_analysis_id", sa.Integer(), nullable=True),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column(
            "research_profile",
            sa.String(length=128),
            nullable=False,
            server_default="default",
        ),
        sa.Column("asset_type", sa.String(length=32), nullable=True),
        sa.Column("deal_type", sa.String(length=32), nullable=True),
        sa.Column("location_text", sa.Text(), nullable=True),
        sa.Column("location_key", sa.String(length=300), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("claim", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("area_m2", sa.Float(), nullable=True),
        sa.Column("price_rub", sa.Float(), nullable=True),
        sa.Column("rent_rub_per_month", sa.Float(), nullable=True),
        sa.Column("price_per_m2_rub", sa.Float(), nullable=True),
        sa.Column("rent_per_m2_rub", sa.Float(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_url_normalized", sa.Text(), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("source_publisher", sa.Text(), nullable=True),
        sa.Column("source_published_at", sa.String(length=80), nullable=True),
        sa.Column(
            "source_indexes_json", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "is_reusable", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("reuse_block_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "checked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_market_evidence_items_confidence",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('comparable_candidate', 'finding', 'assumption_to_verify', 'risk', 'opportunity')",
            name="ck_market_evidence_items_type",
        ),
        sa.UniqueConstraint(
            "run_id",
            "evidence_type",
            "content_hash",
            name="uq_market_evidence_items_run_type_hash",
        ),
    )
    for col in [
        "run_id",
        "listing_external_id",
        "listing_analysis_id",
        "research_profile",
        "evidence_type",
        "asset_type",
        "deal_type",
        "location_key",
        "source_url_normalized",
        "checked_at",
        "expires_at",
        "confidence",
        "is_reusable",
        "content_hash",
    ]:
        op.create_index(
            f"ix_market_evidence_items_{col}", "market_evidence_items", [col]
        )


def downgrade() -> None:
    for col in [
        "content_hash",
        "is_reusable",
        "confidence",
        "expires_at",
        "checked_at",
        "source_url_normalized",
        "location_key",
        "deal_type",
        "asset_type",
        "evidence_type",
        "research_profile",
        "listing_analysis_id",
        "listing_external_id",
        "run_id",
    ]:
        op.drop_index(
            f"ix_market_evidence_items_{col}", table_name="market_evidence_items"
        )
    op.drop_table("market_evidence_items")
    for col in [
        "status",
        "confidence",
        "expires_at",
        "checked_at",
        "research_profile",
        "listing_analysis_id",
        "listing_external_id",
        "agent_task_id",
    ]:
        op.drop_index(
            f"ix_market_research_runs_{col}", table_name="market_research_runs"
        )
    op.drop_table("market_research_runs")
