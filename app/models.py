from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
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


class Organization(Base):
    __tablename__ = "organizations"

    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)


    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    country: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    city: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    website: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    industry: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    default_language: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="en",
        server_default="en",
    )

    timezone: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
        index=True,
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

    opportunity_preferences: Mapped[
        OpportunityPreference | None
    ] = relationship(
        "OpportunityPreference",
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan",
    )


class OpportunityPreference(Base):
    __tablename__ = "opportunity_preferences"

    intelligence: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
        index=True,
    )

    countries: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    regions: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    industries: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    services: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    opportunity_types: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    keywords: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    languages: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    minimum_match_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    include_no_deadline: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    include_jobs: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    include_tenders: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    include_grants: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    include_partnerships: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
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

    organization: Mapped[Organization] = relationship(
        "Organization",
        back_populates="opportunity_preferences",
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)


    original_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def discovered_from_url(self) -> str:
        return self.source_url

    @property
    def canonical_url(self) -> str:
        return self.original_source_url or self.source_url

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
        server_default=false(),
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
        server_default=false(),
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

    source_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="UNKNOWN", server_default="UNKNOWN",
    )

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
        server_default=true(),
        index=True,
    )

    is_approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
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
        server_default=false(),
        index=True,
    )

    disabled_reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    ai_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    ai_confidence: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    ai_source_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    ai_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    ai_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    source_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )


class AutomationJobState(Base):
    __tablename__ = "automation_job_state"

    job_name: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
    )

    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AIOpportunityVerification(Base):
    __tablename__ = "ai_opportunity_verification"

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )

    decision: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    confidence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    opportunity_type: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    verifier_model: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class AIOpportunityAnalysis(Base):
    __tablename__ = "ai_opportunity_analysis"

    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)


    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    source_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )

    raw_title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    source_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    content_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    opportunity_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )

    decision: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    confidence: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    opportunity_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    opportunity_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    analysis_data: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    error_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class OpportunityRequirementExtraction(Base):
    __tablename__ = "opportunity_requirement_extractions"

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True,
    )
    analysis_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    requirements_data: Mapped[dict | None] = mapped_column(JSON)
    error_reason: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyOpportunityReadiness(Base):
    __tablename__ = "company_opportunity_readiness"
    __table_args__ = (
        UniqueConstraint("company_id", "opportunity_id", name="uq_company_opportunity_readiness"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True)
    analysis_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    documents_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_data: Mapped[dict | None] = mapped_column(JSON)
    error_reason: Mapped[str | None] = mapped_column(Text)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpportunityRequirement(Base):
    __tablename__ = "opportunity_requirements"
    __table_args__ = (
        UniqueConstraint("company_id", "opportunity_id", "requirement_key", name="uq_company_opportunity_requirement"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    requirement_key: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    source_field: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[bool | None] = mapped_column(Boolean)
    critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requires_document: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(25), nullable=False)
    availability: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    document_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class CompanyOpportunityTriage(Base):
    __tablename__ = "company_opportunity_triage"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "opportunity_id", name="uq_company_opportunity_triage"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    triage_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    match_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_deadline: Mapped[date | None] = mapped_column(Date)
    effective_status: Mapped[str] = mapped_column(String(30), nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    result_data: Mapped[dict | None] = mapped_column(JSON)
    error_reason: Mapped[str | None] = mapped_column(Text)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompanyOpportunityMatch(Base):
    __tablename__ = "company_opportunity_matches"
    applicant_eligibility: Mapped[str | None] = mapped_column(String(20), nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "opportunity_id",
            name="uq_company_opportunity_match",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey(
            "opportunities.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    match_strength: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    explanation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    strengths: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    potential_gaps: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    eligibility_concerns: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    recommendation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    minimum_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    qualifies_for_feed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    match_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    error_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


# Register relationship targets whenever the primary model module is loaded.
from app.lead_models import Lead as Lead  # noqa: E402
from app.proposal_models import Proposal as Proposal  # noqa: E402

from app.beta_models import Workspace as Workspace  # noqa: E402
