"""make_last_error_nullable

Revision ID: f5e5657dcbf1
Revises: 0005_listing_published
Create Date: 2026-05-18

"""
from alembic import op
import sqlalchemy as sa

revision = 'f5e5657dcbf1'
down_revision = '0005_listing_published'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('search_jobs', 'last_error',
                    existing_type=sa.VARCHAR(),
                    nullable=True,
                    server_default=None)


def downgrade():
    op.execute("UPDATE search_jobs SET last_error = '' WHERE last_error IS NULL")
    op.alter_column('search_jobs', 'last_error',
                    existing_type=sa.VARCHAR(),
                    nullable=False)
