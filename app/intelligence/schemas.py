from __future__ import annotations

from datetime import date
from enum import Enum
import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.intelligence.countries import normalize_countries


class AnalysisDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REVIEW = "REVIEW"


class MatchStrength(str, Enum):
    VERY_STRONG = "VERY_STRONG"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    NOT_A_MATCH = "NOT_A_MATCH"


class SourceRole(str, Enum):
    AUTHORITATIVE = "AUTHORITATIVE"
    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    REPOST = "REPOST"
    UNKNOWN = "UNKNOWN"


class GeographicScope(str, Enum):
    SINGLE_COUNTRY = "SINGLE_COUNTRY"
    MULTI_COUNTRY = "MULTI_COUNTRY"
    REGIONAL = "REGIONAL"
    GLOBAL = "GLOBAL"
    REMOTE = "REMOTE"
    UNKNOWN = "UNKNOWN"


class OpportunityType(str, Enum):
    PROCUREMENT = "PROCUREMENT"
    TENDER = "TENDER"
    RFP = "RFP"
    RFQ = "RFQ"
    EOI = "EOI"
    CONSULTANCY = "CONSULTANCY"
    GRANT = "GRANT"
    CALL_FOR_PROPOSALS = "CALL_FOR_PROPOSALS"
    PROFESSIONAL_SERVICE = "PROFESSIONAL_SERVICE"
    FRAMEWORK = "FRAMEWORK"
    OTHER_OPPORTUNITY = "OTHER_OPPORTUNITY"
    JOB_VACANCY = "JOB_VACANCY"
    ARTICLE = "ARTICLE"
    NEWS = "NEWS"
    TRAINING = "TRAINING"
    EVENT = "EVENT"
    DIRECTORY = "DIRECTORY"
    MARKETING = "MARKETING"
    AWARD_NOTICE = "AWARD_NOTICE"
    CLOSED_OPPORTUNITY = "CLOSED_OPPORTUNITY"
    INFORMATION_PAGE = "INFORMATION_PAGE"
    UNKNOWN = "UNKNOWN"


class OpportunityStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    UPCOMING = "UPCOMING"
    UNKNOWN = "UNKNOWN"


class IntelligenceSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class SourceAnalysis(IntelligenceSchema):
    decision: AnalysisDecision
    confidence: int = Field(ge=0, le=100)
    source_name: str | None = None
    organisation: str | None = None
    country: str | None = None
    source_type: str | None = None
    source_role: SourceRole = SourceRole.UNKNOWN
    is_official: bool | None = None
    publishes_opportunities: bool | None = None
    is_recruitment_portal: bool | None = None
    is_paid_aggregator: bool | None = None
    is_directory: bool | None = None
    is_marketing_site: bool | None = None
    reason: str


