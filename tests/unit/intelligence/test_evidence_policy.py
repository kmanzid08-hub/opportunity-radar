import json
import logging
from datetime import date, timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.intelligence import triage_engine, triage_policy
from app.intelligence.company_matcher import match_company_to_opportunity
from app.intelligence.downstream_diagnostics import validation_details
from app.intelligence.eligibility import (
    EVIDENCE_INSTRUCTION,
    UNKNOWN_APPLICANT,
    CONFIRM_ACTION,
    CONFIRM_APPLICANT,
    applicant_eligibility_state,
    applicant_eligibility_statement,
    apply_match_eligibility,
    evidence_context,
)
from app.intelligence.schemas import EvidenceStatus, OpportunityAnalysis
from app.intelligence.triage_analyzer import analyze_triage
from app.intelligence.triage_policy import apply_triage_policy
from app.intelligence.triage_engine import validate_triage
from app.models import CompanyOpportunityMatch, CompanyOpportunityTriage

from .test_matching_engine import _analysis, _configure, _match, _seed
from .test_triage import result


def analysis(**kwargs):
    return _analysis(deadline=date.today() + timedelta(days=7), **kwargs)


def proposed(**kwargs):
    return result(**kwargs)


@pytest.mark.parametrize(
    "status,types,expected",
    [
        ("UNSPECIFIED", [], "UNKNOWN"),
        ("UNRESTRICTED", [], "CONFIRMED"),
        ("RESTRICTED", ["INDIVIDUAL"], "CONFLICT"),
        ("RESTRICTED", ["CONSULTING_FIRM"], "UNKNOWN"),
        ("RESTRICTED", ["CONSULTING_FIRM", "JOINT_VENTURE"], "UNKNOWN"),
        ("RESTRICTED", ["NGO"], "UNKNOWN"),
    ],
)
def test_structured_applicant_authority_not_prose(status, types, expected):
    from .test_eligibility_geography_quality import eligibility

    a = analysis(
        bidder_eligibility=eligibility(types, status=status)
    )
    assert applicant_eligibility_state(a).value == expected
    raw = _match(1, score=92, strength="VERY_STRONG").model_copy(
        update={"applicant_eligibility": EvidenceStatus.CONFIRMED}
    )
    checked = apply_match_eligibility(raw, a)
    assert checked.applicant_eligibility.value == expected
    assert checked.score == 92  # Policy does not rewrite capability score.


def test_unspecified_legacy_prose_cannot_invent_organizational_requirement():
    a = analysis(eligibility_requirements=["Organizational bidders required"])
    assert applicant_eligibility_state(a) is EvidenceStatus.UNKNOWN
    assert applicant_eligibility_statement(a) == UNKNOWN_APPLICANT
    context = evidence_context({}, a)
    assert context["opportunity_applicant_policy"]["applicant_eligibility"] == "UNKNOWN"
    assert "cannot redefine" in context["legacy_requirements_authority"]
    assert "never assert that firms are permitted" in EVIDENCE_INSTRUCTION


def test_smart_africa_missing_evidence_not_absence_and_preferences_preserved():
    profile = {
        "capabilities": ["Capacity building"],
        "experience": {},
        "geography": {
            "base_country": "Rwanda",
            "preferred_regions": ["East Africa", "Africa"],
            "unstructured_geographic_willingness": ["Africa-wide assignments"],
            "demonstrated_delivery_countries": None,
        },
    }
    original = json.dumps(profile, sort_keys=True)
    context = evidence_context(
        profile, analysis(delivery_countries=["Ghana", "Mozambique"])
    )
    geo = context["company_unconfirmed_evidence"]["demonstrated_delivery_geography"]
    assert geo == {"status": "UNKNOWN", "reason": "NOT_DOCUMENTED"}
    assert context["company_geography"]["preferred_regions"] == [
        "East Africa",
        "Africa",
    ]
    assert context["company_geography"]["demonstrated_delivery_countries"] is None
    assert json.dumps(profile, sort_keys=True) == original
    assert "NOT DOCUMENTED != DOES NOT EXIST" in EVIDENCE_INSTRUCTION
    assert "Do not assert absent overseas offices" in EVIDENCE_INSTRUCTION
    assert "PREFERENCES are not prior delivery experience" in EVIDENCE_INSTRUCTION


