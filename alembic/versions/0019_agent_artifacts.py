"""add agent artifacts blackboard

Revision ID: 0019_agent_artifacts
Revises: 0018_agent_task_orch_meta
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_agent_artifacts"
down_revision = "0018_agent_task_orch_meta"
branch_labels = None
depends_on = None

ARTIFACT_TYPES = (
    "evidence_candidates",
    "normalized_evidence",
    "data_gap_report",
    "call_questions",
    "decision_wording",
    "claim_review",
    "report_draft",
    "offer_draft",
    "presentation_outline",
    "geo_context",
    "portfolio_memory_finding",
)
REDACTION_STATUSES = ("not_required", "redacted", "blocked", "unknown")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("listing_external_id", sa.String(length=128), nullable=True),
        sa.Column("listing_analysis_id", sa.Integer(), nullable=True),
        sa.Column("search_job_id", sa.Integer(), nullable=True),
        sa.Column("context_key", sa.String(length=160), nullable=True),
        sa.Column("source_task_id", sa.Integer(), sa.ForeignKey("agent_tasks.id"), nullable=True),
        sa.Column("orchestration_run_id", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_refs_json", sa.JSON(), nullable=False),
        sa.Column("redaction_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"artifact_type IN ({_in(ARTIFACT_TYPES)})", name="ck_agent_artifacts_artifact_type"),
        sa.CheckConstraint(f"redaction_status IN ({_in(REDACTION_STATUSES)})", name="ck_agent_artifacts_redaction_status"),
        sa.CheckConstraint("length(trim(input_hash)) > 0", name="ck_agent_artifacts_input_hash_not_empty"),
        sa.CheckConstraint("length(trim(content_hash)) > 0", name="ck_agent_artifacts_content_hash_not_empty"),
        sa.CheckConstraint("length(trim(schema_version)) > 0", name="ck_agent_artifacts_schema_version_not_empty"),
    )
    for name, cols in {
        "ix_agent_artifacts_artifact_type": ["artifact_type"],
        "ix_agent_artifacts_listing_external_id": ["listing_external_id"],
        "ix_agent_artifacts_listing_analysis_id": ["listing_analysis_id"],
        "ix_agent_artifacts_search_job_id": ["search_job_id"],
        "ix_agent_artifacts_context_key": ["context_key"],
        "ix_agent_artifacts_source_task_id": ["source_task_id"],
        "ix_agent_artifacts_orchestration_run_id": ["orchestration_run_id"],
        "ix_agent_artifacts_input_hash": ["input_hash"],
        "ix_agent_artifacts_content_hash": ["content_hash"],
        "ix_agent_artifacts_created_at": ["created_at"],
        "ix_agent_artifacts_context_latest": ["listing_external_id", "artifact_type", "context_key", "created_at"],
    }.items():
        op.create_index(name, "agent_artifacts", cols)


def downgrade() -> None:
    for name in (
        "ix_agent_artifacts_context_latest",
        "ix_agent_artifacts_created_at",
        "ix_agent_artifacts_content_hash",
        "ix_agent_artifacts_input_hash",
        "ix_agent_artifacts_orchestration_run_id",
        "ix_agent_artifacts_source_task_id",
        "ix_agent_artifacts_context_key",
        "ix_agent_artifacts_search_job_id",
        "ix_agent_artifacts_listing_analysis_id",
        "ix_agent_artifacts_listing_external_id",
        "ix_agent_artifacts_artifact_type",
    ):
        op.drop_index(name, table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
