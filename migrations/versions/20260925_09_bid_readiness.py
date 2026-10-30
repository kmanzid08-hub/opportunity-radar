"""Add evidence-bound company checklists and independently cached extraction."""

from alembic import op
import sqlalchemy as sa


revision = "20260925_09"
down_revision = "20260925_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunity_requirement_extractions",
        sa.Column("opportunity_id", sa.Integer(), primary_key=True),
        sa.Column("analysis_fingerprint", sa.String(64), nullable=False),
        sa.Column("extraction_fingerprint", sa.String(64), nullable=False),
        sa.Column("requirements_data", sa.JSON(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"
        ),
    )
    op.create_table(
        "company_opportunity_readiness",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("analysis_fingerprint", sa.String(64), nullable=False),
        sa.Column("extraction_fingerprint", sa.String(64), nullable=False),
        sa.Column("profile_fingerprint", sa.String(64), nullable=False),
        sa.Column("documents_fingerprint", sa.String(64), nullable=False),
        sa.Column("readiness_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_data", sa.JSON(), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "company_id", "opportunity_id", name="uq_company_opportunity_readiness"
        ),
    )
    op.create_index(
        "ix_company_opportunity_readiness_opportunity_id",
        "company_opportunity_readiness",
        ["opportunity_id"],
    )
    op.create_table(
        "opportunity_requirements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("requirement_key", sa.String(64), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("source_field", sa.String(40), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=True),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.Column("requires_document", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(25), nullable=False),
        sa.Column("availability", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "company_id",
            "opportunity_id",
            "requirement_key",
            name="uq_company_opportunity_requirement",
        ),
    )
    op.create_index(
        "ix_opportunity_requirements_opportunity_id",
        "opportunity_requirements",
        ["opportunity_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_opportunity_requirements_opportunity_id",
        table_name="opportunity_requirements",
    )
    op.drop_table("opportunity_requirements")
    op.drop_index(
        "ix_company_opportunity_readiness_opportunity_id",
        table_name="company_opportunity_readiness",
    )
    op.drop_table("company_opportunity_readiness")
    op.drop_table("opportunity_requirement_extractions")
