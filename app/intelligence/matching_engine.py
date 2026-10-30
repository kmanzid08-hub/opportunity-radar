from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256

from sqlalchemy import select

from app.database import SessionLocal
from app.company_profiles import CompanyIntelligence
from app.intelligence.company_matcher import match_company_to_opportunity
from app.intelligence.countries import normalize_country
from app.intelligence.eligibility import validate_match_eligibility, apply_match_eligibility
from app.intelligence.opportunity_pipeline import (
    load_canonical_analyses,
    OpportunityProcessingStatus,
    opportunity_quality_status,
)
from app.intelligence.schemas import CompanyMatch, MatchStrength, OpportunityAnalysis, EvidenceStatus
from app.models import (
    CompanyOpportunityMatch,
    OpportunityPreference,
    Organization,
)


DEFAULT_MINIMUM_MATCH_SCORE = 70
MATCH_THRESHOLD_ENV = "COMPANY_MATCH_MINIMUM_SCORE"
MATCH_ANALYSIS_VERSION = "7-geography-evidence-grounding"

CompanyMatcher = Callable[..., CompanyMatch]


class CompanyMatchProcessingStatus(str, Enum):
    MATCHED = "MATCHED"
    CACHED = "CACHED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class CompanyMatchOutcome:
    company_id: int
    status: CompanyMatchProcessingStatus
    match: CompanyMatch | None
    qualifies_for_feed: bool


@dataclass(frozen=True, slots=True)
class CompanyProfileInput:
    company_id: int
    profile: dict[str, object]
    minimum_score: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def configured_minimum_match_score() -> int:
    raw = os.getenv(
        MATCH_THRESHOLD_ENV,
        str(DEFAULT_MINIMUM_MATCH_SCORE),
    ).strip()
    try:
        score = int(raw)
    except ValueError:
        score = DEFAULT_MINIMUM_MATCH_SCORE
    return max(0, min(100, score))


