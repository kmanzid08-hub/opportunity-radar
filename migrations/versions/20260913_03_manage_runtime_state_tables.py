"""manage runtime state tables

Revision ID: 20260913_03
Revises: 20260810_02
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260913_03"
down_revision = "20260810_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_job_state",
        sa.Column(
            "job_name",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "last_success_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("job_name"),
    )

    op.create_table(
        "ai_opportunity_verification",
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "decision",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "opportunity_type",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "verifier_model",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("opportunity_id"),
    )


def downgrade() -> None:
    op.drop_table("ai_opportunity_verification")
    op.drop_table("automation_job_state")
