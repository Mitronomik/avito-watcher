"""seen fields and snapshot observed_at

Revision ID: 0002_seen_fields_and_snapshot_observed_at
Revises: 0001_init
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_seen_fields_and_snapshot_observed_at"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listings", sa.Column("first_seen_at", sa.DateTime(), nullable=True))
    op.add_column("listings", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.add_column("listing_snapshots", sa.Column("observed_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE listings SET first_seen_at = NOW() WHERE first_seen_at IS NULL")
    op.execute("UPDATE listings SET last_seen_at = NOW() WHERE last_seen_at IS NULL")
    op.execute("UPDATE listing_snapshots SET observed_at = NOW() WHERE observed_at IS NULL")

    op.alter_column("listings", "first_seen_at", nullable=False)
    op.alter_column("listings", "last_seen_at", nullable=False)
    op.alter_column("listing_snapshots", "observed_at", nullable=False)


def downgrade() -> None:
    op.drop_column("listing_snapshots", "observed_at")
    op.drop_column("listings", "last_seen_at")
    op.drop_column("listings", "first_seen_at")
