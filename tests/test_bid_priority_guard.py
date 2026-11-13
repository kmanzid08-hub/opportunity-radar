"""Bid prioritization must distinguish capability fit from mandatory eligibility."""
from __future__ import annotations

from datetime import date

import pytest

from app.intelligence import triage_policy
from app.intelligence.schemas import (
    AnalysisDecision,
    CompanyMatch,
    EvidenceStatus,
    MatchStrength,
    OpportunityAnalysis,
    OpportunityStatus,
    OpportunityType,
)
from app.intelligence.triage_engine import validate_triage
from app.intelligence.triage_policy import (
    apply_triage_policy,
    cap_unverified_bid_priority,
    pending_mandatory_checks,
)
from app.intelligence.triage_schemas import (
    BidReadiness,
    EstimatedEffort,
    Priority,
    TriageResult,
    Urgency,
)


def analysis(**overrides) -> OpportunityAnalysis:
    data = dict(
        decision=AnalysisDecision.ACCEPT,
        confidence=92,
        reason="The notice contains a consulting assignment.",
        title="Policy brief on women's access to finance",
        opportunity_type=OpportunityType.CONSULTANCY,
        status=OpportunityStatus.ACTIVE,
        eligibility_requirements=[],
        experience_requirements=[],
        qualification_requirements=[],
        required_documents=[],
    )
    data.update(overrides)
    return OpportunityAnalysis(**data)


def triage(**overrides) -> TriageResult:
    data = dict(
        priority=Priority.HIGH,
        priority_score=91,
        bid_readiness=BidReadiness.MOSTLY_READY,
        urgency=Urgency.HIGH,
        estimated_effort=EstimatedEffort.MEDIUM,
        key_reasons=["Strong technical match."],
        critical_gaps=[],
        eligibility_risks=[],
        recommended_next_action="Prepare a proposal.",
        budget_evidence=EvidenceStatus.UNKNOWN,
        deadline_evidence=EvidenceStatus.UNKNOWN,
        eligibility_evidence=EvidenceStatus.UNKNOWN,
        experience_evidence=EvidenceStatus.UNKNOWN,
        exclusion_applies=False,
        required_documents=[],
    )
    data.update(overrides)
    return TriageResult(**data)


def match() -> CompanyMatch:
    return CompanyMatch(
        company_id=1,
        score=85,
        match_strength=MatchStrength.STRONG,
        explanation="Relevant policy research and institutional consulting.",
        recommendation="Review the terms of reference.",
        applicant_eligibility=EvidenceStatus.UNKNOWN,
    )


def test_unknown_applicant_caps_high_priority_but_preserves_technical_fit():
    item = analysis()
    original = triage()
    protected = cap_unverified_bid_priority(original, item)
    assert protected.priority is Priority.MEDIUM
    assert protected.priority_score <= 69
    assert protected.bid_readiness is BidReadiness.NEEDS_WORK
    assert protected.urgency is Urgency.HIGH
    assert "Confirm that a registered company" in protected.recommended_next_action
    assert original.priority is Priority.HIGH  # No mutation of cached data.
    assert "unverified_mandatory_evidence_priority_cap" in protected.policy_adjustments


def test_generic_validator_keeps_legacy_triage_contract_separate_from_bid_policy():
    # The model validator checks structured consistency; the pursuit policy
    # independently caps the saved/displayed attention priority.
    item = analysis()
    original = triage(
        bid_readiness=BidReadiness.NEEDS_WORK,
        urgency=Urgency.UNKNOWN,
    )
    checked = validate_triage(
        original,
        analysis=item,
        match=match(),
        minimum_score=70,
        deadline=None,
        status="ACTIVE",
    )
    assert checked == original
    assert cap_unverified_bid_priority(checked, item).priority is Priority.MEDIUM


def test_full_policy_caps_urgent_even_with_strong_match():
    item = analysis()
    result = apply_triage_policy(
        triage(priority=Priority.URGENT),
        analysis=item,
        match=match(),
        company_profile={"experience": {"relevant_experience": ["Survey work"]}},
    )
    assert result.priority is Priority.MEDIUM
    assert result.urgency is Urgency.HIGH
    assert result.bid_readiness is BidReadiness.NEEDS_WORK
    assert result.eligibility_evidence is EvidenceStatus.UNKNOWN
    assert result.recommended_next_action.startswith("Before committing bid resources:")
    assert match().score == 85
    assert validate_triage(
        result, analysis=item, match=match(), minimum_score=70,
        deadline=date(2026, 11, 20), status="ACTIVE",
    ) == result


def test_mandatory_experience_requires_evidence_even_when_bidder_type_is_known(monkeypatch):
    monkeypatch.setattr(
        triage_policy, "applicant_eligibility_state",
        lambda _notice: EvidenceStatus.CONFIRMED,
    )
    item = analysis(experience_requirements=["Seven years' relevant experience."])
    possible = triage(
        eligibility_evidence=EvidenceStatus.CONFIRMED,
        experience_evidence=EvidenceStatus.POSSIBLE,
    )
    protected = cap_unverified_bid_priority(possible, item)
    assert protected.priority is Priority.MEDIUM
    assert any("stated experience" in reason for reason in pending_mandatory_checks(item, possible))

    confirmed = possible.model_copy(
        update={"experience_evidence": EvidenceStatus.CONFIRMED}
    )
    assert cap_unverified_bid_priority(confirmed, item).priority is Priority.HIGH


def test_mandatory_qualification_requires_evidence_without_inventing_ineligibility(monkeypatch):
    monkeypatch.setattr(
        triage_policy, "applicant_eligibility_state",
        lambda _notice: EvidenceStatus.CONFIRMED,
    )
    item = analysis(qualification_requirements=["Lead consultant: relevant advanced degree."])
    candidate = triage(eligibility_evidence=EvidenceStatus.CONFIRMED)
    guarded = cap_unverified_bid_priority(candidate, item)
    assert guarded.priority is Priority.MEDIUM
    assert guarded.priority is not Priority.DO_NOT_PURSUE
    assert any("qualification" in text.lower() for text in guarded.key_reasons)


def test_medium_and_low_review_priorities_are_not_artificially_reclassified():
    item = analysis()
    for value in (Priority.MEDIUM, Priority.LOW, Priority.DO_NOT_PURSUE):
        original = triage(priority=value, priority_score=42)
        protected = cap_unverified_bid_priority(original, item)
        assert protected.priority is value
        assert protected.priority_score == 42


def test_no_capping_when_mandatory_requirements_actually_confirmed(monkeypatch):
    monkeypatch.setattr(
        triage_policy, "applicant_eligibility_state",
        lambda _notice: EvidenceStatus.CONFIRMED,
    )
    item = analysis()
    ready = triage(
        eligibility_evidence=EvidenceStatus.CONFIRMED,
        experience_evidence=EvidenceStatus.UNKNOWN,
    )
    assert pending_mandatory_checks(item, ready) == []
    assert cap_unverified_bid_priority(ready, item).priority is Priority.HIGH
