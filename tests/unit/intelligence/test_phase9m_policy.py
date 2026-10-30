import json
from datetime import date
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.intelligence import matching_engine, triage_engine
from app.intelligence.applicant_evidence import capability_only
from app.intelligence.company_matcher import match_company_to_opportunity
from app.intelligence.eligibility import (
    apply_match_eligibility,
    applicant_eligibility_statement,
)
from app.intelligence.schemas import CompanyMatch
from app.intelligence.triage_analyzer import analyze_triage
from app.intelligence.triage_policy import apply_triage_policy, experience_context
from app.models import CompanyOpportunityTriage

from .test_matching_engine import _analysis, _configure, _match, _seed
from .test_triage import result


def checked(analysis, proposed, profile=None):
    match = apply_match_eligibility(_match(1, score=75, strength="STRONG"), analysis)
    adjusted = apply_triage_policy(
        proposed, analysis=analysis, match=match, company_profile=profile
    )
    triage_engine.validate_triage(
        adjusted,
        analysis=analysis,
        match=match,
        minimum_score=70,
        deadline=analysis.deadline,
        status="ACTIVE",
    )
    return adjusted


@pytest.mark.parametrize("evidence", ["CONFIRMED", "POSSIBLE", "UNKNOWN", "CONFLICT"])
def test_unspecified_experience_normalizes_without_inventing_satisfaction(evidence):
    a = _analysis(experience_requirements=[])
    raw = result(experience_evidence=evidence)
    adjusted = checked(a, raw)
    assert adjusted.experience_evidence.value == "UNKNOWN"
    assert raw.experience_evidence.value == evidence
    assert adjusted.priority == raw.priority
    assert any("unspecified" in reason for reason in adjusted.key_reasons)
    if evidence != "UNKNOWN":
        assert "experience_requirement_unknown" in adjusted.policy_adjustments


@pytest.mark.parametrize(
    "experience",
    [{}, {"relevant_experience": None}, {"assignment_types": ["Consultancy"]}],
)
def test_missing_assignment_evidence_is_not_absence(experience):
    profile = {"experience": experience, "capabilities": ["Training"]}
    assert experience_context(profile) == {
        "status": "UNKNOWN",
        "reason": "NOT_DOCUMENTED",
    }
    adjusted = checked(_analysis(), result(experience_evidence="CONFIRMED"), profile)
    assert adjusted.experience_evidence.value == "UNKNOWN"
    assert "mandatory_experience_unconfirmed" in adjusted.policy_adjustments
    assert any("not documented" in gap for gap in adjusted.critical_gaps)
    assert "ABSENT" not in adjusted.model_dump_json()


def test_proportionate_caps_and_documented_conflict_preserved():
    a = _analysis(
        bidder_eligibility={
            "status": "UNRESTRICTED",
            "evidence": ["All applicant types are permitted."],
        }
    )
    profile = {"experience": {"relevant_experience": "Documented assignments"}}
    assert (
        checked(a, result(experience_evidence="CONFIRMED"), profile).bid_readiness.value
        == "READY"
    )
    assert (
        checked(a, result(experience_evidence="POSSIBLE"), profile).bid_readiness.value
        == "NEEDS_WORK"
    )
    unspecified = a.model_copy(update={"experience_requirements": []})
    assert checked(unspecified, result(), profile).bid_readiness.value == "READY"
    qualification = a.model_copy(
        update={"qualification_requirements": ["Professional registration required"]}
    )
    assert (
        checked(
            qualification, result(experience_evidence="CONFIRMED"), profile
        ).bid_readiness.value
        == "NEEDS_WORK"
    )
    conflict = checked(
        a,
        result(
            experience_evidence="CONFLICT",
            bid_readiness="LIKELY_INELIGIBLE",
            priority="DO_NOT_PURSUE",
        ),
        profile,
    )
    assert conflict.experience_evidence.value == "CONFLICT"
    assert conflict.bid_readiness.value == "LIKELY_INELIGIBLE"


def test_sama_policy_barriers_adjustment_persisted_cached_not_raw(
    monkeypatch, isolated_session_factory
):
    _configure(monkeypatch, isolated_session_factory)
    a = _analysis(
        title="SAMA policy, regulatory and interoperability barriers",
        experience_requirements=[],
        qualification_requirements=[],
        eligibility_requirements=[],
    )
    oid, companies = _seed(
        isolated_session_factory,
        analysis=a,
        companies=[
            {
                "name": "Consulting profile",
                "services": ["Institutional development", "Policy advisory"],
            }
        ],
    )
    raw = result(experience_evidence="CONFIRMED")
    analyzer = Mock(return_value=raw)
    matcher = Mock(return_value=_match(companies[0], score=75, strength="STRONG"))
    first = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )[0]
    second = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )[0]
    assert first.status == "TRIAGED" and first.error_category is None
    assert second.status == "CACHED" and second.result == first.result
    assert first.result.experience_evidence.value == "UNKNOWN"
    assert first.result.priority == raw.priority
    with isolated_session_factory() as db:
        stored = db.scalar(select(CompanyOpportunityTriage))
        assert stored.result_data == first.result.model_dump(mode="json")
        assert stored.result_data != raw.model_dump(mode="json")
        assert stored.error_reason is None
    analyzer.assert_called_once()
    matcher.assert_called_once()


@pytest.mark.parametrize(
    "assertion",
    [
        "The opportunity requires a consultancy firm to design the programme.",
        "This is an RFP for a consultancy firm to deliver training.",
    ],
)
def test_eso_capability_path_separate_from_applicant_authority(assertion):
    a = _analysis(title="ESO acceleration programme")
    raw = _match(
        2,
        score=75,
        strength="STRONG",
        explanation=assertion
        + " Institutional development and training align with the assignment.",
        strengths=["Experience partnering with consultancy firms on training."],
        gaps=["Programme delivery history requires confirmation."],
    )
    client = Mock()
    client.generate_structured.return_value = raw
    match = match_company_to_opportunity(
        company_id=2,
        company_profile={"services": ["Institutional development", "Training"]},
        opportunity=a,
        client=client,
    )
    assert match.applicant_eligibility.value == "UNKNOWN"
    assert match.score == raw.score
    assert assertion not in match.explanation
    assert "Institutional development" in match.explanation
    assert match.strengths == raw.strengths
    assert applicant_eligibility_statement(a) in match.eligibility_concerns
    fields = CompanyMatch.model_json_schema()["properties"]
    for field in ("explanation", "strengths", "potential_gaps", "recommendation"):
        assert "applicant" in fields[field]["description"].lower()
    assert client.generate_structured.call_args.args[1] is CompanyMatch
    assert capability_only("Consultancy firms have technical programme experience.")


def test_triage_prompt_receives_explicit_missing_evidence_context():
    client = Mock()
    client.generate_structured.return_value = result()
    a = _analysis()
    analyze_triage(
        company_profile={"experience": {}},
        opportunity=a,
        match=_match(1, score=75, strength="STRONG"),
        effective_deadline=a.deadline,
        effective_status="ACTIVE",
        as_of_date=date.today(),
        client=client,
    )
    payload = json.loads(client.generate_structured.call_args.args[0])
    assert payload["company_experience_evidence"] == {
        "status": "UNKNOWN",
        "reason": "NOT_DOCUMENTED",
    }
    assert matching_engine.MATCH_ANALYSIS_VERSION == "7-geography-evidence-grounding"
    assert triage_engine.TRIAGE_VERSION == "4-hard-requirement-evidence-policy"
