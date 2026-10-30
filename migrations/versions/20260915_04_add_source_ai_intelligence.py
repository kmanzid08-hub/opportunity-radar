"""add source AI intelligence

Revision ID: 20260915_04
Revises: 20260913_03
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20260915_04"
down_revision = "20260913_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("ai_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("ai_confidence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("ai_source_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("ai_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column(
            "ai_analyzed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_sources_source_fingerprint"),
        "sources",
        ["source_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sources_source_fingerprint"),
        table_name="sources",
    )
    op.drop_column("sources", "source_fingerprint")
    op.drop_column("sources", "ai_analyzed_at")
    op.drop_column("sources", "ai_reason")
    op.drop_column("sources", "ai_source_type")
    op.drop_column("sources", "ai_confidence")
    op.drop_column("sources", "ai_status")
