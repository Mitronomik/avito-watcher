"""add listing publication fields

Revision ID: 0005_listing_published
Revises: 0004_search_active
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_listing_published"
down_revision = "0004_search_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("published_label", sa.String(length=255), server_default="", nullable=False),
    )
    op.add_column("listings", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.add_column(
        "listing_snapshots",
        sa.Column("published_label", sa.String(length=255), server_default="", nullable=False),
    )
    op.add_column("listing_snapshots", sa.Column("published_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_snapshots", "published_at")
    op.drop_column("listing_snapshots", "published_label")
    op.drop_column("listings", "published_at")
    op.drop_column("listings", "published_label")
