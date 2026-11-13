from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256

from sqlalchemy import select

from app import database
from app.intelligence.gemini_client import load_gemini_config
from app.intelligence.downstream_diagnostics import record_failure
from app.intelligence.eligibility import individual_only_conflict, applicant_eligibility_state
from app.intelligence.triage_policy import (
    apply_triage_policy,
    cap_unverified_bid_priority,
)
from app.intelligence.matching_engine import (
    CompanyMatchOutcome,
    CompanyMatcher,
    CompanyProfileInput,
    _load_verified_analysis,
    build_match_fingerprint,
    load_active_company_profiles,
    match_verified_opportunity,
)
from app.intelligence.schemas import CompanyMatch, MatchStrength, OpportunityAnalysis
from app.intelligence.triage_analyzer import TRIAGE_VERSION, analyze_triage
from app.intelligence.triage_store import effective_status
from app.intelligence.triage_schemas import (
    BidReadiness,
    EvidenceStatus,
    Priority,
    TriageResult,
    Urgency,
)
from app.models import CompanyOpportunityTriage, Opportunity


logger = logging.getLogger(__name__)
TriageAnalyzer = Callable[..., TriageResult]


@dataclass(frozen=True, slots=True)
class TriageOutcome:
    company_id: int
    status: str
    result: TriageResult | None
    error_category: str | None = None


