"""Separate authoritative notice URL and source role; geography uses analysis JSON."""

from alembic import op
import sqlalchemy as sa

revision = "20260930_10"
down_revision = "20260925_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "opportunities", sa.Column("original_source_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "sources",
        sa.Column(
            "source_role", sa.String(20), nullable=False, server_default="UNKNOWN"
        ),
    )


def downgrade() -> None:
    # Do not silently discard resolved provenance on rollback.
    raise RuntimeError(
        "Provenance migration is forward-only; downgrade would discard data"
    )
