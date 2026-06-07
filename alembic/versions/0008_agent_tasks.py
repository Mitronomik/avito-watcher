"""add agent tasks

Revision ID: 0008_agent_tasks
Revises: 0007_search_analysis_ctx
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_agent_tasks"
down_revision = "0007_search_analysis_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("listing_external_id", sa.String(length=128), nullable=True),
        sa.Column("listing_analysis_id", sa.Integer(), nullable=True),
        sa.Column("search_job_id", sa.Integer(), nullable=True),
        sa.Column("context_key", sa.String(length=160), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'canceled', 'skipped')",
            name="ck_agent_tasks_status",
        ),
    )
    op.create_index("ix_agent_tasks_task_type", "agent_tasks", ["task_type"])
    op.create_index("ix_agent_tasks_status", "agent_tasks", ["status"])
    op.create_index("ix_agent_tasks_priority", "agent_tasks", ["priority"])
    op.create_index("ix_agent_tasks_listing_external_id", "agent_tasks", ["listing_external_id"])
    op.create_index("ix_agent_tasks_listing_analysis_id", "agent_tasks", ["listing_analysis_id"])
    op.create_index("ix_agent_tasks_search_job_id", "agent_tasks", ["search_job_id"])
    op.create_index("ix_agent_tasks_context_key", "agent_tasks", ["context_key"])
    op.create_index("ix_agent_tasks_dedupe_key", "agent_tasks", ["dedupe_key"], unique=True)
    op.create_index("ix_agent_tasks_created_at", "agent_tasks", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_created_at", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_dedupe_key", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_context_key", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_search_job_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_listing_analysis_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_listing_external_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_priority", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_status", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_task_type", table_name="agent_tasks")
    op.drop_table("agent_tasks")
