"""Read-only currentness for derived company decisions. Never refresh on GET."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intelligence.matching_engine import (
    CompanyProfileInput,
    build_match_fingerprint,
    build_company_profile,
)
from app.intelligence.schemas import OpportunityAnalysis
from app.models import CompanyOpportunityMatch, Organization, OpportunityPreference


def load_current_profiles(db: Session) -> dict[int, CompanyProfileInput]:
    """One batched profile read per render, shared by match/triage checks."""
    rows = db.execute(
        select(Organization, OpportunityPreference)
        .outerjoin(
            OpportunityPreference,
            OpportunityPreference.organization_id == Organization.id,
        )
        .where(Organization.is_active.is_(True))
    )
    return {
        organization.id: build_company_profile(organization, preference)
        for organization, preference in rows
    }


def match_is_current(
    match: CompanyOpportunityMatch,
    *,
    profile: CompanyProfileInput | None,
    analysis: OpportunityAnalysis | None,
) -> bool:
    if profile is None or analysis is None or match.error_reason:
        return False
    return (
        match.organization_id == profile.company_id
        and match.match_fingerprint
        == build_match_fingerprint(
            company_profile=profile.profile,
            opportunity=analysis,
            minimum_score=profile.minimum_score,
        )
    )
