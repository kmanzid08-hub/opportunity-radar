from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


PROPOSAL_STATUSES: tuple[str, ...] = (
    "Not Started",
    "Initial Review",
    "Go",
    "No-Go",
    "Proposal Preparation",
    "Internal Review",
    "Submitted",
    "Clarification",
    "Negotiation",
    "Awarded",
    "Lost",
    "Contract Signed",
)


PROPOSAL_RESULTS: tuple[str, ...] = (
    "Pending",
    "Awarded",
    "Lost",
    "Cancelled",
)


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    bid_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Not Started",
        server_default="Not Started",
        index=True,
    )

    proposal_manager: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    estimated_contract_value: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="RWF",
        server_default="RWF",
    )

    estimated_effort_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    bid_security_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    bid_security_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )

    proposal_start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    planned_submission_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    actual_submission_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    proposal_submitted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    proposal_result: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Pending",
        server_default="Pending",
        index=True,
    )

    next_action: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    next_action_due: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    internal_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    no_go_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    loss_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    opportunity = relationship(
        "Opportunity",
        back_populates="proposal",
    )