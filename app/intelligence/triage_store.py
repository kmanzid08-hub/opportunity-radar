"""Read-only triage helpers. No AI clients or generation imports."""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.intelligence.triage_schemas import TriageResult
from app.intelligence.match_store import match_is_current
from app.intelligence.matching_engine import CompanyProfileInput, _match_from_record
from app.intelligence.schemas import OpportunityAnalysis
from app.models import CompanyOpportunityMatch, CompanyOpportunityTriage, Opportunity


PRIORITY_ORDER = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 4, "DO_NOT_PURSUE": 5}


def effective_status(opportunity: Opportunity, today: date) -> str:
    """Reading a notice (New -> Seen) does not change its bidding status."""
    if opportunity.is_expired or (
        opportunity.deadline is not None and opportunity.deadline < today
    ):
        return "EXPIRED"
    status = opportunity.status.upper()
    return "ACTIVE" if status in {"NEW", "SEEN", "INTERESTED"} else status


def load_stored_triage(
    db: Session,
    opportunity_ids: set[int],
) -> dict[tuple[int, int], CompanyOpportunityTriage]:
    if not opportunity_ids:
        return {}
    return {
        (record.opportunity_id, record.organization_id): record
        for record in db.scalars(
            select(CompanyOpportunityTriage).where(
                CompanyOpportunityTriage.opportunity_id.in_(opportunity_ids),
                CompanyOpportunityTriage.error_reason.is_(None),
            )
        )
    }


def stored_triage_result(
    opportunity: Opportunity,
    match: CompanyOpportunityMatch,
    record: CompanyOpportunityTriage | None,
    *,
    profile: CompanyProfileInput | None = None,
    analysis: OpportunityAnalysis | None = None,
) -> TriageResult | None:
    today = datetime.now(timezone.utc).date()
    if (
        record is None
        or record.error_reason
        or record.match_fingerprint != match.match_fingerprint
        or record.effective_deadline != opportunity.deadline
        or record.effective_status != effective_status(opportunity, today)
        or record.assessment_date != today
        or not match_is_current(match, profile=profile, analysis=analysis)
    ):
        return None
    try:
        from app.intelligence.triage_engine import build_triage_fingerprint

        parsed_match = _match_from_record(match)
        if parsed_match is None or profile is None or analysis is None:
            return None
        expected = build_triage_fingerprint(
            profile=profile, analysis=analysis, match=parsed_match,
            match_fingerprint=match.match_fingerprint, deadline=opportunity.deadline,
            status=effective_status(opportunity, today), today=today,
        )
        if record.triage_fingerprint != expected:
            return None
        from app.intelligence.triage_policy import cap_unverified_bid_priority

        # Older cached HIGH ratings remain visible but are shown conservatively
        # until the separate mandatory eligibility evidence is verified.
        return cap_unverified_bid_priority(
            TriageResult.model_validate(record.result_data), analysis
        )
    except ValueError:
        return None
