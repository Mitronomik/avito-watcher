"""human review tracking

Revision ID: 0014_human_review_tracking
Revises: 0013_market_evidence_storage
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0014_human_review_tracking"
down_revision = "0013_market_evidence_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("listing_id", sa.Integer(), sa.ForeignKey("listings.id"), nullable=True),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("search_job_id", sa.Integer(), sa.ForeignKey("search_jobs.id"), nullable=True),
        sa.Column("listing_analysis_id", sa.Integer(), sa.ForeignKey("listing_analyses.id"), nullable=True),
        sa.Column("review_context_key", sa.String(length=320), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column("human_verdict", sa.String(length=64), nullable=True),
        sa.Column("next_action", sa.String(length=64), nullable=True),
        sa.Column("rejected_reason", sa.String(length=64), nullable=True),
        sa.Column("outcome_status", sa.String(length=64), nullable=True),
        sa.Column("watchlist", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("false_positive", sa.Boolean(), nullable=True),
        sa.Column("false_negative", sa.Boolean(), nullable=True),
        sa.Column("confirmed_purchase_price_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("confirmed_monthly_rent_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("confirmed_area_m2", sa.Numeric(12, 2), nullable=True),
        sa.Column("confirmed_opex_monthly_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("confirmed_opex_ratio", sa.Numeric(6, 5), nullable=True),
        sa.Column("confirmed_capex_initial_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("confirmed_vacancy_rate", sa.Numeric(6, 5), nullable=True),
        sa.Column("confirmed_source", sa.String(length=255), nullable=True),
        sa.Column("reviewer", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("review_context_key", name="uq_human_reviews_review_context_key"),
    )
    for col in ["listing_id", "listing_external_id", "search_job_id", "listing_analysis_id", "review_context_key", "review_status", "human_verdict", "outcome_status", "reviewed_at", "created_at", "watchlist", "false_positive", "false_negative"]:
        op.create_index(f"ix_human_reviews_{col}", "human_reviews", [col])
    op.create_index("ix_human_reviews_listing_external_id_context", "human_reviews", ["listing_external_id", "review_context_key"])

    op.create_table(
        "human_review_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("human_review_id", sa.Integer(), sa.ForeignKey("human_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    for col in ["human_review_id", "action_type", "actor", "created_at"]:
        op.create_index(f"ix_human_review_actions_{col}", "human_review_actions", [col])

    op.create_table(
        "investment_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("human_review_id", sa.Integer(), sa.ForeignKey("human_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_external_id", sa.String(length=128), nullable=False),
        sa.Column("decision_type", sa.String(length=64), nullable=False),
        sa.Column("decision_status", sa.String(length=64), nullable=False),
        sa.Column("decision_reason", sa.String(length=255), nullable=True),
        sa.Column("amount_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("expected_monthly_rent_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("actual_monthly_rent_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("actual_purchase_price_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )
    for col in ["human_review_id", "listing_external_id", "decision_type", "decision_status", "created_at", "decided_at"]:
        op.create_index(f"ix_investment_decisions_{col}", "investment_decisions", [col])


def downgrade() -> None:
    for col in ["decided_at", "created_at", "decision_status", "decision_type", "listing_external_id", "human_review_id"]:
        op.drop_index(f"ix_investment_decisions_{col}", table_name="investment_decisions")
    op.drop_table("investment_decisions")
    for col in ["created_at", "actor", "action_type", "human_review_id"]:
        op.drop_index(f"ix_human_review_actions_{col}", table_name="human_review_actions")
    op.drop_table("human_review_actions")
    op.drop_index("ix_human_reviews_listing_external_id_context", table_name="human_reviews")
    for col in ["false_negative", "false_positive", "watchlist", "created_at", "reviewed_at", "outcome_status", "human_verdict", "review_status", "review_context_key", "listing_analysis_id", "search_job_id", "listing_external_id", "listing_id"]:
        op.drop_index(f"ix_human_reviews_{col}", table_name="human_reviews")
    op.drop_table("human_reviews")
