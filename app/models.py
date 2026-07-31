from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


OPPORTUNITY_STATUSES: tuple[str, ...] = (
    "New",
    "Seen",
    "Interested",
    "Applied",
    "Rejected",
    "Expired",
)

LEAD_STATUSES: tuple[str, ...] = (
    "Under Review",
    "Pursuing",
    "Submitted",
    "On Hold",
    "Awarded",
    "Lost",
    "Cancelled",
)

LEAD_PRIORITIES: tuple[str, ...] = (
    "Low",
    "Medium",
    "High",
    "Urgent",
)


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    organisation_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        index=True,
    )

    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    source_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    source_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )

    match_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    match_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="New",
        server_default="New",
        index=True,
    )

    is_expired: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    user_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_seen_at: Mapped[datetime] = mapped_column(
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

    is_lead: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    assigned_to: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    lead_status: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )

    lead_priority: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
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

    last_follow_up_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    lead_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    lead = relationship(
        "Lead",
        back_populates="opportunity",
        uselist=False,
    )

    proposal = relationship(
        "Proposal",
        back_populates="opportunity",
        uselist=False,
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    organisation_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    base_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    monitor_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )

    domain: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    source_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="Unknown",
        server_default="Unknown",
    )

    discovered_from: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    discovery_query: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default="0",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    is_approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_discovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_scan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_successful_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_opportunity_found_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_scan_duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    last_http_status: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    total_opportunities_found: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    scan_interval_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=12,
        server_default="12",
    )

    priority_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=50.0,
        server_default="50",
        index=True,
    )

    url_relevance_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default="0",
    )

    is_auto_disabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    disabled_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
