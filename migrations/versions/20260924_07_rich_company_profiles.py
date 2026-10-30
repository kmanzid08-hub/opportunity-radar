"""Add structured company intelligence while preserving existing profiles."""

from alembic import op
import sqlalchemy as sa


revision = "20260924_07"
down_revision = "20260916_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opportunity_preferences",
        sa.Column("intelligence", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("opportunity_preferences", "intelligence")
