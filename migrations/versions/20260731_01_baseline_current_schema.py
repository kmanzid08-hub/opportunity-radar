"""baseline current schema

Revision ID: 20260731_01
Revises:
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260731_01"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_name", sa.String(255), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("match_reason", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            server_default="New",
            nullable=False,
        ),
        sa.Column(
            "is_expired",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("user_notes", sa.Text(), nullable=True),
        sa.Column(
            "first_discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
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
        sa.Column(
            "is_lead",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("assigned_to", sa.String(255), nullable=True),
        sa.Column("lead_status", sa.String(50), nullable=True),
        sa.Column("lead_priority", sa.String(20), nullable=True),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("next_action_due", sa.Date(), nullable=True),
        sa.Column("last_follow_up_date", sa.Date(), nullable=True),
        sa.Column("lead_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_url"),
    )
    op.create_index("ix_opportunities_assigned_to", "opportunities", ["assigned_to"])
    op.create_index("ix_opportunities_category", "opportunities", ["category"])
    op.create_index("ix_opportunities_deadline", "opportunities", ["deadline"])
    op.create_index("ix_opportunities_id", "opportunities", ["id"])
    op.create_index("ix_opportunities_is_expired", "opportunities", ["is_expired"])
    op.create_index("ix_opportunities_is_lead", "opportunities", ["is_lead"])
    op.create_index("ix_opportunities_lead_priority", "opportunities", ["lead_priority"])
    op.create_index("ix_opportunities_lead_status", "opportunities", ["lead_status"])
    op.create_index("ix_opportunities_match_score", "opportunities", ["match_score"])
    op.create_index("ix_opportunities_next_action_due", "opportunities", ["next_action_due"])
    op.create_index("ix_opportunities_source_name", "opportunities", ["source_name"])
    op.create_index("ix_opportunities_status", "opportunities", ["status"])
    op.create_index("ix_opportunities_title", "opportunities", ["title"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_name", sa.String(255), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("monitor_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column(
            "source_type",
            sa.String(100),
            server_default="Unknown",
            nullable=False,
        ),
        sa.Column("discovered_from", sa.Text(), nullable=True),
        sa.Column("discovery_query", sa.Text(), nullable=True),
        sa.Column(
            "confidence_score",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "is_approved",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "first_discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_opportunity_found_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_duration_seconds", sa.Float(), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_opportunities_found",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "scan_interval_hours",
            sa.Integer(),
            server_default=sa.text("12"),
            nullable=False,
        ),
        sa.Column(
            "priority_score",
            sa.Float(),
            server_default=sa.text("50"),
            nullable=False,
        ),
        sa.Column(
            "url_relevance_score",
            sa.Float(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "is_auto_disabled",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("disabled_reason", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("monitor_url"),
    )
    op.create_index("ix_sources_domain", "sources", ["domain"])
    op.create_index("ix_sources_id", "sources", ["id"])
    op.create_index("ix_sources_is_active", "sources", ["is_active"])
    op.create_index("ix_sources_is_approved", "sources", ["is_approved"])
    op.create_index("ix_sources_is_auto_disabled", "sources", ["is_auto_disabled"])
    op.create_index("ix_sources_priority_score", "sources", ["priority_score"])

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            server_default="Under Review",
            nullable=False,
        ),
        sa.Column("assigned_to", sa.String(255), nullable=True),
        sa.Column(
            "priority",
            sa.String(20),
            server_default="Medium",
            nullable=False,
        ),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("next_action_due", sa.Date(), nullable=True),
        sa.Column("last_follow_up_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leads_assigned_to", "leads", ["assigned_to"])
    op.create_index("ix_leads_id", "leads", ["id"])
    op.create_index("ix_leads_next_action_due", "leads", ["next_action_due"])
    op.create_index("ix_leads_opportunity_id", "leads", ["opportunity_id"], unique=True)
    op.create_index("ix_leads_priority", "leads", ["priority"])
    op.create_index("ix_leads_status", "leads", ["status"])

    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=False),
        sa.Column(
            "bid_status",
            sa.String(50),
            server_default="Not Started",
            nullable=False,
        ),
        sa.Column("proposal_manager", sa.String(255), nullable=True),
        sa.Column("estimated_contract_value", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "currency",
            sa.String(10),
            server_default="RWF",
            nullable=False,
        ),
        sa.Column("estimated_effort_days", sa.Integer(), nullable=True),
        sa.Column(
            "bid_security_required",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("bid_security_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("proposal_start_date", sa.Date(), nullable=True),
        sa.Column("planned_submission_date", sa.Date(), nullable=True),
        sa.Column("actual_submission_date", sa.Date(), nullable=True),
        sa.Column(
            "proposal_submitted",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "proposal_result",
            sa.String(50),
            server_default="Pending",
            nullable=False,
        ),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("next_action_due", sa.Date(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("no_go_reason", sa.Text(), nullable=True),
        sa.Column("loss_reason", sa.Text(), nullable=True),
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
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_proposals_bid_status", "proposals", ["bid_status"])
    op.create_index("ix_proposals_id", "proposals", ["id"])
    op.create_index("ix_proposals_next_action_due", "proposals", ["next_action_due"])
    op.create_index("ix_proposals_opportunity_id", "proposals", ["opportunity_id"], unique=True)
    op.create_index("ix_proposals_planned_submission_date", "proposals", ["planned_submission_date"])
    op.create_index("ix_proposals_proposal_result", "proposals", ["proposal_result"])


def downgrade() -> None:
    op.drop_table("proposals")
    op.drop_table("leads")
    op.drop_table("sources")
    op.drop_table("opportunities")