def test_both_prompts_use_same_evidence_contract():
    a = analysis()
    match = _match(1, score=92, strength="VERY_STRONG")
    client = Mock()
    client.generate_structured.return_value = match
    checked = match_company_to_opportunity(
        company_id=1, company_profile={"name": "Example"}, opportunity=a, client=client
    )
    match_call = client.generate_structured.call_args
    assert EVIDENCE_INSTRUCTION in match_call.kwargs["system_instruction"]
    assert '"reason": "NOT_DOCUMENTED"' in match_call.args[0]
    client.generate_structured.return_value = proposed()
    analyze_triage(
        company_profile={"name": "Example"},
        opportunity=a,
        match=checked,
        effective_deadline=a.deadline,
        effective_status="ACTIVE",
        as_of_date=date.today(),
        client=client,
    )
    assert (
        EVIDENCE_INSTRUCTION
        in client.generate_structured.call_args.kwargs["system_instruction"]
    )
    payload = json.loads(client.generate_structured.call_args.args[0])
    assert (
        payload["evidence_contract"]["opportunity_applicant_policy"][
            "applicant_eligibility"
        ]
        == "UNKNOWN"
    )


@pytest.mark.parametrize(
    "readiness", ["READY", "MOSTLY_READY", "UNKNOWN", "NEEDS_WORK"]
)
@pytest.mark.parametrize("requirements", [[], ["Five years of experience"]])
def test_unknown_policy_is_conservative_valid_and_nonmutating(readiness, requirements):
    a = analysis(eligibility_requirements=requirements)
    match = apply_match_eligibility(_match(1, score=92, strength="VERY_STRONG"), a)
    raw = proposed(bid_readiness=readiness, eligibility_evidence="CONFIRMED")
    before = raw.model_dump()
    checked = apply_triage_policy(raw, analysis=a, match=match)
    assert checked.eligibility_evidence.value == "UNKNOWN"
    assert checked.bid_readiness.value == (
        "NEEDS_WORK" if readiness in {"READY", "MOSTLY_READY"} else readiness
    )
    assert checked.eligibility_statement == UNKNOWN_APPLICANT
    assert CONFIRM_APPLICANT in checked.critical_gaps
    assert checked.recommended_next_action.startswith(CONFIRM_ACTION)
    assert "mandatory experience" in checked.recommended_next_action
    assert (
        checked.priority == raw.priority
        and checked.priority_score == raw.priority_score
    )
    assert (
        checked.urgency == raw.urgency
        and checked.estimated_effort == raw.estimated_effort
    )
    assert raw.model_dump() == before
    validate_triage(
        checked,
        analysis=a,
        match=match,
        minimum_score=70,
        deadline=a.deadline,
        status="ACTIVE",
    )


def test_confirmed_compatible_has_no_policy_cap():
    a = analysis(
        bidder_eligibility={
            "status": "UNRESTRICTED",
            "evidence": ["All applicant types accepted"],
        }
    )
    match = apply_match_eligibility(_match(1, score=92, strength="VERY_STRONG"), a)
    checked = apply_triage_policy(
        proposed(experience_evidence="CONFIRMED"), analysis=a, match=match,
        company_profile={"experience": {"relevant_experience": "Ten years and three similar assignments"}},
    )
    assert checked.bid_readiness.value == "READY"
    assert checked.eligibility_evidence.value == "CONFIRMED"
    assert checked.policy_adjustments == []


def test_policy_log_is_safe_and_provider_cannot_supply_adjustments(caplog, monkeypatch):
    monkeypatch.setattr(triage_policy.logger, "disabled", False)
    raw = proposed(
        key_reasons=["PRIVATE PROFILE"],
        policy_adjustments=["API_KEY_AND_PROMPT"],
        eligibility_statement="PRIVATE PAGE",
    )
    with caplog.at_level(logging.INFO, logger=triage_policy.__name__):
        checked = apply_triage_policy(
            raw, analysis=analysis(), match=_match(1, score=92, strength="VERY_STRONG")
        )
    assert "policy_adjustment=eligibility_unknown_readiness_cap" in caplog.text
    assert "PRIVATE" not in caplog.text and "API_KEY" not in caplog.text
    assert checked.policy_adjustments == [
        "eligibility_unknown_readiness_cap",
        "eligibility_unknown_evidence",
        "mandatory_experience_unconfirmed",
    ]
    assert checked.eligibility_statement == UNKNOWN_APPLICANT


