"""Store independent per-company opportunity triage; no runtime schema writes."""

from alembic import op
import sqlalchemy as sa


revision = "20260925_08"
down_revision = "20260924_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_opportunity_triage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("triage_fingerprint", sa.String(64), nullable=False),
        sa.Column("match_fingerprint", sa.String(64), nullable=False),
        sa.Column("effective_deadline", sa.Date(), nullable=True),
        sa.Column("effective_status", sa.String(30), nullable=False),
        sa.Column("assessment_date", sa.Date(), nullable=False),
        sa.Column("result_data", sa.JSON(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "organization_id", "opportunity_id", name="uq_company_opportunity_triage"
        ),
    )
    op.create_index(
        "ix_company_opportunity_triage_opportunity_id",
        "company_opportunity_triage",
        ["opportunity_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_opportunity_triage_opportunity_id",
        table_name="company_opportunity_triage",
    )
    op.drop_table("company_opportunity_triage")
