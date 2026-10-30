"""Evidence-bound requirements and operational (not probabilistic) readiness."""

from enum import Enum
from hashlib import sha256
import json
from typing import Literal

from pydantic import Field

from app.intelligence.schemas import IntelligenceSchema, OpportunityAnalysis


class RequirementCategory(str, Enum):
    COMPANY_DOCUMENT = "COMPANY_DOCUMENT"
    LEGAL_REGISTRATION = "LEGAL_REGISTRATION"
    TAX = "TAX"
    FINANCIAL = "FINANCIAL"
    PROFESSIONAL_CREDENTIAL = "PROFESSIONAL_CREDENTIAL"
    PERSONNEL = "PERSONNEL"
    EXPERIENCE = "EXPERIENCE"
    TECHNICAL_PROPOSAL = "TECHNICAL_PROPOSAL"
    FINANCIAL_PROPOSAL = "FINANCIAL_PROPOSAL"
    METHODOLOGY = "METHODOLOGY"
    DECLARATION = "DECLARATION"
    OTHER = "OTHER"


class RequirementStatus(str, Enum):
    READY = "READY"
    MISSING = "MISSING"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Availability(str, Enum):
    KNOWN_AVAILABLE = "KNOWN_AVAILABLE"
    KNOWN_MISSING = "KNOWN_MISSING"
    UNKNOWN = "UNKNOWN"


class ReadinessStatus(str, Enum):
    READY = "READY"
    MOSTLY_READY = "MOSTLY_READY"
    NEEDS_WORK = "NEEDS_WORK"
    SIGNIFICANT_GAPS = "SIGNIFICANT_GAPS"
    UNKNOWN = "UNKNOWN"


LIST_REQUIREMENT_FIELDS = (
    "required_documents",
    "eligibility_requirements",
    "experience_requirements",
    "qualification_requirements",
)


class ExtractedRequirement(IntelligenceSchema):
    category: RequirementCategory
    requirement: str = Field(min_length=1)
    source_field: Literal[
        "required_documents",
        "eligibility_requirements",
        "experience_requirements",
        "qualification_requirements",
        "scope",
        "submission_method",
        "deliverables",
    ]
    evidence: str = Field(min_length=1)
    required: bool | None
    critical: bool
    requires_document: bool
    confidence: int = Field(ge=0, le=100)


class RequirementExtraction(IntelligenceSchema):
    requirements: list[ExtractedRequirement]


class RequirementAssessment(IntelligenceSchema):
    requirement_id: str
    status: RequirementStatus
    availability: Availability
    confidence: int = Field(ge=0, le=100)
    notes: str = Field(min_length=1, max_length=1000)
    next_action: str = Field(max_length=240)
    document_ids: list[str]


class ReadinessAnalysis(IntelligenceSchema):
    items: list[RequirementAssessment]


class ReadinessSummary(IntelligenceSchema):
    readiness_score: int | None = Field(ge=0, le=100)
    ready_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    confirmation_count: int = Field(ge=0)
    critical_gap_count: int = Field(ge=0)
    overall_status: ReadinessStatus
    next_actions: list[str]


def fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def requirement_key(requirement: ExtractedRequirement) -> str:
    return fingerprint([requirement.source_field, requirement.evidence])


def analysis_fingerprint(analysis: OpportunityAnalysis) -> str:
    return fingerprint(analysis.model_dump(mode="json"))


def validate_extraction(
    extraction: RequirementExtraction,
    analysis: OpportunityAnalysis,
) -> RequirementExtraction:
    if not isinstance(extraction, RequirementExtraction):
        raise ValueError("Invalid requirement extraction")
    extraction = RequirementExtraction.model_validate(extraction.model_dump())
    seen: set[tuple[str, str]] = set()
    for item in extraction.requirements:
        source = getattr(analysis, item.source_field)
        supported = (
            item.evidence in source
            if isinstance(source, list)
            else bool(source and item.evidence in source)
        )
        if not supported or item.requirement != item.evidence:
            raise ValueError("Requirement must preserve an exact supported quotation")
        pair = (item.source_field, item.evidence)
        if pair in seen:
            raise ValueError("Duplicate requirement")
        seen.add(pair)
        if item.source_field == "required_documents" and not item.requires_document:
            raise ValueError("An explicitly required document needs document evidence")
    expected = {
        (field, value)
        for field in LIST_REQUIREMENT_FIELDS
        for value in getattr(analysis, field)
    }
    if not expected.issubset(seen):
        raise ValueError("Extraction omitted an explicit requirement")
    return extraction
