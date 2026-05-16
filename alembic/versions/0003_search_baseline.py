"""search baseline state

Revision ID: 0003_search_baseline
Revises: 0002_seen_fields
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_search_baseline"
down_revision = "0002_seen_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "search_jobs",
        sa.Column("baseline_initialized", sa.Boolean(), nullable=True),
    )
    op.add_column("search_jobs", sa.Column("baseline_initialized_at", sa.DateTime(), nullable=True))
    op.add_column("search_jobs", sa.Column("last_checked_at", sa.DateTime(), nullable=True))
    op.add_column("search_jobs", sa.Column("last_success_at", sa.DateTime(), nullable=True))
    op.add_column("search_jobs", sa.Column("last_error", sa.String(length=2048), nullable=True))
    op.add_column("search_jobs", sa.Column("fail_count", sa.Integer(), nullable=True))
    op.add_column("search_jobs", sa.Column("next_run_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE search_jobs SET baseline_initialized = false WHERE baseline_initialized IS NULL")
    op.execute("UPDATE search_jobs SET last_error = '' WHERE last_error IS NULL")
    op.execute("UPDATE search_jobs SET fail_count = 0 WHERE fail_count IS NULL")

    op.alter_column("search_jobs", "baseline_initialized", nullable=False)
    op.alter_column("search_jobs", "last_error", nullable=False)
    op.alter_column("search_jobs", "fail_count", nullable=False)


def downgrade() -> None:
    op.drop_column("search_jobs", "next_run_at")
    op.drop_column("search_jobs", "fail_count")
    op.drop_column("search_jobs", "last_error")
    op.drop_column("search_jobs", "last_success_at")
    op.drop_column("search_jobs", "last_checked_at")
    op.drop_column("search_jobs", "baseline_initialized_at")
    op.drop_column("search_jobs", "baseline_initialized")
