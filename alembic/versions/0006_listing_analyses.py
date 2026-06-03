"""add listing analyses

Revision ID: 0006_listing_analyses
Revises: f5e5657dcbf1
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_listing_analyses"
down_revision = "f5e5657dcbf1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=True),
        sa.Column("profile", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(length=64), nullable=True),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("risks_json", sa.JSON(), nullable=False),
        sa.Column("questions_json", sa.JSON(), nullable=False),
        sa.Column("report_md", sa.Text(), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'skipped', 'stale')",
            name="ck_listing_analyses_status",
        ),
        sa.UniqueConstraint(
            "listing_external_id",
            "profile",
            "analysis_version",
            "input_hash",
            name="uq_listing_analyses_input",
        ),
    )
    op.create_index("ix_listing_analyses_listing_external_id", "listing_analyses", ["listing_external_id"])
    op.create_index("ix_listing_analyses_snapshot_id", "listing_analyses", ["snapshot_id"])
    op.create_index("ix_listing_analyses_profile", "listing_analyses", ["profile"])
    op.create_index("ix_listing_analyses_status", "listing_analyses", ["status"])
    op.create_index("ix_listing_analyses_input_hash", "listing_analyses", ["input_hash"])
    op.create_index("ix_listing_analyses_created_at", "listing_analyses", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_listing_analyses_created_at", table_name="listing_analyses")
    op.drop_index("ix_listing_analyses_input_hash", table_name="listing_analyses")
    op.drop_index("ix_listing_analyses_status", table_name="listing_analyses")
    op.drop_index("ix_listing_analyses_profile", table_name="listing_analyses")
    op.drop_index("ix_listing_analyses_snapshot_id", table_name="listing_analyses")
    op.drop_index("ix_listing_analyses_listing_external_id", table_name="listing_analyses")
    op.drop_table("listing_analyses")
