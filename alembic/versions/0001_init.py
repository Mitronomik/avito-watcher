"""init

Revision ID: 0001_init
Revises: 
Create Date: 2026-05-16 21:50:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'search_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('source_url', sa.String(length=2048), nullable=False),
        sa.Column('filters_json', sa.JSON(), nullable=False),
        sa.Column('poll_interval_sec', sa.Integer(), nullable=False),
    )
    op.create_table(
        'listings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('external_id', sa.String(length=128), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('title', sa.String(length=1024), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('address', sa.String(length=1024), nullable=False),
        sa.Column('area_m2', sa.Float(), nullable=True),
        sa.Column('rooms', sa.String(length=64), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
    )
    op.create_index('ix_listings_external_id', 'listings', ['external_id'], unique=True)
    op.create_table(
        'listing_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('external_id', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=1024), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('payload_json', sa.JSON(), nullable=False),
        sa.Column('screenshot_path', sa.String(length=1024), nullable=False),
    )
    op.create_index('ix_listing_snapshots_external_id', 'listing_snapshots', ['external_id'], unique=False)
    op.create_table(
        'alerts_sent',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('listing_external_id', sa.String(length=128), nullable=False),
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('dedupe_key', sa.String(length=255), nullable=False),
    )
    op.create_index('ix_alerts_sent_listing_external_id', 'alerts_sent', ['listing_external_id'], unique=False)
    op.create_index('ix_alerts_sent_dedupe_key', 'alerts_sent', ['dedupe_key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_alerts_sent_dedupe_key', table_name='alerts_sent')
    op.drop_index('ix_alerts_sent_listing_external_id', table_name='alerts_sent')
    op.drop_table('alerts_sent')
    op.drop_index('ix_listing_snapshots_external_id', table_name='listing_snapshots')
    op.drop_table('listing_snapshots')
    op.drop_index('ix_listings_external_id', table_name='listings')
    op.drop_table('listings')
    op.drop_table('search_jobs')
