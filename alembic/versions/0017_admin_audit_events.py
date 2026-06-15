"""admin audit events

Revision ID: 0017_admin_audit_events
Revises: 0016_monitor_cycle_runs
Create Date: 2026-06-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0017_admin_audit_events"
down_revision = "0016_monitor_cycle_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("actor_kind", sa.String(length=64), nullable=False),
        sa.Column("actor_label", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=128), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_method", sa.String(length=16), nullable=True),
        sa.Column("request_path", sa.String(length=255), nullable=True),
        sa.Column("ip_hash", sa.String(length=128), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB().with_variant(sa.JSON(), "sqlite"), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_admin_audit_events_created_at", "admin_audit_events", ["created_at"])
    op.create_index("ix_admin_audit_events_action", "admin_audit_events", ["action"])
    op.create_index("ix_admin_audit_events_status", "admin_audit_events", ["status"])
    op.create_index("ix_admin_audit_events_target", "admin_audit_events", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_audit_events_target", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_status", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_action", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_events_created_at", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
