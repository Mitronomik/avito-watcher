"""add search-aware listing analysis context

Revision ID: 0007_search_analysis_ctx
Revises: 0006_listing_analyses
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_search_analysis_ctx"
down_revision = "0006_listing_analyses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_search_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("search_job_id", sa.Integer(), nullable=False),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "search_job_id",
            "listing_external_id",
            name="uq_listing_search_matches_search_listing",
        ),
    )
    op.create_index("ix_listing_search_matches_search_job_id", "listing_search_matches", ["search_job_id"])
    op.create_index("ix_listing_search_matches_listing_external_id", "listing_search_matches", ["listing_external_id"])
    op.create_index("ix_listing_search_matches_last_seen_at", "listing_search_matches", ["last_seen_at"])

    op.add_column("listing_analyses", sa.Column("search_job_id", sa.Integer(), nullable=True))
    op.add_column(
        "listing_analyses",
        sa.Column(
            "context_key",
            sa.String(length=160),
            nullable=False,
            server_default="global",
        ),
    )
    op.drop_constraint("uq_listing_analyses_input", "listing_analyses", type_="unique")
    op.create_unique_constraint(
        "uq_listing_analyses_input_context",
        "listing_analyses",
        [
            "listing_external_id",
            "profile",
            "analysis_version",
            "input_hash",
            "context_key",
        ],
    )
    op.create_index("ix_listing_analyses_search_job_id", "listing_analyses", ["search_job_id"])
    op.create_index("ix_listing_analyses_context_key", "listing_analyses", ["context_key"])


def downgrade() -> None:
    op.drop_index("ix_listing_analyses_context_key", table_name="listing_analyses")
    op.drop_index("ix_listing_analyses_search_job_id", table_name="listing_analyses")
    op.drop_constraint("uq_listing_analyses_input_context", "listing_analyses", type_="unique")
    op.create_unique_constraint(
        "uq_listing_analyses_input",
        "listing_analyses",
        ["listing_external_id", "profile", "analysis_version", "input_hash"],
    )
    op.drop_column("listing_analyses", "context_key")
    op.drop_column("listing_analyses", "search_job_id")

    op.drop_index("ix_listing_search_matches_last_seen_at", table_name="listing_search_matches")
    op.drop_index("ix_listing_search_matches_listing_external_id", table_name="listing_search_matches")
    op.drop_index("ix_listing_search_matches_search_job_id", table_name="listing_search_matches")
    op.drop_table("listing_search_matches")
