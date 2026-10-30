"""add company opportunity matches

Revision ID: 20260916_06
Revises: 20260916_05
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260916_06"
down_revision = "20260916_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_opportunity_matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("match_strength", sa.String(length=20), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("potential_gaps", sa.JSON(), nullable=False),
        sa.Column("eligibility_concerns", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("minimum_score", sa.Integer(), nullable=False),
        sa.Column("qualifies_for_feed", sa.Boolean(), nullable=False),
        sa.Column("match_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column(
            "matched_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "opportunity_id",
            name="uq_company_opportunity_match",
        ),
    )
    op.create_index(
        op.f("ix_company_opportunity_matches_id"),
        "company_opportunity_matches",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_opportunity_matches_match_fingerprint"),
        "company_opportunity_matches",
        ["match_fingerprint"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_opportunity_matches_opportunity_id"),
        "company_opportunity_matches",
        ["opportunity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_opportunity_matches_organization_id"),
        "company_opportunity_matches",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_opportunity_matches_qualifies_for_feed"),
        "company_opportunity_matches",
        ["qualifies_for_feed"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_company_opportunity_matches_qualifies_for_feed"),
        table_name="company_opportunity_matches",
    )
    op.drop_index(
        op.f("ix_company_opportunity_matches_organization_id"),
        table_name="company_opportunity_matches",
    )
    op.drop_index(
        op.f("ix_company_opportunity_matches_opportunity_id"),
        table_name="company_opportunity_matches",
    )
    op.drop_index(
        op.f("ix_company_opportunity_matches_match_fingerprint"),
        table_name="company_opportunity_matches",
    )
    op.drop_index(
        op.f("ix_company_opportunity_matches_id"),
        table_name="company_opportunity_matches",
    )
    op.drop_table("company_opportunity_matches")
