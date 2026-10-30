"""Invite-only beta ownership; existing companies remain unassigned and private."""

from alembic import op
import sqlalchemy as sa

revision = "20261030_12"
down_revision = "20261007_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
    )
    op.create_table(
        "beta_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text()),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("invite_hash", sa.String(64), unique=True),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_beta_users_workspace_id", "beta_users", ["workspace_id"])
    op.create_table(
        "beta_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("beta_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_beta_sessions_user_id", "beta_sessions", ["user_id"])
    op.create_table(
        "beta_usage",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspace_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="New"),
        sa.Column("is_lead", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_notes", sa.Text()),
        sa.Column("assigned_to", sa.String(255)),
        sa.Column("lead_priority", sa.String(20)),
        sa.Column("lead_status", sa.String(50)),
        sa.Column("next_action", sa.Text()),
        sa.Column("next_action_due", sa.Date()),
        sa.Column("last_follow_up_date", sa.Date()),
        sa.Column("lead_notes", sa.Text()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workspace_id", "opportunity_id", name="uq_workspace_opportunity"
        ),
    )
    op.create_index(
        "ix_workspace_opportunities_workspace_id",
        "workspace_opportunities",
        ["workspace_id"],
    )
    for table in ("organizations", "opportunities", "ai_opportunity_analysis"):
        # Batch mode supports adding a real FK on SQLite as well as PostgreSQL.
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("workspace_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_workspace", "workspaces", ["workspace_id"], ["id"]
            )
            batch.create_index(f"ix_{table}_workspace_id", ["workspace_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Private beta ownership is forward-only; restore a verified backup instead."
    )
