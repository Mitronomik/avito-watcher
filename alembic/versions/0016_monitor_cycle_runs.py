"""add monitor cycle runs ledger

Revision ID: 0016_monitor_cycle_runs
Revises: 0015_alert_delivery_attempts
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_monitor_cycle_runs"
down_revision = "0015_alert_delivery_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitor_cycle_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("searches_total", sa.Integer(), nullable=True),
        sa.Column("searches_processed", sa.Integer(), nullable=True),
        sa.Column("searches_failed", sa.Integer(), nullable=True),
        sa.Column("listings_seen", sa.Integer(), nullable=True),
        sa.Column("listings_created", sa.Integer(), nullable=True),
        sa.Column("listings_updated", sa.Integer(), nullable=True),
        sa.Column("analysis_attempted", sa.Integer(), nullable=True),
        sa.Column("analysis_succeeded", sa.Integer(), nullable=True),
        sa.Column("analysis_failed", sa.Integer(), nullable=True),
        sa.Column("alert_delivery_attempts_created", sa.Integer(), nullable=True),
        sa.Column("alerts_sent_created", sa.Integer(), nullable=True),
        sa.Column("alert_delivery_failed", sa.Integer(), nullable=True),
        sa.Column("alert_delivery_unknown", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("worker_status_file", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status in ('running', 'success', 'failed', 'partial', 'skipped', 'unknown')", name="ck_monitor_cycle_runs_status"),
    )
    op.create_index("ix_monitor_cycle_runs_started_at", "monitor_cycle_runs", ["started_at"])
    op.create_index("ix_monitor_cycle_runs_status_started_at", "monitor_cycle_runs", ["status", "started_at"])
    op.create_index("ix_monitor_cycle_runs_created_at", "monitor_cycle_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_monitor_cycle_runs_created_at", table_name="monitor_cycle_runs")
    op.drop_index("ix_monitor_cycle_runs_status_started_at", table_name="monitor_cycle_runs")
    op.drop_index("ix_monitor_cycle_runs_started_at", table_name="monitor_cycle_runs")
    op.drop_table("monitor_cycle_runs")