class ApplicantType(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    CONSULTING_FIRM = "CONSULTING_FIRM"
    JOINT_VENTURE = "JOINT_VENTURE"
    NGO = "NGO"
    OTHER_ORGANIZATION = "OTHER_ORGANIZATION"


class EligibilityStatus(str, Enum):
    UNSPECIFIED = "UNSPECIFIED"
    RESTRICTED = "RESTRICTED"
    UNRESTRICTED = "UNRESTRICTED"


class EvidenceStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class EligibilityEvidenceStrength(str, Enum):
    DIRECT = "DIRECT"
    SUPPORTING = "SUPPORTING"
    AMBIGUOUS = "AMBIGUOUS"


class BidderEligibility(IntelligenceSchema):
    status: EligibilityStatus = EligibilityStatus.UNSPECIFIED
    allowed_applicant_types: list[ApplicantType] = Field(default_factory=list)
    evidence: list[str] = Field(
        default_factory=list,
        description="Short verbatim page quotes supporting applicant-type eligibility, not inference.",
    )
    evidence_strength: EligibilityEvidenceStrength = Field(
        default=EligibilityEvidenceStrength.AMBIGUOUS,
        description="DIRECT applicant invitation/permission, SUPPORTING personnel context, or AMBIGUOUS. Only independently checked DIRECT evidence establishes restricted types.",
    )

    @model_validator(mode="after")
    def explicit_evidence(self) -> BidderEligibility:
        if self.status is EligibilityStatus.UNSPECIFIED:
            if self.allowed_applicant_types:
                raise ValueError("Unspecified applicant eligibility cannot assert restrictions")
            if self.evidence:
                raise ValueError("Unspecified applicant eligibility cannot assert evidence")
        elif not self.evidence or any(not quote.strip() for quote in self.evidence):
            raise ValueError("Explicit applicant eligibility requires evidence")
        if self.status is EligibilityStatus.RESTRICTED:
            if not self.allowed_applicant_types:
                raise ValueError("Restricted applicant eligibility requires applicant types")
        elif self.allowed_applicant_types:
            raise ValueError("Only restricted eligibility lists allowed applicant types")
        if self.status is not EligibilityStatus.UNSPECIFIED:
            from app.intelligence.applicant_evidence import direct_applicant_types, direct_unrestricted

            direct = (
                direct_applicant_types(self.evidence) == {item.value for item in self.allowed_applicant_types}
                if self.status is EligibilityStatus.RESTRICTED
                else direct_unrestricted(self.evidence)
            )
            if not direct or self.evidence_strength is EligibilityEvidenceStrength.SUPPORTING:
                self.status = EligibilityStatus.UNSPECIFIED
                self.allowed_applicant_types = []
                self.evidence = []
                self.evidence_strength = EligibilityEvidenceStrength.AMBIGUOUS
                logging.getLogger(__name__).info("eligibility_evidence_downgraded=true")
            else:
                self.evidence_strength = EligibilityEvidenceStrength.DIRECT
        elif self.status is EligibilityStatus.UNSPECIFIED:
            self.evidence_strength = EligibilityEvidenceStrength.AMBIGUOUS
        return self


class OpportunityAnalysis(IntelligenceSchema):
    decision: AnalysisDecision
    confidence: int = Field(ge=0, le=100)
    reason: str
    title: str | None = None
    buyer_name: str | None = None
    issuing_organisation: str | None = None
    country: str | None = None
    buyer_country: str | None = None
    delivery_countries: list[str] = Field(default_factory=list)
    eligibility_countries: list[str] = Field(default_factory=list)
    geographic_scope: GeographicScope = GeographicScope.UNKNOWN
    location: str | None = None
    opportunity_type: OpportunityType
    status: OpportunityStatus
    publication_date: date | None = None
    deadline: date | None = None
    contract_duration: str | None = None
    estimated_value: str | None = None
    currency: str | None = None
    summary: str | None = None
    scope: str | None = None
    services_required: list[str] = Field(default_factory=list)
    eligibility_requirements: list[str] = Field(default_factory=list)
    bidder_eligibility: BidderEligibility = Field(default_factory=BidderEligibility)
    experience_requirements: list[str] = Field(default_factory=list)
    qualification_requirements: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    submission_method: str | None = None
    submission_address: str | None = None
    contact_information: list[str] = Field(default_factory=list)
    original_source_url: str | None = None
    discovered_from_url: str | None = None
    source_role: SourceRole = SourceRole.UNKNOWN
    buyer_website: str | None = None
    source_confidence: int | None = Field(default=None, ge=0, le=100)
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def delivery_country_compatibility(self) -> OpportunityAnalysis:
        """Legacy country is delivery only; old country/search/buyer are not evidence."""
        buyer = normalize_countries(
            [self.buyer_country] if self.buyer_country else [], field="buyer_country"
        )
        self.buyer_country = buyer[0] if buyer else None
        self.delivery_countries = normalize_countries(
            self.delivery_countries, field="delivery_countries"
        )
        self.eligibility_countries = normalize_countries(
            self.eligibility_countries, field="eligibility_countries"
        )
        count = len(self.delivery_countries)
        if not count and self.geographic_scope in {
            GeographicScope.SINGLE_COUNTRY, GeographicScope.MULTI_COUNTRY
        }:
            raise ValueError("Declared country scope requires valid delivery countries")
        self.country = (
            self.delivery_countries[0]
            if count == 1
            else "MULTI_COUNTRY"
            if count > 1
            else None
        )
        if self.geographic_scope in {
            GeographicScope.UNKNOWN,
            GeographicScope.SINGLE_COUNTRY,
            GeographicScope.MULTI_COUNTRY,
        }:
            if count > 1:
                self.geographic_scope = GeographicScope.MULTI_COUNTRY
            elif count == 1:
                self.geographic_scope = GeographicScope.SINGLE_COUNTRY
            else:
                self.geographic_scope = GeographicScope.UNKNOWN
        return self

    def matching_payload(self) -> dict[str, object]:
        """Provenance-only enrichment does not change semantic matching inputs."""
        return self.model_dump(
            mode="json",
            exclude={
                "original_source_url",
                "discovered_from_url",
                "source_role",
                "buyer_website",
                "source_confidence",
            },
        )


class CompanyMatch(IntelligenceSchema):
    company_id: int
    score: int = Field(ge=0, le=100)
    match_strength: MatchStrength
    explanation: str = Field(description="Capability, sector, assignment experience and delivery fit ONLY. Do not restate legal applicant-type requirements from titles or prose; structured eligibility owns them.")
    strengths: list[str] = Field(default_factory=list, description="Documented technical/capability strengths only, never applicant-type eligibility.")
    potential_gaps: list[str] = Field(default_factory=list, description="Technical, experience and delivery evidence gaps only; missing evidence is not absence. Applicant-type requirements belong to structured eligibility.")
    eligibility_concerns: list[str] = Field(default_factory=list, description="Other hard requirements needing confirmation. Applicant-type wording comes from the authoritative structured eligibility statement, not an independent inference.")
    recommendation: str = Field(description="Capability/delivery next action only. Do not assert that a legal applicant type is required or permitted.")
    applicant_eligibility: EvidenceStatus = Field(
        default=EvidenceStatus.UNKNOWN,
        description="Applicant-type compatibility, separate from capability score. Application policy is authoritative; unspecified is UNKNOWN.",
    )
