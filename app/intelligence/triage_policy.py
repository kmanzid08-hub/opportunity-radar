"""Eligibility policy applies before the unchanged domain validator/persistence."""

import logging
from collections.abc import Mapping

from app.intelligence.eligibility import (
    CONFIRM_ACTION,
    CONFIRM_APPLICANT,
    applicant_eligibility_state,
    applicant_eligibility_statement,
)
from app.intelligence.schemas import CompanyMatch, EvidenceStatus, OpportunityAnalysis
from app.intelligence.triage_schemas import BidReadiness, Priority, TriageResult


logger = logging.getLogger(__name__)


def experience_context(profile: Mapping[str, object] | None) -> dict[str, str]:
    """Profile claims can support review, but preferences/tags are not assignments."""
    experience = (profile or {}).get("experience")
    documented = isinstance(experience, Mapping) and any(
        experience.get(key) for key in ("relevant_experience", "track_record")
    )
    return {
        "status": "POSSIBLE" if documented else "UNKNOWN",
        "reason": "SUPPLIED_NOT_INDEPENDENTLY_VERIFIED"
        if documented
        else "NOT_DOCUMENTED",
    }


def pending_mandatory_checks(
    analysis: OpportunityAnalysis,
    result: TriageResult | None,
) -> list[str]:
    """Unverified hard requirements, not assertions that the company is ineligible.

    This is a bid-decision gate, separate from the technical capability match.
    The company profile currently lacks independently verified qualification
    and supporting-contract evidence fields.
    """
    reasons: list[str] = []
    if (
        applicant_eligibility_state(analysis) is not EvidenceStatus.CONFIRMED
        or (result is not None and result.eligibility_evidence is not EvidenceStatus.CONFIRMED)
    ):
        reasons.append("Confirm that a registered company may submit this tender.")
    if analysis.qualification_requirements:
        reasons.append(
            "Verify the stated professional qualifications, licences or registrations "
            "against the proposed team and company documents."
        )
    if analysis.experience_requirements and (
        result is None or result.experience_evidence is not EvidenceStatus.CONFIRMED
    ):
        reasons.append(
            "Provide evidence of assignments meeting the tender's stated "
            "experience requirements."
        )
    return reasons


def cap_unverified_bid_priority(
    result: TriageResult,
    analysis: OpportunityAnalysis,
) -> TriageResult:
    """Conservative priority guard for newly generated and saved triage.

    Never lower the capability-match score, change the tender deadline, or
    treat missing evidence as proof of ineligibility.
    """
    checks = pending_mandatory_checks(analysis, result)
    if not checks or result.priority not in {Priority.HIGH, Priority.URGENT}:
        return result
    data = result.model_dump()
    data["priority"] = Priority.MEDIUM
    data["priority_score"] = min(result.priority_score, 69)
    if result.bid_readiness in {BidReadiness.READY, BidReadiness.MOSTLY_READY}:
        data["bid_readiness"] = BidReadiness.NEEDS_WORK
    data["key_reasons"] = list(dict.fromkeys([
        *result.key_reasons,
        "High capability fit does not establish eligibility to bid.",
        *checks,
    ]))
    data["recommended_next_action"] = "Before committing bid resources: " + " ".join(checks)
    data["policy_adjustments"] = list(dict.fromkeys([
        *result.policy_adjustments,
        "unverified_mandatory_evidence_priority_cap",
    ]))
    return TriageResult.model_validate(data)


def apply_triage_policy(
    result: TriageResult,
    *,
    analysis: OpportunityAnalysis,
    match: CompanyMatch,
    company_profile: Mapping[str, object] | None = None,
) -> TriageResult:
    if not isinstance(result, TriageResult):
        raise ValueError("Invalid triage response")
    data = result.model_dump()
    data["policy_adjustments"] = []  # Never trust provider-supplied policy audit data.
    data["eligibility_statement"] = applicant_eligibility_statement(analysis)
    state = applicant_eligibility_state(analysis)
    if state is EvidenceStatus.CONFLICT:
        raise ValueError("Applicant conflict must not reach triage")
    unknown = (
        state is EvidenceStatus.UNKNOWN
        or match.applicant_eligibility
        in {EvidenceStatus.UNKNOWN, EvidenceStatus.POSSIBLE}
        or not analysis.eligibility_requirements
    )
    if unknown:
        data["eligibility_evidence"] = EvidenceStatus.UNKNOWN
        data["critical_gaps"] = list(
            dict.fromkeys([CONFIRM_APPLICANT, *result.critical_gaps])
        )
        data["eligibility_risks"] = list(
            dict.fromkeys([data["eligibility_statement"], *result.eligibility_risks])
        )
        data["recommended_next_action"] = CONFIRM_ACTION
        if result.bid_readiness in {BidReadiness.READY, BidReadiness.MOSTLY_READY}:
            data["bid_readiness"] = BidReadiness.NEEDS_WORK
            data["policy_adjustments"].append("eligibility_unknown_readiness_cap")
        if result.eligibility_evidence is not EvidenceStatus.UNKNOWN:
            data["policy_adjustments"].append("eligibility_unknown_evidence")
    # Requirement absence is not satisfaction. Normalize before the unchanged
    # validator, without rewriting attention scores or inventing experience.
    if not analysis.experience_requirements:
        data["experience_evidence"] = EvidenceStatus.UNKNOWN
        if result.experience_evidence is not EvidenceStatus.UNKNOWN:
            data["policy_adjustments"].append("experience_requirement_unknown")
        gap = "Experience requirements are unspecified; confirm them in the tender documents."
        # An unspecified requirement is a clarification, not a known blocker.
        data["key_reasons"] = list(dict.fromkeys([*data["key_reasons"], gap]))
    elif experience_context(company_profile)["reason"] == "NOT_DOCUMENTED":
        data["experience_evidence"] = EvidenceStatus.UNKNOWN
        gap = "Evidence against the stated experience requirements is not documented; obtain and confirm relevant assignments."
        data["critical_gaps"] = list(dict.fromkeys([*data["critical_gaps"], gap]))
        data["policy_adjustments"].append("mandatory_experience_unconfirmed")
    # Existing qualification_requirements are requirements, not preferences.
    # Profiles currently have no structured qualification/registration evidence
    # field; neither a service label nor a high match score establishes compliance.
    if analysis.qualification_requirements:
        gap = "Confirm documented qualifications, registrations or certifications against the stated requirements."
        data["critical_gaps"] = list(dict.fromkeys([*data["critical_gaps"], gap]))
        data["policy_adjustments"].append("mandatory_qualifications_unconfirmed")
    material_unconfirmed = bool(analysis.qualification_requirements) or (
        bool(analysis.experience_requirements)
        and data["experience_evidence"]
        in {EvidenceStatus.UNKNOWN, EvidenceStatus.POSSIBLE}
    )
    if material_unconfirmed:
        if data["bid_readiness"] in {BidReadiness.READY, BidReadiness.MOSTLY_READY}:
            data["bid_readiness"] = BidReadiness.NEEDS_WORK
            data["policy_adjustments"].append(
                "hard_requirement_unconfirmed_readiness_cap"
            )
        action = "Confirm evidence against the mandatory experience and qualification requirements before committing bid resources."
        data["recommended_next_action"] = (
            CONFIRM_ACTION + " " + action if unknown else action
        )
    for adjustment in data["policy_adjustments"]:
        logger.info("policy_adjustment=%s", adjustment)
    return cap_unverified_bid_priority(TriageResult.model_validate(data), analysis)
