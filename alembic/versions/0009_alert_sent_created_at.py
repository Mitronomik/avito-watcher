"""add created_at to alerts_sent

Revision ID: 0009_alert_sent_created_at
Revises: 0008_agent_tasks
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_alert_sent_created_at"
down_revision = "0008_agent_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts_sent", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE alerts_sent SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    with op.batch_alter_table("alerts_sent") as batch_op:
        batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)


def downgrade() -> None:
    op.drop_column("alerts_sent", "created_at")
