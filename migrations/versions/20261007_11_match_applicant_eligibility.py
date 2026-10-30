"""Persist applicant eligibility separately from semantic capability fit."""

from alembic import op
import sqlalchemy as sa

revision = "20261007_11"
down_revision = "20260930_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy matches remain unknown, not retrospectively confirmed. Alembic's
    # revision tracking provides repeat-upgrade safety; do not hide schema drift.
    op.add_column(
        "company_opportunity_matches",
        sa.Column("applicant_eligibility", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Eligibility migration is forward-only; downgrade would discard assessments"
    )
