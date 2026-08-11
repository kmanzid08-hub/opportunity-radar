"""add organizations and opportunity preferences

Revision ID: 20260810_02
Revises: 20260731_01
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_02"
down_revision = "20260731_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "country",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "city",
            sa.String(length=150),
            nullable=True,
        ),
        sa.Column(
            "website",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "industry",
            sa.String(length=150),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "default_language",
            sa.String(length=20),
            server_default="en",
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_organizations_id"),
        "organizations",
        ["id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_organizations_name"),
        "organizations",
        ["name"],
        unique=False,
    )

    op.create_index(
        op.f("ix_organizations_country"),
        "organizations",
        ["country"],
        unique=False,
    )

    op.create_index(
        op.f("ix_organizations_industry"),
        "organizations",
        ["industry"],
        unique=False,
    )

    op.create_index(
        op.f("ix_organizations_is_active"),
        "organizations",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "opportunity_preferences",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "countries",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "regions",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "industries",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "services",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "opportunity_types",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "keywords",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "languages",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "minimum_match_score",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "include_no_deadline",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "include_jobs",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "include_tenders",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "include_grants",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "include_partnerships",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_opportunity_preferences_id"),
        "opportunity_preferences",
        ["id"],
        unique=False,
    )

    op.create_index(
        op.f("ix_opportunity_preferences_organization_id"),
        "opportunity_preferences",
        ["organization_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_opportunity_preferences_organization_id"),
        table_name="opportunity_preferences",
    )

    op.drop_index(
        op.f("ix_opportunity_preferences_id"),
        table_name="opportunity_preferences",
    )

    op.drop_table(
        "opportunity_preferences"
    )

    op.drop_index(
        op.f("ix_organizations_is_active"),
        table_name="organizations",
    )

    op.drop_index(
        op.f("ix_organizations_industry"),
        table_name="organizations",
    )

    op.drop_index(
        op.f("ix_organizations_country"),
        table_name="organizations",
    )

    op.drop_index(
        op.f("ix_organizations_name"),
        table_name="organizations",
    )

    op.drop_index(
        op.f("ix_organizations_id"),
        table_name="organizations",
    )

    op.drop_table(
        "organizations"
    )
