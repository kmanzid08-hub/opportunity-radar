"""add opportunity AI analysis

Revision ID: 20260916_05
Revises: 20260915_04
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260916_05"
down_revision = "20260915_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_opportunity_analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("raw_title", sa.String(length=500), nullable=False),
        sa.Column("source_name", sa.String(length=100), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "opportunity_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("decision", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("opportunity_type", sa.String(length=50), nullable=True),
        sa.Column("opportunity_status", sa.String(length=20), nullable=True),
        sa.Column("analysis_data", sa.JSON(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url"),
    )
    op.create_index(
        op.f("ix_ai_opportunity_analysis_content_fingerprint"),
        "ai_opportunity_analysis",
        ["content_fingerprint"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_opportunity_analysis_id"),
        "ai_opportunity_analysis",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_opportunity_analysis_opportunity_fingerprint"),
        "ai_opportunity_analysis",
        ["opportunity_fingerprint"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ai_opportunity_analysis_opportunity_id"),
        "ai_opportunity_analysis",
        ["opportunity_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ai_opportunity_analysis_opportunity_id"),
        table_name="ai_opportunity_analysis",
    )
    op.drop_index(
        op.f("ix_ai_opportunity_analysis_opportunity_fingerprint"),
        table_name="ai_opportunity_analysis",
    )
    op.drop_index(
        op.f("ix_ai_opportunity_analysis_id"),
        table_name="ai_opportunity_analysis",
    )
    op.drop_index(
        op.f("ix_ai_opportunity_analysis_content_fingerprint"),
        table_name="ai_opportunity_analysis",
    )
    op.drop_table("ai_opportunity_analysis")
