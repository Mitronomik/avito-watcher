"""add alert delivery attempts

Revision ID: 0015_alert_delivery_attempts
Revises: 0014_human_review_tracking
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_alert_delivery_attempts"
down_revision = "0014_human_review_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_delivery_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("search_job_id", sa.Integer(), nullable=True),
        sa.Column("search_name", sa.String(length=255), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_alert_delivery_attempts_listing_external_id", "alert_delivery_attempts", ["listing_external_id"])
    op.create_index("ix_alert_delivery_attempts_channel", "alert_delivery_attempts", ["channel"])
    op.create_index("ix_alert_delivery_attempts_dedupe_key", "alert_delivery_attempts", ["dedupe_key"])
    op.create_index("ix_alert_delivery_attempts_payload_hash", "alert_delivery_attempts", ["payload_hash"])
    op.create_index("ix_alert_delivery_attempts_status", "alert_delivery_attempts", ["status"])
    op.create_index("ix_alert_delivery_attempts_created_at", "alert_delivery_attempts", ["created_at"])
    op.create_index("ix_alert_delivery_attempts_status_next_retry_at", "alert_delivery_attempts", ["status", "next_retry_at"])
    op.create_index("ix_alert_delivery_attempts_channel_status", "alert_delivery_attempts", ["channel", "status"])
    op.create_index("ix_alert_delivery_attempts_dedupe_key_channel", "alert_delivery_attempts", ["dedupe_key", "channel"])


def downgrade() -> None:
    op.drop_index("ix_alert_delivery_attempts_dedupe_key_channel", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_channel_status", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_status_next_retry_at", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_created_at", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_status", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_payload_hash", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_dedupe_key", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_channel", table_name="alert_delivery_attempts")
    op.drop_index("ix_alert_delivery_attempts_listing_external_id", table_name="alert_delivery_attempts")
    op.drop_table("alert_delivery_attempts")
