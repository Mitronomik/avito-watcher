"""add search active flag

Revision ID: 0004_search_active
Revises: 0003_search_baseline
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_search_active"
down_revision = "0003_search_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_jobs",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("search_jobs", "is_active")