def test_awf_eu_regression_persists_only_policy_result_and_reuses_cache(
    monkeypatch, isolated_session_factory, client
):
    _configure(monkeypatch, isolated_session_factory)
    a = analysis(eligibility_requirements=["Relevant qualifications required"])
    oid, companies = _seed(isolated_session_factory, analysis=a)
    raw = proposed(bid_readiness="MOSTLY_READY", eligibility_evidence="CONFIRMED")
    matcher = Mock(return_value=_match(companies[0], score=92, strength="VERY_STRONG"))
    analyzer = Mock(return_value=raw)
    first = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )[0]
    second = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )[0]
    assert first.status == "TRIAGED" and first.error_category is None
    assert second.status == "CACHED"
    assert first.result == second.result
    with isolated_session_factory() as db:
        m = db.scalar(select(CompanyOpportunityMatch))
        t = db.scalar(select(CompanyOpportunityTriage))
        assert (
            m.score == 92
            and m.qualifies_for_feed
            and m.applicant_eligibility == "UNKNOWN"
        )
        assert t.result_data["bid_readiness"] == "NEEDS_WORK"
        assert t.result_data["eligibility_evidence"] == "UNKNOWN"
        assert t.error_reason is None and t.result_data != raw.model_dump(mode="json")
    assert raw.bid_readiness.value == "MOSTLY_READY"
    matcher.assert_called_once()
    analyzer.assert_called_once()
    for url in [
        f"/?company_id={companies[0]}",
        f"/opportunities/{oid}?company_id={companies[0]}",
    ]:
        response = client.get(url)
        assert response.status_code == 200
        assert "Applicant eligibility: UNKNOWN" in response.text
        assert UNKNOWN_APPLICANT in response.text


def test_tui_two_companies_conflict_skips_triage(monkeypatch, isolated_session_factory):
    _configure(monkeypatch, isolated_session_factory)
    a = analysis(
        bidder_eligibility={
            "status": "RESTRICTED",
            "allowed_applicant_types": ["INDIVIDUAL"],
            "evidence": ["Individual consultants only"],
        }
    )
    oid, companies = _seed(
        isolated_session_factory,
        analysis=a,
        companies=[{"name": "Company A"}, {"name": "Company B"}],
    )
    matcher = Mock(
        side_effect=lambda **kw: _match(
            kw["company_id"],
            score=0,
            strength="NOT_A_MATCH",
            concerns=["Individuals only"],
        )
    )
    analyzer = Mock(side_effect=AssertionError("Conflict must not invoke AI triage"))
    outcomes = triage_engine.triage_verified_opportunity(
        oid, matcher=matcher, analyzer=analyzer
    )
    assert [o.status for o in outcomes] == ["SKIPPED", "SKIPPED"]
    with isolated_session_factory() as db:
        rows = list(db.scalars(select(CompanyOpportunityMatch)))
        assert len(rows) == 2 and all(
            r.applicant_eligibility == "CONFLICT" and not r.qualifies_for_feed
            for r in rows
        )
        assert db.scalar(select(CompanyOpportunityTriage)) is None
    analyzer.assert_not_called()


@pytest.mark.parametrize(
    "overrides,field,code",
    [
        (
            {"geographic_scope": "MULTI_COUNTRY", "delivery_countries": []},
            "delivery_countries",
            "country_scope_without_delivery",
        ),
        (
            {
                "bidder_eligibility": {
                    "status": "RESTRICTED",
                    "evidence": ["PRIVATE PAGE"],
                }
            },
            "bidder_eligibility.allowed_applicant_types",
            "restricted_applicant_types_required",
        ),
        (
            {"bidder_eligibility": {"status": "UNRESTRICTED"}},
            "bidder_eligibility.evidence",
            "applicant_evidence_required",
        ),
        (
            {"bidder_eligibility": {"status": "UNSPECIFIED", "evidence": ["PRIVATE PAGE"]}},
            "bidder_eligibility.evidence",
            "unspecified_applicant_evidence",
        ),
    ],
)
def test_domain_schema_errors_have_specific_safe_constraints(overrides, field, code):
    with pytest.raises(ValidationError) as caught:
        analysis(**overrides)
    details = validation_details(caught.value, OpportunityAnalysis)
    assert details[0]["field"] == field and details[0]["error"] == code
    assert "PRIVATE" not in json.dumps(details)
    assert details[0]["reason"] != "value failed schema validation"
