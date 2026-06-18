"""add agent task orchestration metadata

Revision ID: 0018_agent_task_orchestration_metadata
Revises: 0017_admin_audit_events
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_agent_task_orchestration_metadata"
down_revision = "0017_admin_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("orchestration_run_id", sa.String(length=128), nullable=True))
    op.add_column("agent_tasks", sa.Column("workflow_id", sa.String(length=128), nullable=True))
    op.add_column("agent_tasks", sa.Column("parent_task_id", sa.Integer(), nullable=True))
    op.add_column("agent_tasks", sa.Column("depends_on_task_id", sa.Integer(), nullable=True))
    op.add_column("agent_tasks", sa.Column("chain_depth", sa.Integer(), nullable=True))
    op.add_column("agent_tasks", sa.Column("blocking", sa.Boolean(), nullable=True))
    op.add_column("agent_tasks", sa.Column("dependency_status", sa.String(length=32), nullable=True))
    op.add_column("agent_tasks", sa.Column("orchestration_status", sa.String(length=32), nullable=True))
    op.create_foreign_key("fk_agent_tasks_parent_task_id", "agent_tasks", "agent_tasks", ["parent_task_id"], ["id"])
    op.create_foreign_key("fk_agent_tasks_depends_on_task_id", "agent_tasks", "agent_tasks", ["depends_on_task_id"], ["id"])
    op.create_index("ix_agent_tasks_orchestration_run_id", "agent_tasks", ["orchestration_run_id"])
    op.create_index("ix_agent_tasks_workflow_id", "agent_tasks", ["workflow_id"])
    op.create_index("ix_agent_tasks_parent_task_id", "agent_tasks", ["parent_task_id"])
    op.create_index("ix_agent_tasks_depends_on_task_id", "agent_tasks", ["depends_on_task_id"])
    op.create_index("ix_agent_tasks_orchestration_status", "agent_tasks", ["orchestration_status"])
    op.create_index("ix_agent_tasks_dependency_status", "agent_tasks", ["dependency_status"])


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_dependency_status", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_orchestration_status", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_depends_on_task_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_parent_task_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_workflow_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_orchestration_run_id", table_name="agent_tasks")
    op.drop_constraint("fk_agent_tasks_depends_on_task_id", "agent_tasks", type_="foreignkey")
    op.drop_constraint("fk_agent_tasks_parent_task_id", "agent_tasks", type_="foreignkey")
    op.drop_column("agent_tasks", "orchestration_status")
    op.drop_column("agent_tasks", "dependency_status")
    op.drop_column("agent_tasks", "blocking")
    op.drop_column("agent_tasks", "chain_depth")
    op.drop_column("agent_tasks", "depends_on_task_id")
    op.drop_column("agent_tasks", "parent_task_id")
    op.drop_column("agent_tasks", "workflow_id")
    op.drop_column("agent_tasks", "orchestration_run_id")
