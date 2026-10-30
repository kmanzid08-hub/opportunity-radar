"""Stored, company-specific decision support; not a replacement for matching."""

from enum import Enum

from pydantic import Field

from app.intelligence.schemas import IntelligenceSchema
from app.intelligence.schemas import EvidenceStatus as EvidenceStatus


class Priority(str, Enum):
    URGENT = "URGENT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    DO_NOT_PURSUE = "DO_NOT_PURSUE"


class BidReadiness(str, Enum):
    READY = "READY"
    MOSTLY_READY = "MOSTLY_READY"
    NEEDS_WORK = "NEEDS_WORK"
    UNKNOWN = "UNKNOWN"
    LIKELY_INELIGIBLE = "LIKELY_INELIGIBLE"


class Urgency(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class EstimatedEffort(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class DocumentCategory(str, Enum):
    COMPANY = "COMPANY"
    FINANCIAL = "FINANCIAL"
    LEGAL_REGISTRATION = "LEGAL_REGISTRATION"
    PROFESSIONAL_CREDENTIALS = "PROFESSIONAL_CREDENTIALS"
    PERSONNEL = "PERSONNEL"
    TECHNICAL_PROPOSAL = "TECHNICAL_PROPOSAL"
    FINANCIAL_PROPOSAL = "FINANCIAL_PROPOSAL"
    EXPERIENCE_EVIDENCE = "EXPERIENCE_EVIDENCE"
    OTHER = "OTHER"


class RequiredDocument(IntelligenceSchema):
    name: str = Field(min_length=1)
    category: DocumentCategory


class TriageResult(IntelligenceSchema):
    priority: Priority
    priority_score: int = Field(ge=0, le=100)
    bid_readiness: BidReadiness = Field(
        description="Unknown hard eligibility requires UNKNOWN or NEEDS_WORK, never READY/MOSTLY_READY."
    )
    urgency: Urgency
    estimated_effort: EstimatedEffort
    key_reasons: list[str] = Field(min_length=1)
    critical_gaps: list[str]
    eligibility_risks: list[str]
    recommended_next_action: str = Field(min_length=1)
    budget_evidence: EvidenceStatus
    deadline_evidence: EvidenceStatus
    eligibility_evidence: EvidenceStatus = Field(
        description="Whether THIS company meets eligibility. Empty eligibility_requirements requires UNKNOWN."
    )
    experience_evidence: EvidenceStatus = Field(
        description="Satisfaction of stated experience requirements, not capability fit. No requirement or NOT_DOCUMENTED company evidence means UNKNOWN, never ABSENT or CONFIRMED."
    )
    exclusion_applies: bool | None
    required_documents: list[RequiredDocument]
    eligibility_statement: str = ""
    policy_adjustments: list[str] = Field(default_factory=list)