def _fingerprint(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def build_triage_fingerprint(
    *,
    profile: CompanyProfileInput,
    analysis: OpportunityAnalysis,
    match: CompanyMatch,
    match_fingerprint: str,
    deadline: date | None,
    status: str,
    today: date,
) -> str:
    return _fingerprint(
        {
            "company_profile_fingerprint": _fingerprint(profile.profile),
            "opportunity_analysis_fingerprint": _fingerprint(
                analysis.model_dump(mode="json")
            ),
            "company_match_fingerprint": match_fingerprint,
            "match_result": match.model_dump(mode="json"),
            "deadline": deadline,
            "status": status,
            "assessment_date": today,
            "configuration": {
                "version": TRIAGE_VERSION,
                "model": load_gemini_config().model,
                "minimum_score": profile.minimum_score,
            },
        }
    )


def validate_triage(
    result: TriageResult,
    *,
    analysis: OpportunityAnalysis,
    match: CompanyMatch,
    minimum_score: int,
    deadline: date | None,
    status: str,
) -> TriageResult:
    """Reject unsafe/invented structured results; never synthesize a fallback score."""
    if not isinstance(result, TriageResult):
        raise ValueError("Invalid triage response")
    # Revalidate even objects supplied by an injected analyzer.
    result = TriageResult.model_validate(result.model_dump())
    high = result.priority in {Priority.HIGH, Priority.URGENT}
    weak = match.score < minimum_score or match.match_strength in {
        MatchStrength.WEAK,
        MatchStrength.NOT_A_MATCH,
    }
    if high and weak:
        raise ValueError("Weak matches cannot have high priority")
    if result.bid_readiness is BidReadiness.READY and (
        result.critical_gaps
        or result.eligibility_risks
        or match.eligibility_concerns
        or result.eligibility_evidence is not EvidenceStatus.CONFIRMED
    ):
        raise ValueError("Readiness contradicts eligibility evidence")
    if not analysis.eligibility_requirements and (
        result.eligibility_evidence is not EvidenceStatus.UNKNOWN
        or result.bid_readiness in {BidReadiness.READY, BidReadiness.MOSTLY_READY}
    ):
        raise ValueError("Missing eligibility must remain unknown")
    if result.bid_readiness in {BidReadiness.READY, BidReadiness.MOSTLY_READY} and (
        result.eligibility_evidence is not EvidenceStatus.CONFIRMED
    ):
        raise ValueError("Readiness contradicts eligibility evidence")
    if individual_only_conflict(analysis) and (
        result.eligibility_evidence is not EvidenceStatus.CONFLICT
        or result.bid_readiness is not BidReadiness.LIKELY_INELIGIBLE
        or result.priority is not Priority.DO_NOT_PURSUE
    ):
        raise ValueError("Readiness contradicts eligibility evidence")
    if deadline is None and (
        result.urgency is not Urgency.UNKNOWN
        or result.deadline_evidence is not EvidenceStatus.UNKNOWN
    ):
        raise ValueError("Missing deadline must remain unknown")
    if (
        not analysis.estimated_value
        and result.budget_evidence is not EvidenceStatus.UNKNOWN
    ):
        raise ValueError("Missing budget must remain unknown")
    if (
        not analysis.experience_requirements
        and result.experience_evidence is not EvidenceStatus.UNKNOWN
    ):
        raise ValueError("Missing experience requirements must remain unknown")
    if (
        result.exclusion_applies is True
        or result.bid_readiness is BidReadiness.LIKELY_INELIGIBLE
        or status in {"CLOSED", "EXPIRED", "REJECTED"}
        or analysis.status.value in {"CLOSED", "EXPIRED"}
    ) and result.priority is not Priority.DO_NOT_PURSUE:
        raise ValueError("Priority contradicts a known blocker")
    names = [document.name for document in result.required_documents]
    if set(names) != set(analysis.required_documents) or len(names) != len(set(names)):
        raise ValueError("Required documents must exactly reflect the analysis")
    return result


def triage_verified_opportunity(
    opportunity_id: int,
    *,
    profiles: list[CompanyProfileInput] | None = None,
    matches: list[CompanyMatchOutcome] | None = None,
    matcher: CompanyMatcher | None = None,
    analyzer: TriageAnalyzer | None = None,
    as_of_date: date | None = None,
) -> list[TriageOutcome]:
    """Refresh matching then triage, or reuse matches from the scanner's same run.

    This is a background/pipeline operation, never a rendering operation.
    Deadline-only updates to Opportunity do not mutate or reanalyze its semantic
    analysis, so matching stays cached. Updated profiles/analyses invalidate both.
    Each triage write commits separately from verified opportunities and matches.
    """
    active_profiles = (
        profiles if profiles is not None else load_active_company_profiles(None)
    )
    if not active_profiles:
        return []
    analysis = _load_verified_analysis(opportunity_id)
    outcomes = (
        matches
        if matches is not None
        else match_verified_opportunity(
            opportunity_id,
            profiles=active_profiles,
            matcher=matcher,
        )
    )
    by_company = {outcome.company_id: outcome for outcome in outcomes}
    today = as_of_date or datetime.now(timezone.utc).date()
    with database.SessionLocal() as db:
        opportunity = db.get(Opportunity, opportunity_id)
        if opportunity is None:
            raise ValueError("Opportunity does not exist")
        deadline = opportunity.deadline
        status = effective_status(opportunity, today)
        records = {
            record.organization_id: record
            for record in db.scalars(
                select(CompanyOpportunityTriage).where(
                    CompanyOpportunityTriage.opportunity_id == opportunity_id,
                )
            )
        }
    generate = analyzer or analyze_triage
    results: list[TriageOutcome] = []
    for profile in active_profiles:
        outcome = by_company.get(profile.company_id)
        if (
            outcome is None or outcome.match is None or not outcome.qualifies_for_feed
            or applicant_eligibility_state(analysis) is EvidenceStatus.CONFLICT
            or outcome.match.applicant_eligibility is EvidenceStatus.CONFLICT
        ):
            results.append(TriageOutcome(profile.company_id, "SKIPPED", None))
            continue
        match = outcome.match
        match_fingerprint = build_match_fingerprint(
            company_profile=profile.profile,
            opportunity=analysis,
            minimum_score=profile.minimum_score,
        )
        fingerprint = build_triage_fingerprint(
            profile=profile,
            analysis=analysis,
            match=match,
            match_fingerprint=match_fingerprint,
            deadline=deadline,
            status=status,
            today=today,
        )
        record = records.get(profile.company_id)
        if (
            record is not None
            and record.triage_fingerprint == fingerprint
            and not record.error_reason
        ):
            try:
                cached = TriageResult.model_validate(record.result_data)
            except ValueError:
                pass
            else:
                # Historical stored HIGH ratings may predate the hard-evidence
                # policy. Preserve the source record but return a safe decision.
                results.append(TriageOutcome(
                    profile.company_id, "CACHED",
                    cap_unverified_bid_priority(cached, analysis),
                ))
                continue
        result = None
        error = None
        failure = None
        try:
            result = validate_triage(
                apply_triage_policy(
                    generate(
                        company_profile=profile.profile,
                        opportunity=analysis,
                        match=match,
                        effective_deadline=deadline,
                        effective_status=status,
                        as_of_date=today,
                    ),
                    analysis=analysis,
                    match=match,
                    company_profile=profile.profile,
                ),
                analysis=analysis,
                match=match,
                minimum_score=profile.minimum_score,
                deadline=deadline,
                status=status,
            )
        except Exception as exc:
            result = None
            failure = record_failure(exc, stage="TRIAGE", schema=TriageResult)
            error = failure.serialize()
        try:
            with database.SessionLocal() as db:
                stored = db.scalar(
                    select(CompanyOpportunityTriage).where(
                        CompanyOpportunityTriage.organization_id == profile.company_id,
                        CompanyOpportunityTriage.opportunity_id == opportunity_id,
                    )
                )
                if stored is None:
                    stored = CompanyOpportunityTriage(
                        organization_id=profile.company_id,
                        opportunity_id=opportunity_id,
                    )
                    db.add(stored)
                stored.triage_fingerprint = fingerprint
                stored.match_fingerprint = match_fingerprint
                stored.effective_deadline = deadline
                stored.effective_status = status
                stored.assessment_date = today
                stored.result_data = result.model_dump(mode="json") if result else None
                stored.error_reason = error
                stored.assessed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            failure = record_failure(
                exc,
                stage="TRIAGE",
                schema=TriageResult,
                persistence=True,
            )
            error = failure.serialize()
        results.append(
            TriageOutcome(
                profile.company_id,
                "ERROR" if error else "TRIAGED",
                result if not error else None,
                failure.category if failure else None,
            )
        )
    return results