def _deduplicate(values: list[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def build_company_profile(
    organization: Organization,
    preference: OpportunityPreference | None,
) -> CompanyProfileInput:
    """Serialize every currently available profile field for Gemini."""
    industries = _deduplicate(
        [organization.industry] + (list(preference.industries) if preference else [])
    )
    countries = _deduplicate(
        [organization.country] + (list(preference.countries) if preference else [])
    )
    configured_threshold = configured_minimum_match_score()
    intelligence = CompanyIntelligence.model_validate(
        (preference.intelligence or {}) if preference else {}
    )
    if preference is not None and preference.minimum_match_score > 0:
        configured_threshold = max(
            0,
            min(100, preference.minimum_match_score),
        )

    profile: dict[str, object] = {
        "name": organization.name,
        "description": organization.description,
        "website": organization.website,
        "home_country": organization.country,
        "geography": {
            "base_country": normalize_country(organization.country),
            "preferred_countries": _deduplicate([
                normalize_country(value) for value in (preference.countries if preference else [])
            ]),
            "preferred_regions": list(preference.regions) if preference else [],
            "unstructured_geographic_willingness": [
                value for value in (preference.countries if preference else [])
                if normalize_country(value) is None
            ],
            "demonstrated_delivery_countries": None,
        },
        "city": organization.city,
        "services": list(preference.services) if preference else [],
        "capabilities": intelligence.capabilities,
        "areas_of_expertise": intelligence.areas_of_expertise,
        "industries": industries,
        "countries": countries,
        "regions": list(preference.regions) if preference else [],
        "experience": {
            "assignment_types": intelligence.assignment_types,
            "client_types": intelligence.client_types,
            "sector_experience": intelligence.sector_experience,
            "relevant_experience": intelligence.relevant_experience,
            "track_record": intelligence.track_record,
        },
        "preferred_sectors": intelligence.preferred_sectors,
        "exclusions": {
            "services": intelligence.excluded_services,
            "sectors": intelligence.excluded_sectors,
            "opportunity_types": intelligence.excluded_opportunity_types,
            "countries": intelligence.excluded_countries,
        },
        "preferred_opportunity_types": (
            list(preference.opportunity_types) if preference else []
        ),
        "stated_preferences": list(preference.keywords) if preference else [],
        "languages": list(preference.languages) if preference else [],
        "exclusions_and_preferences": {
            "include_no_deadline": (
                preference.include_no_deadline if preference else True
            ),
            "include_jobs": preference.include_jobs if preference else True,
            "include_tenders": preference.include_tenders if preference else True,
            "include_grants": preference.include_grants if preference else True,
            "include_partnerships": (
                preference.include_partnerships if preference else True
            ),
        },
    }
    return CompanyProfileInput(
        company_id=organization.id,
        profile=profile,
        minimum_score=configured_threshold,
    )


def build_match_fingerprint(
    *,
    company_profile: Mapping[str, object],
    opportunity: OpportunityAnalysis,
    minimum_score: int,
) -> str:
    payload = {
        "version": MATCH_ANALYSIS_VERSION,
        "company_profile": dict(company_profile),
        "minimum_score": minimum_score,
        "opportunity_analysis": opportunity.matching_payload(),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _load_verified_analysis(opportunity_id: int) -> OpportunityAnalysis:
    with SessionLocal() as db:
        analysis = load_canonical_analyses(db, {opportunity_id}).get(opportunity_id)

    if analysis is None:
        raise ValueError(
            "A verified AI opportunity analysis is required before matching."
        )
    if opportunity_quality_status(analysis) is not OpportunityProcessingStatus.VERIFIED:
        raise ValueError("Company matching accepts VERIFIED opportunities only.")
    return analysis


def load_active_company_profiles(
    organization_ids: list[int] | None,
) -> list[CompanyProfileInput]:
    """Load active companies and their preferences in one query."""
    with SessionLocal() as db:
        statement = (
            select(Organization, OpportunityPreference)
            .outerjoin(
                OpportunityPreference,
                OpportunityPreference.organization_id == Organization.id,
            )
            .where(Organization.is_active.is_(True))
            .order_by(Organization.id.asc())
        )
        if organization_ids is not None:
            statement = statement.where(Organization.id.in_(organization_ids))
        return [
            build_company_profile(organization, preference)
            for organization, preference in db.execute(statement).all()
        ]


def _match_from_record(
    record: CompanyOpportunityMatch,
) -> CompanyMatch | None:
    if (
        record.error_reason is not None
        or record.score is None
        or record.match_strength is None
        or record.explanation is None
        or record.recommendation is None
    ):
        return None
    try:
        return CompanyMatch.model_validate(
            {
                "company_id": record.organization_id,
                "score": record.score,
                "match_strength": record.match_strength,
                "explanation": record.explanation,
                "strengths": record.strengths,
                "potential_gaps": record.potential_gaps,
                "eligibility_concerns": record.eligibility_concerns,
                "recommendation": record.recommendation,
                "applicant_eligibility": record.applicant_eligibility or EvidenceStatus.UNKNOWN.value,
            }
        )
    except (TypeError, ValueError):
        return None


def _cached_match(
    *,
    company_id: int,
    opportunity_id: int,
    fingerprint: str,
) -> tuple[CompanyMatch, bool] | None:
    with SessionLocal() as db:
        record = db.scalar(
            select(CompanyOpportunityMatch).where(
                CompanyOpportunityMatch.organization_id == company_id,
                CompanyOpportunityMatch.opportunity_id == opportunity_id,
                CompanyOpportunityMatch.match_fingerprint == fingerprint,
            )
        )
        if record is None:
            return None
        match = _match_from_record(record)
        if match is None:
            return None
        return match, record.qualifies_for_feed


def _persist_match(
    *,
    opportunity_id: int,
    profile: CompanyProfileInput,
    fingerprint: str,
    match: CompanyMatch | None,
    error_reason: str | None = None,
) -> bool:
    qualifies = bool(
        match is not None
        and match.score >= profile.minimum_score
        and match.match_strength is not MatchStrength.NOT_A_MATCH
        and match.applicant_eligibility is not EvidenceStatus.CONFLICT
    )
    with SessionLocal() as db:
        record = db.scalar(
            select(CompanyOpportunityMatch).where(
                CompanyOpportunityMatch.organization_id == profile.company_id,
                CompanyOpportunityMatch.opportunity_id == opportunity_id,
            )
        )
        if record is None:
            record = CompanyOpportunityMatch(
                organization_id=profile.company_id,
                opportunity_id=opportunity_id,
                strengths=[],
                potential_gaps=[],
                eligibility_concerns=[],
                minimum_score=profile.minimum_score,
                qualifies_for_feed=False,
                match_fingerprint=fingerprint,
                matched_at=_utc_now(),
            )
            db.add(record)

        record.minimum_score = profile.minimum_score
        record.qualifies_for_feed = qualifies
        record.match_fingerprint = fingerprint
        record.matched_at = _utc_now()
        record.error_reason = error_reason
        record.applicant_eligibility = match.applicant_eligibility.value if match else None
        if match is None:
            record.score = None
            record.match_strength = None
            record.explanation = None
            record.strengths = []
            record.potential_gaps = []
            record.eligibility_concerns = []
            record.recommendation = None
        else:
            record.score = match.score
            record.match_strength = match.match_strength.value
            record.explanation = match.explanation
            record.strengths = list(match.strengths)
            record.potential_gaps = list(match.potential_gaps)
            record.eligibility_concerns = list(match.eligibility_concerns)
            record.recommendation = match.recommendation
        db.commit()
    return qualifies


def match_verified_opportunity(
    opportunity_id: int,
    *,
    organization_ids: list[int] | None = None,
    profiles: list[CompanyProfileInput] | None = None,
    matcher: CompanyMatcher | None = None,
) -> list[CompanyMatchOutcome]:
    """Match one verified opportunity against selected or all active companies."""
    if opportunity_id < 1:
        raise ValueError("Opportunity ID must be a positive integer.")
    if profiles is not None and organization_ids is not None:
        raise ValueError("Pass profiles or organization_ids, not both.")

    opportunity = _load_verified_analysis(opportunity_id)
    active_profiles = (
        profiles
        if profiles is not None
        else load_active_company_profiles(organization_ids)
    )
    generate_match = matcher or match_company_to_opportunity
    outcomes: list[CompanyMatchOutcome] = []

    for profile in active_profiles:
        fingerprint = build_match_fingerprint(
            company_profile=profile.profile,
            opportunity=opportunity,
            minimum_score=profile.minimum_score,
        )
        cached = _cached_match(
            company_id=profile.company_id,
            opportunity_id=opportunity_id,
            fingerprint=fingerprint,
        )
        if cached is not None:
            match, qualifies = cached
            outcomes.append(
                CompanyMatchOutcome(
                    company_id=profile.company_id,
                    status=CompanyMatchProcessingStatus.CACHED,
                    match=match,
                    qualifies_for_feed=qualifies,
                )
            )
            continue

        try:
            match = generate_match(
                company_id=profile.company_id,
                company_profile=profile.profile,
                opportunity=opportunity,
            )
            if isinstance(match, CompanyMatch):
                validate_match_eligibility(match, opportunity)
                match = apply_match_eligibility(match, opportunity)
                from app.intelligence.gap_evidence import normalize_match_gaps

                match = normalize_match_gaps(match, profile.profile)
        except Exception:
            _persist_match(
                opportunity_id=opportunity_id,
                profile=profile,
                fingerprint=fingerprint,
                match=None,
                error_reason="Gemini company matching failed.",
            )
            outcomes.append(
                CompanyMatchOutcome(
                    company_id=profile.company_id,
                    status=CompanyMatchProcessingStatus.ERROR,
                    match=None,
                    qualifies_for_feed=False,
                )
            )
            continue

        if (
            not isinstance(match, CompanyMatch)
            or match.company_id != profile.company_id
        ):
            _persist_match(
                opportunity_id=opportunity_id,
                profile=profile,
                fingerprint=fingerprint,
                match=None,
                error_reason="Gemini returned an invalid company match.",
            )
            outcomes.append(
                CompanyMatchOutcome(
                    company_id=profile.company_id,
                    status=CompanyMatchProcessingStatus.ERROR,
                    match=None,
                    qualifies_for_feed=False,
                )
            )
            continue

        qualifies = _persist_match(
            opportunity_id=opportunity_id,
            profile=profile,
            fingerprint=fingerprint,
            match=match,
        )
        outcomes.append(
            CompanyMatchOutcome(
                company_id=profile.company_id,
                status=CompanyMatchProcessingStatus.MATCHED,
                match=match,
                qualifies_for_feed=qualifies,
            )
        )

    return outcomes
