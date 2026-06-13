"""add knowledge notes

Revision ID: 0010_knowledge_notes
Revises: 0009_alert_sent_created_at
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_knowledge_notes"
down_revision = "0009_alert_sent_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("note_type", sa.String(length=32), nullable=False),
        sa.Column("profile", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("source_ref", sa.String(length=500), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "note_type IN ('rulebook', 'false_positive', 'domain_note')",
            name="ck_knowledge_notes_note_type",
        ),
    )
    op.create_index("ix_knowledge_notes_note_type", "knowledge_notes", ["note_type"])
    op.create_index("ix_knowledge_notes_profile", "knowledge_notes", ["profile"])
    op.create_index("ix_knowledge_notes_is_active", "knowledge_notes", ["is_active"])
    op.create_index("ix_knowledge_notes_priority", "knowledge_notes", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_notes_priority", table_name="knowledge_notes")
    op.drop_index("ix_knowledge_notes_is_active", table_name="knowledge_notes")
    op.drop_index("ix_knowledge_notes_profile", table_name="knowledge_notes")
    op.drop_index("ix_knowledge_notes_note_type", table_name="knowledge_notes")
    op.drop_table("knowledge_notes")
